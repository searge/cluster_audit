"""Capacity ingest: the same Prometheus demand data, into Postgres.

`capacity-plan` answers "what does the cluster look like now" as CSV.
This answers "what has it been doing for months" — Prometheus only keeps 15
days, so trend evidence has to be accumulated in a durable store. Grafana reads
the same tables directly as a Postgres datasource, which is why the views here
are shaped for charting rather than for the CLI.

Reuses the collectors from `capacity_plan_service` on purpose: the two
capabilities must never disagree about what a phantom core is.
"""

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from mks.application._step_report import banner, info, ok, warn
from mks.application.capacity_plan_service import (
    build_queries,
    collect_cluster,
    collect_namespaces,
    collect_spikers,
)
from mks.config import load_config, load_prices
from mks.domain.capacity import CapacitySnapshot, ClusterTotals, NsDemand
from mks.domain.cost import (
    CostBasis,
    NodeFlavour,
    build_cost_basis,
    flavour_of,
    pool_flavours,
)
from mks.infrastructure.kube_client import KubeApiError, KubeClient
from mks.infrastructure.postgres_client import PostgresClient, PostgresTransaction
from mks.infrastructure.prometheus_client import PrometheusClient
from mks.infrastructure.rancher_client import (
    RancherApiError,
    RancherAuth,
    RancherClient,
)

