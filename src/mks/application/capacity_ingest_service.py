"""Capacity ingest: the same Prometheus demand data, into Postgres.

`capacity-plan` answers "what does the cluster look like now" as CSV.
This answers "what has it been doing for months" — Prometheus only keeps 15
days, so trend evidence has to be accumulated in a durable store. Grafana reads
the same tables directly as a Postgres datasource, which is why the views here
are shaped for charting rather than for the CLI.

Reuses the collectors from `capacity_plan_service` on purpose: the two
capabilities must never disagree about what a phantom core is.
"""

from datetime import UTC, datetime

from mks.application._step_report import banner, info, ok
from mks.application.capacity_plan_service import (
    build_queries,
    collect_cluster,
    collect_namespaces,
    collect_spikers,
)
from mks.domain.capacity import CapacitySnapshot
from mks.infrastructure.postgres_client import PostgresClient
from mks.infrastructure.prometheus_client import PrometheusClient

# Idempotent: safe to run on every ingest, which keeps deploys to one step.
# Numeric, not float — these numbers end up in cost arguments and money
# discussions do not survive "why does it say 62.00000000001".
SCHEMA_DDL = """
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

-- Grafana reads these. cpu_phantom_cores is the headline number: reserved but
-- never used. mem_shortfall_gb is its mirror — used more than reserved, which
-- is what causes MemoryPressure evictions rather than cost.
CREATE OR REPLACE VIEW namespace_trend AS
SELECT s.taken_at,
       s.cluster,
       s.window_spec,
       d.namespace,
       d.cpu_req_cores,
       d.cpu_p95_cores,
       d.cpu_max_cores,
       d.cpu_req_cores - d.cpu_p95_cores AS cpu_phantom_cores,
       d.mem_req_gb,
       d.mem_max_gb,
       d.mem_max_gb - d.mem_req_gb       AS mem_shortfall_gb
FROM namespace_demand d
JOIN capacity_snapshot s ON s.id = d.snapshot_id;

CREATE OR REPLACE VIEW cluster_trend AS
SELECT taken_at,
       cluster,
       window_spec,
       nodes,
       cpu_req_cores,
       cpu_p95_cores,
       cpu_req_cores - cpu_p95_cores AS cpu_phantom_cores,
       mem_req_gb,
       mem_max_gb
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
     cpu_req_cores, cpu_p95_cores, mem_req_gb, mem_max_gb)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (cluster, taken_at, window_spec) DO UPDATE SET
    nodes = EXCLUDED.nodes,
    cpu_req_cores = EXCLUDED.cpu_req_cores,
    cpu_p95_cores = EXCLUDED.cpu_p95_cores,
    mem_req_gb = EXCLUDED.mem_req_gb,
    mem_max_gb = EXCLUDED.mem_max_gb
RETURNING id
"""

_INSERT_NS = """
INSERT INTO namespace_demand
    (snapshot_id, namespace, cpu_req_cores, cpu_p95_cores,
     cpu_max_cores, mem_req_gb, mem_max_gb)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id, namespace) DO UPDATE SET
    cpu_req_cores = EXCLUDED.cpu_req_cores,
    cpu_p95_cores = EXCLUDED.cpu_p95_cores,
    cpu_max_cores = EXCLUDED.cpu_max_cores,
    mem_req_gb = EXCLUDED.mem_req_gb,
    mem_max_gb = EXCLUDED.mem_max_gb
"""

_INSERT_SPIKER = """
INSERT INTO mem_spiker (snapshot_id, namespace, pod, mem_max_gb)
VALUES (%s, %s, %s, %s)
ON CONFLICT (snapshot_id, namespace, pod) DO UPDATE SET
    mem_max_gb = EXCLUDED.mem_max_gb
"""


def _store(db: PostgresClient, snapshot: CapacitySnapshot) -> int:
    """Write one snapshot and its rows; returns the snapshot id."""
    totals = snapshot.totals
    snapshot_id = db.insert_returning_id(
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
        ),
    )
    db.insert_many(
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
            )
            for r in snapshot.namespaces
        ],
    )
    db.insert_many(
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

    banner(3, "Collect")
    queries = build_queries(window)
    namespaces = collect_namespaces(prom, queries)
    spikers = collect_spikers(prom, queries["mem_spikers"])
    totals = collect_cluster(prom, queries)
    ok(f"{len(namespaces)} namespaces, {len(spikers)} memory spikers")
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
        ),
    )
    history = db.scalar("SELECT count(*) FROM capacity_snapshot")
    ok(f"snapshot #{snapshot_id} stored; {history} snapshots in history")
    return snapshot_id


__all__ = ["SCHEMA_DDL", "execute_capacity_ingest"]
