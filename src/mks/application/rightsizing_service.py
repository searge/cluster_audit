"""Rightsizing service: node-pool cost vs real utilization -> recommendations.

Cost comes from the editable price reference (``config/ovh_prices.toml``).
Utilization comes from ``kubectl top nodes`` (metrics-server / rancher-monitoring)
and is best-effort: if metrics are unreachable the report is cost-only.

When metrics are present, the report also suggests a cheaper flavor that still
fits the observed RAM/CPU (with headroom), priced from the live OVH catalog.
Suggestions ignore disk, flavor-family and scheduling constraints — treat as a
starting point, not an automated change.
"""

from dataclasses import dataclass
from pathlib import Path

from mks.application._ovh_session import (
    ovh_session,
    require_project_id,
    resolve_kube_id,
)
from mks.application._step_report import banner, info, ok, warn
from mks.application.use_case_utils import write_csv
from mks.config import OvhConfig, Prices
from mks.infrastructure.kubectl_client import KubectlError, kubectl_text
from mks.infrastructure.ovh_client import FlavorSpec, KubeNode, NodePool

HOURS_PER_MONTH = 730
# Below this average utilization a pool is a scale-down candidate.
UNDERUTILIZED_PCT = 50.0
# Spare capacity kept when sizing a replacement flavor.
HEADROOM = 1.3
# Minimum per-node saving (fraction) before a cheaper flavor is suggested.
MIN_SAVING_FRACTION = 0.10


@dataclass(frozen=True)
class _Market:
    """Flavor shapes + live catalog prices used to size replacements."""

    specs: dict[str, FlavorSpec]
    catalog: Prices


@dataclass(frozen=True)
class _PoolRow:
    """Precomputed analysis for one node pool."""

    name: str
    flavor: str
    nodes: int
    monthly_cost: float | None
    avg: tuple[float, float] | None
    recommendation: str


def _pool_monthly_cost(pool: NodePool, prices: Prices) -> float | None:
    """Estimate a pool's monthly cost from the price reference, or None if unknown."""
    return _node_monthly_price(
        pool.flavor, pool.monthly_billed, prices, pool.current_nodes
    )


def _node_monthly_price(
    flavor: str, monthly_billed: bool, prices: Prices, nodes: int = 1
) -> float | None:
    """Monthly price of ``nodes`` of ``flavor`` per the given price table."""
    price = prices.flavors.get(flavor)
    if price is None:
        return None
    if monthly_billed and price.monthly_eur is not None:
        return price.monthly_eur * nodes
    if price.hourly_eur is not None:
        return price.hourly_eur * HOURS_PER_MONTH * nodes
    return None


def _node_utilization() -> dict[str, tuple[float, float]]:
    """Return ``{node_name: (cpu_pct, mem_pct)}`` via ``kubectl top nodes``.

    Best-effort: returns an empty mapping if metrics-server is unreachable.
    """
    try:
        raw = kubectl_text("top nodes --no-headers")
    except KubectlError:
        return {}
    usage: dict[str, tuple[float, float]] = {}
    for line in raw.splitlines():
        parts = line.split()
        # NAME  CPU(cores)  CPU%  MEMORY(bytes)  MEMORY%
        if len(parts) >= 5 and parts[2].endswith("%") and parts[4].endswith("%"):
            usage[parts[0]] = (float(parts[2].rstrip("%")), float(parts[4].rstrip("%")))
    return usage


def _pool_avg_util(
    pool: NodePool, nodes: list[KubeNode], util: dict[str, tuple[float, float]]
) -> tuple[float, float] | None:
    """Average (cpu%, mem%) across a pool's nodes, or None if no metrics."""
    samples = [
        util[n.name] for n in nodes if n.node_pool_id == pool.id and n.name in util
    ]
    if not samples:
        return None
    cpu = sum(s[0] for s in samples) / len(samples)
    mem = sum(s[1] for s in samples) / len(samples)
    return cpu, mem


def _suggest_flavor(
    pool: NodePool, avg: tuple[float, float], market: _Market
) -> tuple[str, float] | None:
    """Cheapest flavor fitting observed usage; returns (name, pool_monthly_saving)."""
    cur_spec = market.specs.get(pool.flavor)
    cur_price = _node_monthly_price(pool.flavor, pool.monthly_billed, market.catalog)
    if cur_spec is None or not cur_price:
        return None
    cpu, mem = avg
    need_ram = (mem / 100) * cur_spec.ram_gb * HEADROOM
    need_vcpu = max(1.0, (cpu / 100) * cur_spec.vcpus * HEADROOM)
    best_name: str | None = None
    best_price = cur_price
    for name, spec in market.specs.items():
        if name == pool.flavor or spec.ram_gb < need_ram or spec.vcpus < need_vcpu:
            continue
        price = _node_monthly_price(name, pool.monthly_billed, market.catalog)
        if price is None or price >= best_price:
            continue
        best_name, best_price = name, price
    if best_name is None or (cur_price - best_price) / cur_price < MIN_SAVING_FRACTION:
        return None
    return best_name, (cur_price - best_price) * pool.current_nodes