# Idempotent: safe to run on every ingest, which keeps deploys to one step.
# Numeric, not float — these numbers end up in cost arguments and money
# discussions do not survive "why does it say 62.00000000001".
SCHEMA_DDL = """
-- ALTER TABLE takes ACCESS EXCLUSIVE before it evaluates IF NOT EXISTS, so even
-- the steady-state run asks for the strongest lock. A pending exclusive request
-- queues ahead of every new reader, so one slow Grafana query would otherwise
-- freeze all the panels until this finishes, and a session left idle in
-- transaction would hold the ingest until the Job deadline kills it and the
-- week's snapshot is lost. Failing fast and retrying next week is the better of
-- the two bad outcomes.
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS capacity_snapshot (
    id            bigserial PRIMARY KEY,
    cluster       text        NOT NULL,
    taken_at      timestamptz NOT NULL,
    window_spec   text        NOT NULL,
    nodes         integer     NOT NULL,
    cpu_req_cores numeric(12,3) NOT NULL,
    cpu_p95_cores numeric(12,3) NOT NULL,
    mem_req_gb    numeric(12,3) NOT NULL,
    mem_max_gb    numeric(12,3) NOT NULL,
    UNIQUE (cluster, taken_at, window_spec)
);

CREATE TABLE IF NOT EXISTS namespace_demand (
    snapshot_id   bigint NOT NULL
                  REFERENCES capacity_snapshot(id) ON DELETE CASCADE,
    namespace     text   NOT NULL,
    cpu_req_cores numeric(12,3) NOT NULL,
    cpu_p95_cores numeric(12,3) NOT NULL,
    cpu_max_cores numeric(12,3) NOT NULL,
    mem_req_gb    numeric(12,3) NOT NULL,
    mem_max_gb    numeric(12,3) NOT NULL,
    PRIMARY KEY (snapshot_id, namespace)
);

CREATE TABLE IF NOT EXISTS mem_spiker (
    snapshot_id bigint NOT NULL
                REFERENCES capacity_snapshot(id) ON DELETE CASCADE,
    namespace   text   NOT NULL,
    pod         text   NOT NULL,
    mem_max_gb  numeric(12,3) NOT NULL,
    PRIMARY KEY (snapshot_id, namespace, pod)
);

CREATE INDEX IF NOT EXISTS namespace_demand_ns_idx
    ON namespace_demand (namespace);

-- Added after the first deployments, so they arrive as ALTERs rather than in
-- the CREATE above: an existing table is never recreated by this script.
ALTER TABLE capacity_snapshot
    ADD COLUMN IF NOT EXISTS eur_per_core_month numeric(12,4),
    ADD COLUMN IF NOT EXISTS node_bill_eur_month numeric(12,2),
    ADD COLUMN IF NOT EXISTS priced_nodes integer,
    ADD COLUMN IF NOT EXISTS unpriced_nodes integer,
    ADD COLUMN IF NOT EXISTS binding_resource text;

ALTER TABLE namespace_demand
    ADD COLUMN IF NOT EXISTS project_id text;

-- Rancher project names. A downstream cluster only knows the opaque id, and the
-- Project objects live in the Rancher management cluster, so this is filled in
-- by whoever runs the ingest with Rancher credentials configured. The scheduled
-- run in-cluster has none and leaves the table alone, which is why the join
-- below is an outer one: an unnamed project still reports its numbers.
CREATE TABLE IF NOT EXISTS project_name (
    project_id text PRIMARY KEY,
    name       text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Grafana reads these. cpu_phantom_cores is the headline number: reserved but
-- never used. mem_shortfall_gb is its mirror — used more than reserved, which
-- is what causes MemoryPressure evictions rather than cost.
--
-- Dropped and recreated rather than CREATE OR REPLACE: replacing a view can
-- only append columns, so adding one in the middle fails with "cannot change
-- name of view column". Recreating keeps the shape of these free to change.
-- Both statements are in the same transaction as everything else here, so a
-- reader never sees the view missing.
-- floor(), not round(): a figure someone will dispute should only ever be
-- revisable upwards. NULL when the snapshot could not be priced.
DROP VIEW IF EXISTS namespace_trend;
CREATE VIEW namespace_trend AS
SELECT s.taken_at,
       s.cluster,
       s.window_spec,
       d.namespace,
       d.project_id,
       coalesce(n.name, d.project_id) AS project,
       floor(d.cpu_req_cores * s.eur_per_core_month) AS reserved_eur_month,
       floor((d.cpu_req_cores - d.cpu_p95_cores) * s.eur_per_core_month)
           AS wasted_eur_month,
       d.cpu_req_cores,
       d.cpu_p95_cores,
       d.cpu_max_cores,
       d.cpu_req_cores - d.cpu_p95_cores AS cpu_phantom_cores,
       d.mem_req_gb,
       d.mem_max_gb,
       d.mem_max_gb - d.mem_req_gb       AS mem_shortfall_gb
FROM namespace_demand d
JOIN capacity_snapshot s ON s.id = d.snapshot_id
LEFT JOIN project_name n ON n.project_id = d.project_id;

-- One row per Rancher project, per snapshot. Grouping by snapshot id rather
-- than by timestamp matters: capacity_snapshot is unique on
-- (cluster, taken_at, window_spec) and taken_at is truncated to the hour, so an
-- ad-hoc run with a different --window in the same hour creates a second
-- snapshot. Grouping on the timestamp alone would silently add the two together
-- and double every figure in the panel, with no column to notice it by.
--
-- Namespaces Rancher does not manage collapse into a single "(no project)"
-- bucket rather than being dropped, so the totals still add up to the cluster.
DROP VIEW IF EXISTS project_trend;
CREATE VIEW project_trend AS
SELECT s.id                                        AS snapshot_id,
       s.taken_at,
       s.cluster,
       s.window_spec,
       coalesce(d.project_id, '(no project)')      AS project_id,
       coalesce(n.name, d.project_id, '(no project)') AS project,
       count(*)                                    AS namespaces,
       sum(d.cpu_req_cores)                  AS cpu_req_cores,
       sum(d.cpu_p95_cores)                  AS cpu_p95_cores,
       sum(d.cpu_req_cores - d.cpu_p95_cores) AS cpu_phantom_cores,
       sum(d.mem_req_gb)                     AS mem_req_gb,
       sum(d.mem_max_gb)                     AS mem_max_gb,
       floor(sum(d.cpu_req_cores - d.cpu_p95_cores) * min(s.eur_per_core_month))
           AS wasted_eur_month
FROM namespace_demand d
JOIN capacity_snapshot s ON s.id = d.snapshot_id
LEFT JOIN project_name n ON n.project_id = d.project_id
GROUP BY s.id, s.taken_at, s.cluster, s.window_spec,
         coalesce(d.project_id, '(no project)'),
         coalesce(n.name, d.project_id, '(no project)');

DROP VIEW IF EXISTS cluster_trend;
CREATE VIEW cluster_trend AS
SELECT taken_at,
       cluster,
       window_spec,
       nodes,
       cpu_req_cores,
       cpu_p95_cores,
       cpu_req_cores - cpu_p95_cores AS cpu_phantom_cores,
       mem_req_gb,
       mem_max_gb,
       eur_per_core_month,
       node_bill_eur_month,
       priced_nodes,
       unpriced_nodes,
       binding_resource,
       floor((cpu_req_cores - cpu_p95_cores) * eur_per_core_month) AS wasted_eur_month
FROM capacity_snapshot;
"""
# Deliberately no GRANT logic here. The dashboard's read-only role gets its
# privileges from PostgreSQL's predefined `pg_read_all_data`, granted by CNPG
# at role creation (`managed.roles[].inRoles` in the cluster manifest), so
# authorization never depends on this script having run.
#
# The tempting alternative — `GRANT SELECT ON ALL TABLES` from here — was built
# and then removed. It is not merely redundant, it is dangerous: that statement
# raises on any relation in the schema owned by someone else (a
# `CREATE EXTENSION` is enough), and because `execute_script` runs this whole
# DDL in one transaction, the failure rolls back the entire ingest. A cosmetic
# privilege problem would become a lost week of data that Prometheus can no
# longer replay. It also made the role undroppable, leaving CNPG's
# `ensure: absent` stuck in Terminating.

