"""Rightsizing service: node-pool cost vs real utilization -> recommendations.

Cost comes from the editable price reference (``config/ovh_prices.toml``).
Utilization comes from ``kubectl top nodes`` (metrics-server / rancher-monitoring)
and is best-effort: if metrics are unreachable the report is cost-only.
"""

import csv
from pathlib import Path

from mks.application._ovh_session import ovh_session, resolve_kube_id
from mks.application._step_report import banner, info, ok, warn
from mks.config import OvhConfig, Prices
from mks.infrastructure.kubectl_client import KubectlError, kubectl_text
from mks.infrastructure.ovh_client import DEFAULT_MKS_PROJECT_ID, KubeNode, NodePool

HOURS_PER_MONTH = 730
# Below this average utilization a monthly-billed pool is a scale-down candidate.
UNDERUTILIZED_PCT = 50.0


def _pool_monthly_cost(pool: NodePool, prices: Prices) -> float | None:
    """Estimate a pool's monthly cost from the price reference, or None if unknown."""
    price = prices.flavors.get(pool.flavor)
    if price is None:
        return None
    if pool.monthly_billed and price.monthly_eur is not None:
        return price.monthly_eur * pool.current_nodes
    if price.hourly_eur is not None:
        return price.hourly_eur * HOURS_PER_MONTH * pool.current_nodes
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


def _recommendation(pool: NodePool, avg: tuple[float, float] | None) -> str:
    """Derive a rightsizing recommendation for a pool."""
    if avg is None:
        return "no metrics (install metrics-server / rancher-monitoring)"
    cpu, mem = avg
    if max(cpu, mem) < UNDERUTILIZED_PCT and pool.min_nodes < pool.current_nodes:
        return f"underutilized ({cpu:.0f}% cpu / {mem:.0f}% mem): consider scale-down"
    return "ok"


def _write_csv(
    data_dir: str,
    pools: list[NodePool],
    nodes: list[KubeNode],
    util: dict[str, tuple[float, float]],
    prices: Prices,
) -> Path:
    out_path = Path(data_dir) / "rightsizing.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "nodePool",
                "flavor",
                "nodes",
                "monthlyCostEUR",
                "cpuPct",
                "memPct",
                "recommendation",
            ]
        )
        for pool in pools:
            cost = _pool_monthly_cost(pool, prices)
            avg = _pool_avg_util(pool, nodes, util)
            writer.writerow(
                [
                    pool.name,
                    pool.flavor,
                    pool.current_nodes,
                    f"{cost:.2f}" if cost is not None else "",
                    f"{avg[0]:.0f}" if avg else "",
                    f"{avg[1]:.0f}" if avg else "",
                    _recommendation(pool, avg),
                ]
            )
    return out_path


def _print_analysis(
    pools: list[NodePool],
    nodes: list[KubeNode],
    util: dict[str, tuple[float, float]],
    prices: Prices,
) -> None:
    """Print per-pool cost and recommendation (STEP 4)."""
    banner(4, "Analyze")
    total = 0.0
    for pool in pools:
        cost = _pool_monthly_cost(pool, prices)
        total += cost or 0.0
        cost_text = f"{cost:8.2f} EUR/mo" if cost is not None else "   (no price)"
        recommendation = _recommendation(pool, _pool_avg_util(pool, nodes, util))
        info(f"{pool.name:<28}{cost_text}  -> {recommendation}")
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
    project_id = ovh_config.project_id or DEFAULT_MKS_PROJECT_ID
    with ovh_session(ovh_config) as client:
        target_kube = resolve_kube_id(client, project_id, kube_id)
        banner(2, "Fetch node pools and nodes")
        pools = client.list_nodepools(project_id, target_kube)
        nodes = client.list_nodes(project_id, target_kube)
        ok(f"{len(pools)} node pools, {len(nodes)} nodes")

    banner(3, "Read node utilization (metrics-server)")
    util = _node_utilization()
    if util:
        ok(f"utilization for {len(util)} nodes")
    else:
        warn("no metrics available; report is cost-only")

    _print_analysis(pools, nodes, util, prices)

    banner(5, "Write CSV")
    out_path = _write_csv(data_dir, pools, nodes, util, prices)
    ok(f"wrote {out_path}")
    return str(out_path)


__all__ = ["execute_rightsizing"]