def _recommendation(
    pool: NodePool, avg: tuple[float, float] | None, market: _Market
) -> str:
    """Derive a rightsizing recommendation for a pool."""
    if avg is None:
        return "no metrics (install metrics-server / rancher-monitoring)"
    cpu, mem = avg
    suggestion = _suggest_flavor(pool, avg, market)
    if suggestion is not None:
        name, saving = suggestion
        return (
            f"{cpu:.0f}% cpu / {mem:.0f}% mem: try {name} (~save {saving:.0f} EUR/mo)"
        )
    if max(cpu, mem) < UNDERUTILIZED_PCT and pool.min_nodes < pool.current_nodes:
        return f"underutilized ({cpu:.0f}% cpu / {mem:.0f}% mem): consider scale-down"
    return "ok"


def _analyze(
    pools: list[NodePool],
    nodes: list[KubeNode],
    util: dict[str, tuple[float, float]],
    prices: Prices,
    market: _Market,
) -> list[_PoolRow]:
    """Build one analysis row per pool."""
    rows: list[_PoolRow] = []
    for pool in pools:
        avg = _pool_avg_util(pool, nodes, util)
        rows.append(
            _PoolRow(
                name=pool.name,
                flavor=pool.flavor,
                nodes=pool.current_nodes,
                monthly_cost=_pool_monthly_cost(pool, prices),
                avg=avg,
                recommendation=_recommendation(pool, avg, market),
            )
        )
    return rows


def _write_csv(data_dir: str, rows: list[_PoolRow]) -> Path:
    header = [
        "nodePool",
        "flavor",
        "nodes",
        "monthlyCostEUR",
        "cpuPct",
        "memPct",
        "recommendation",
    ]
    table = [
        [
            row.name,
            row.flavor,
            row.nodes,
            f"{row.monthly_cost:.2f}" if row.monthly_cost is not None else "",
            f"{row.avg[0]:.0f}" if row.avg else "",
            f"{row.avg[1]:.0f}" if row.avg else "",
            row.recommendation,
        ]
        for row in rows
    ]
    return write_csv(Path(data_dir) / "rightsizing.csv", header, table)


def _print_analysis(rows: list[_PoolRow]) -> None:
    """Print per-pool cost and recommendation (STEP 4)."""
    banner(4, "Analyze")
    total = 0.0
    for row in rows:
        total += row.monthly_cost or 0.0
        cost_text = (
            f"{row.monthly_cost:8.2f} EUR/mo"
            if row.monthly_cost is not None
            else "   (no price)"
        )
        info(f"{row.name:<28}{cost_text}  -> {row.recommendation}")
    ok(f"estimated provisioned cost: {total:.2f} EUR/mo")


def execute_rightsizing(
    *,
    data_dir: str,
    ovh_config: OvhConfig,
    prices: Prices,
    kube_id: str | None = None,
) -> str:
    """Report per-pool cost and utilization with rightsizing hints.

    Returns the CSV path. Raises ``OvhApiError`` on API failure.
    """
    project_id = require_project_id(ovh_config)
    with ovh_session(ovh_config) as client:
        target_kube = resolve_kube_id(client, project_id, kube_id)
        banner(2, "Fetch node pools, nodes and flavor catalog")
        cluster = client.get_kube(project_id, target_kube)
        pools = client.list_nodepools(project_id, target_kube)
        nodes = client.list_nodes(project_id, target_kube)
        specs = {f.name: f for f in client.list_flavors(project_id, cluster.region)}
        market = _Market(specs=specs, catalog=client.get_catalog_prices())
        ok(f"{len(pools)} node pools, {len(nodes)} nodes, {len(specs)} flavors priced")

    banner(3, "Read node utilization (metrics-server)")
    util = _node_utilization()
    if util:
        ok(f"utilization for {len(util)} nodes")
    else:
        warn("no metrics available; report is cost-only")

    rows = _analyze(pools, nodes, util, prices, market)
    _print_analysis(rows)

    banner(5, "Write CSV")
    out_path = _write_csv(data_dir, rows)
    ok(f"wrote {out_path}")
    return str(out_path)


__all__ = ["execute_rightsizing"]