_INSERT_SNAPSHOT = """
INSERT INTO capacity_snapshot
    (cluster, taken_at, window_spec, nodes,
     cpu_req_cores, cpu_p95_cores, mem_req_gb, mem_max_gb,
     eur_per_core_month, node_bill_eur_month,
     priced_nodes, unpriced_nodes, binding_resource)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (cluster, taken_at, window_spec) DO UPDATE SET
    nodes = EXCLUDED.nodes,
    cpu_req_cores = EXCLUDED.cpu_req_cores,
    cpu_p95_cores = EXCLUDED.cpu_p95_cores,
    mem_req_gb = EXCLUDED.mem_req_gb,
    mem_max_gb = EXCLUDED.mem_max_gb,
    eur_per_core_month = EXCLUDED.eur_per_core_month,
    node_bill_eur_month = EXCLUDED.node_bill_eur_month,
    priced_nodes = EXCLUDED.priced_nodes,
    unpriced_nodes = EXCLUDED.unpriced_nodes,
    binding_resource = EXCLUDED.binding_resource
RETURNING id
"""

_INSERT_NS = """
INSERT INTO namespace_demand
    (snapshot_id, namespace, cpu_req_cores, cpu_p95_cores,
     cpu_max_cores, mem_req_gb, mem_max_gb, project_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id, namespace) DO UPDATE SET
    project_id = EXCLUDED.project_id,
    cpu_req_cores = EXCLUDED.cpu_req_cores,
    cpu_p95_cores = EXCLUDED.cpu_p95_cores,
    cpu_max_cores = EXCLUDED.cpu_max_cores,
    mem_req_gb = EXCLUDED.mem_req_gb,
    mem_max_gb = EXCLUDED.mem_max_gb
"""

_DELETE_CHILDREN_NS = """
DELETE FROM namespace_demand WHERE snapshot_id = %s
"""

_DELETE_CHILDREN_SPIKER = """
DELETE FROM mem_spiker WHERE snapshot_id = %s
"""

_UPSERT_PROJECT_NAME = """
INSERT INTO project_name (project_id, name, updated_at)
VALUES (%s, %s, now())
ON CONFLICT (project_id) DO UPDATE SET
    name = EXCLUDED.name,
    updated_at = now()
"""

_INSERT_SPIKER = """
INSERT INTO mem_spiker (snapshot_id, namespace, pod, mem_max_gb)
VALUES (%s, %s, %s, %s)
ON CONFLICT (snapshot_id, namespace, pod) DO UPDATE SET
    mem_max_gb = EXCLUDED.mem_max_gb
"""


async def _fetch_project_names(url: str, auth: RancherAuth) -> dict[str, str]:
    """Ask Rancher for every project id and its display name."""
    async with RancherClient(url, auth) as rancher:
        payload = await rancher.get("/v3/projects", params={"limit": "-1"})
    names: dict[str, str] = {}
    for project in payload.get("data", []):
        short = str(project.get("id", "")).rsplit(":", maxsplit=1)[-1]
        name = project.get("name")
        if short and name:
            names[short] = str(name)
    return names


