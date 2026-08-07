"""Capacity plan service: real demand vs requests from Prometheus.

Pulls requests vs actual peak usage (p95 CPU, max memory) over a window from
rancher-monitoring, so node-pool sizing is based on real demand, not the
inflated requests that currently drive node count. Read-only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mks.application._step_report import banner, info, ok, warn
from mks.application.use_case_utils import write_csv
from mks.infrastructure.prometheus_client import PrometheusClient

# Container filter shared by usage queries (exclude pause/empty containers).
_C = '{container!="",container!="POD"}'


@dataclass(frozen=True)
class _NsDemand:
    """Per-namespace requests vs observed peak usage."""

    namespace: str
    cpu_req: float  # cores
    cpu_p95: float  # cores
    cpu_max: float  # cores; burst signal — p95-based quota unsafe when cpu_max >> p95
    mem_req_gb: float
    mem_max_gb: float


@dataclass(frozen=True)
class _ClusterTotals:
    """Cluster-wide requests vs observed peak, plus the node count they drive."""

    nodes: float
    cpu_req: float  # cores
    cpu_p95: float  # cores
    mem_req_gb: float
    mem_max_gb: float


def build_queries(window: str) -> dict[str, str]:
    """Return the PromQL used, keyed by purpose (also handy for manual runs).

    The ``*_p95`` queries use a PromQL subquery: ``<instant-expr>[window:step]``
    turns the inner aggregation into a range vector that ``quantile_over_time``
    then reduces. The ``[5m]`` inside ``rate`` is the rate window; the
    ``[window:5m]`` after the ``sum`` is the subquery range — both are intended.
    """
    req = "sum by (namespace) (kube_pod_container_resource_requests"
    return {
        "cpu_req_by_ns": f'{req}{{resource="cpu"}})',
        "cpu_p95_by_ns": (
            f"quantile_over_time(0.95, sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "cpu_max_by_ns": (
            f"max_over_time(sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "mem_req_by_ns": f'{req}{{resource="memory"}})',
        "mem_max_by_ns": (
            f"max_over_time(sum by (namespace) "
            f"(container_memory_working_set_bytes{_C})[{window}:30m])"
        ),
        "cpu_req_total": 'sum(kube_pod_container_resource_requests{resource="cpu"})',
        "cpu_p95_total": (
            f"quantile_over_time(0.95, sum"
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "mem_req_total": 'sum(kube_pod_container_resource_requests{resource="memory"})',
        "mem_max_total": (
            f"max_over_time(sum(container_memory_working_set_bytes{_C})[{window}:30m])"
        ),
        "mem_spikers": (
            f"topk(15, max_over_time(sum by (namespace, pod) "
            f"(container_memory_working_set_bytes{_C})[{window}:30m]))"
        ),
        "node_count": "count(kube_node_info)",
    }


def _by_ns(client: PrometheusClient, query: str) -> dict[str, float]:
    return {
        labels.get("namespace", "?"): value for labels, value in client.instant(query)
    }


def _collect_namespaces(
    client: PrometheusClient, queries: dict[str, str]
) -> list[_NsDemand]:
    cpu_req = _by_ns(client, queries["cpu_req_by_ns"])
    cpu_p95 = _by_ns(client, queries["cpu_p95_by_ns"])
    cpu_max = _by_ns(client, queries["cpu_max_by_ns"])
    mem_req = _by_ns(client, queries["mem_req_by_ns"])
    mem_max = _by_ns(client, queries["mem_max_by_ns"])
    names = cpu_req.keys() | cpu_p95.keys() | mem_req.keys() | mem_max.keys()
    rows = [
        _NsDemand(
            namespace=ns,
            cpu_req=cpu_req.get(ns, 0.0),
            cpu_p95=cpu_p95.get(ns, 0.0),
            cpu_max=cpu_max.get(ns, 0.0),
            mem_req_gb=mem_req.get(ns, 0.0) / 1024**3,
            mem_max_gb=mem_max.get(ns, 0.0) / 1024**3,
        )
        for ns in names
    ]
    return sorted(rows, key=lambda r: r.cpu_req - r.cpu_p95, reverse=True)


def _write_namespaces_csv(data_dir: str, rows: list[_NsDemand]) -> Path:
    header = [
        "namespace",
        "cpuReqCores",
        "cpuP95Cores",
        "cpuMaxCores",
        "memReqGB",
        "memMaxGB",
    ]
    table = [
        [
            r.namespace,
            f"{r.cpu_req:.2f}",
            f"{r.cpu_p95:.2f}",
            f"{r.cpu_max:.2f}",
            f"{r.mem_req_gb:.2f}",
            f"{r.mem_max_gb:.2f}",
        ]
        for r in rows
    ]
    return write_csv(Path(data_dir) / "namespace_demand.csv", header, table)


def _write_spikers_csv(data_dir: str, client: PrometheusClient, query: str) -> None:
    rows = [
        [labels.get("namespace", "?"), labels.get("pod", "?"), f"{value / 1024**3:.2f}"]
        for labels, value in client.instant(query)
    ]
    rows.sort(key=lambda r: float(r[2]), reverse=True)
    write_csv(
        Path(data_dir) / "mem_spikers.csv", ["namespace", "pod", "memMaxGB"], rows
    )


def _collect_cluster(
    client: PrometheusClient, queries: dict[str, str]
) -> _ClusterTotals:
    return _ClusterTotals(
        nodes=client.scalar(queries["node_count"]) or 0.0,
        cpu_req=client.scalar(queries["cpu_req_total"]) or 0.0,
        cpu_p95=client.scalar(queries["cpu_p95_total"]) or 0.0,
        mem_req_gb=(client.scalar(queries["mem_req_total"]) or 0.0) / 1024**3,
        mem_max_gb=(client.scalar(queries["mem_max_total"]) or 0.0) / 1024**3,
    )


def _print_cluster(totals: _ClusterTotals) -> None:
    """Print cluster-level requests vs real demand (STEP 3)."""
    banner(3, "Cluster demand vs requests")
    info(f"NODES {totals.nodes:.0f}")
    info(
        f"CPU  requested {totals.cpu_req:6.1f} cores "
        f"| p95 used {totals.cpu_p95:6.1f} cores"
    )
    info(
        f"     reclaimable ≈ {totals.cpu_req - totals.cpu_p95:.1f} cores (over-request)"
    )
    info(
        f"MEM  requested {totals.mem_req_gb:6.1f} GB    "
        f"| max used {totals.mem_max_gb:6.1f} GB"
    )
    ok(
        f"right-sized target ≈ {totals.cpu_p95:.0f} cores / "
        f"{totals.mem_max_gb:.0f} GB (before headroom)"
    )


def _write_cluster_totals_csv(
    data_dir: str, totals: _ClusterTotals, window: str
) -> None:
    """One-row trend record. Concatenating runs is what shows drift over months."""
    header = [
        "date",
        "window",
        "nodes",
        "cpuReqCores",
        "cpuP95Cores",
        "memReqGB",
        "memMaxGB",
    ]
    row = [
        datetime.now(UTC).strftime("%Y-%m-%d"),
        window,
        f"{totals.nodes:.0f}",
        f"{totals.cpu_req:.2f}",
        f"{totals.cpu_p95:.2f}",
        f"{totals.mem_req_gb:.2f}",
        f"{totals.mem_max_gb:.2f}",
    ]
    write_csv(Path(data_dir) / "cluster_totals.csv", header, [row])


def execute_capacity_plan(
    *,
    data_dir: str,
    prometheus_url: str,
    window: str = "14d",
    verify_tls: bool = True,
) -> str:
    """Query Prometheus for demand vs requests and write CSVs. Returns the path."""
    banner(1, "Connect to Prometheus")
    client = PrometheusClient(
        prometheus_url, verify_tls=verify_tls, timeout_seconds=120.0
    )
    queries = build_queries(window)
    if client.scalar("vector(1)") is None:
        warn("Prometheus reachable but returned no data for a trivial query")
    ok(f"querying {prometheus_url} over window {window}")

    banner(2, "Per-namespace demand")
    rows = _collect_namespaces(client, queries)
    ok(f"{len(rows)} namespaces with metrics")
    out_path = _write_namespaces_csv(data_dir, rows)
    _write_spikers_csv(data_dir, client, queries["mem_spikers"])

    totals = _collect_cluster(client, queries)
    _print_cluster(totals)
    _write_cluster_totals_csv(data_dir, totals, window)

    banner(4, "Write CSV")
    ok(f"wrote {out_path} (+ mem_spikers.csv, cluster_totals.csv)")
    return str(out_path)


__all__ = ["build_queries", "execute_capacity_plan"]