def refresh_project_names(db: PostgresClient) -> int:
    """Store Rancher project names, when this run has credentials for them.

    Only the opaque project id is visible from inside the cluster, and the
    scheduled run deliberately carries no Rancher token, so the weekly ingest
    leaves this table untouched. Running the same command from a workstation
    that has RANCHER_URL and a token refreshes the names for everyone, and the
    dashboard picks them up on its next query. Nothing depends on it having
    happened: unnamed projects still report their numbers under their id.
    """
    config = load_config()
    if not config.has_rancher or not config.rancher_url:
        info("no Rancher credentials, leaving project names as they are")
        return 0
    auth = RancherAuth(
        token=config.rancher_token, ak=config.rancher_ak, sk=config.rancher_sk
    )
    try:
        names = asyncio.run(_fetch_project_names(config.rancher_url, auth))
    except (RancherApiError, OSError) as exc:
        warn(f"could not refresh project names: {exc}")
        return 0
    db.insert_many(_UPSERT_PROJECT_NAME, list(names.items()))
    return len(names)


def _fleet_inventory(nodes: list[dict[str, Any]]) -> list[NodeFlavour]:
    """Group the node list into priced flavours, carrying each one's cores."""
    prices = load_prices()
    if not prices.flavors:
        warn("no price reference at config/ovh_prices.toml; storing without costs")
    learned = pool_flavours(nodes, frozenset(prices.flavors))
    counts: Counter[str | None] = Counter()
    cores: dict[str | None, float] = {}
    for node in nodes:
        name = flavour_of(node, learned)
        counts[name] += 1
        cores[name] = cores.get(name, 0.0) + float(node["cpu"])
    return [
        NodeFlavour(
            name=name or "unknown",
            count=count,
            cores=cores[name],
            monthly_eur=prices.flavors[name].monthly_eur if name else None,
            hourly_eur=prices.flavors[name].hourly_eur if name else None,
        )
        for name, count in counts.items()
    ]


def collect_cluster_context(
    namespaces: list[NsDemand], totals: ClusterTotals
) -> tuple[list[NsDemand], CostBasis | None]:
    """Attach Rancher projects to the rows and work out what a core costs.

    Both answers come from the Kubernetes API because neither is in Prometheus:
    kube-state-metrics here runs without label or annotation metrics, so the
    project a namespace belongs to and the flavour a node is are both invisible
    to it.

    Degrades rather than fails. A cluster the ingest cannot reach, or one whose
    node flavours are absent from the price reference, still produces a snapshot
    with the capacity numbers intact and the euro columns left empty. Losing a
    week of trend data over a missing price would be the worse trade.
    """
    try:
        kube = KubeClient()
        nodes = kube.list_nodes()
        projects = kube.namespace_projects()
    except KubeApiError as exc:
        warn(f"Kubernetes API unavailable, no project or cost data: {exc}")
        return namespaces, None

    enriched = [
        replace(row, project_id=projects.get(row.namespace)) for row in namespaces
    ]
    flavours = _fleet_inventory(nodes)

    # Which resource is closer to full decides whether pricing by the core is
    # still the fair basis, so both ratios come from the same node listing.
    #
    # The two are not measured alike: the CPU total is a p95 of requests over
    # the window while the memory total is a peak, so the comparison is biased
    # towards memory and the real CPU dominance is wider than it looks. It is
    # recorded rather than acted on, which is the level of precision that
    # deserves.
    allocatable_cores = sum(float(node["cpu"]) for node in nodes)
    allocatable_memory = sum(float(node["memory_gb"]) for node in nodes)
    cost = build_cost_basis(
        flavours,
        totals.cpu_req / allocatable_cores if allocatable_cores else 0.0,
        totals.mem_req_gb / allocatable_memory if allocatable_memory else 0.0,
    )
    if cost is None:
        warn("no node flavour matched the price reference; storing without costs")
    return enriched, cost


def _store(db: PostgresClient, snapshot: CapacitySnapshot) -> int:
    """Write one snapshot and its rows atomically; returns the snapshot id."""
    totals = snapshot.totals
    cost = snapshot.cost
    with db.transaction() as tx:
        return _store_in(tx, snapshot, totals, cost)


def _store_in(
    tx: PostgresTransaction,
    snapshot: CapacitySnapshot,
    totals: ClusterTotals,
    cost: CostBasis | None,
) -> int:
    """Write the snapshot row and its children inside one transaction."""
    snapshot_id = tx.insert_returning_id(
        _INSERT_SNAPSHOT,
        (
            snapshot.cluster,
            snapshot.taken_at,
            snapshot.window,
            int(totals.nodes),
            totals.cpu_req,
            totals.cpu_p95,
            totals.mem_req_gb,
            totals.mem_max_gb,
            cost.eur_per_core_month if cost else None,
            cost.node_bill_eur_month if cost else None,
            cost.priced_nodes if cost else None,
            cost.unpriced_nodes if cost else None,
            cost.binding_resource if cost else None,
        ),
    )
    # An upsert alone leaves rows behind for namespaces deleted between runs and
    # for pods that dropped out of topk(15), which would then keep appearing in
    # the panels and in the project sums.
    tx.execute(_DELETE_CHILDREN_NS, (snapshot_id,))
    tx.execute(_DELETE_CHILDREN_SPIKER, (snapshot_id,))
    tx.insert_many(
        _INSERT_NS,
        [
            (
                snapshot_id,
                r.namespace,
                r.cpu_req,
                r.cpu_p95,
                r.cpu_max,
                r.mem_req_gb,
                r.mem_max_gb,
                r.project_id,
            )
            for r in snapshot.namespaces
        ],
    )
    tx.insert_many(
        _INSERT_SPIKER,
        [(snapshot_id, s.namespace, s.pod, s.mem_max_gb) for s in snapshot.spikers],
    )
    return snapshot_id


def execute_capacity_ingest(
    *,
    database_url: str,
    prometheus_url: str,
    window: str = "7d",
    cluster: str = "smile-ovh",
    verify_tls: bool = True,
) -> int:
    """Query Prometheus and persist one snapshot. Returns the snapshot id."""
    banner(1, "Connect")
    prom = PrometheusClient(
        prometheus_url, verify_tls=verify_tls, timeout_seconds=120.0
    )
    db = PostgresClient(database_url)
    ok(f"prometheus {prometheus_url} | window {window} | cluster {cluster}")

    banner(2, "Ensure schema")
    db.execute_script(SCHEMA_DDL)
    ok("tables and views up to date")
    renamed = refresh_project_names(db)
    if renamed:
        ok(f"{renamed} Rancher project names refreshed")

    banner(3, "Collect")
    queries = build_queries(window)
    namespaces = collect_namespaces(prom, queries)
    spikers = collect_spikers(prom, queries["mem_spikers"])
    totals = collect_cluster(prom, queries)
    ok(f"{len(namespaces)} namespaces, {len(spikers)} memory spikers")

    namespaces, cost = collect_cluster_context(namespaces, totals)
    if cost is not None:
        ok(
            f"{cost.priced_nodes}/{cost.priced_nodes + cost.unpriced_nodes} nodes "
            f"priced | {cost.node_bill_eur_month:.0f} EUR/month "
            f"| {cost.eur_per_core_month:.2f} EUR per core"
        )
        info(
            f"     reserved-but-unused ≈ "
            f"{cost.allocate(totals.cpu_req - totals.cpu_p95):.0f} EUR/month"
        )
    info(
        f"nodes {totals.nodes:.0f} | CPU req {totals.cpu_req:.1f} "
        f"p95 {totals.cpu_p95:.1f} (phantom {totals.cpu_req - totals.cpu_p95:.1f})"
    )

    banner(4, "Store")
    snapshot_id = _store(
        db,
        CapacitySnapshot(
            cluster=cluster,
            # Truncated to the hour so a re-run within the same hour updates the
            # same row instead of littering the trend with near-duplicate points.
            taken_at=datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
            window=window,
            totals=totals,
            namespaces=tuple(namespaces),
            spikers=tuple(spikers),
            cost=cost,
        ),
    )
    history = db.scalar("SELECT count(*) FROM capacity_snapshot")
    ok(f"snapshot #{snapshot_id} stored; {history} snapshots in history")
    return snapshot_id


__all__ = ["SCHEMA_DDL", "execute_capacity_ingest"]
