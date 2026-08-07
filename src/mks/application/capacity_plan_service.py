"""Capacity plan service: real demand vs requests from Prometheus.

Pulls requests vs actual peak usage (p95 CPU, max memory) over a window from
rancher-monitoring, so node-pool sizing is based on real demand, not the
inflated requests that currently drive node count. Read-only.
"""

from datetime import UTC, datetime
from pathlib import Path

from mks.application._step_report import banner, info, ok, warn
from mks.application.use_case_utils import write_csv
from mks.domain.capacity import ClusterTotals, MemSpiker, NsDemand
from mks.infrastructure.prometheus_client import PrometheusClient

# Container filter shared by usage queries (exclude pause/empty containers).
_C = '{container!="",container!="POD"}'


def build_queries(window: str) -> dict[str, str]:
    """Return the PromQL used, keyed by purpose (also handy for manual runs).

    Every query is windowed, requests included. An instant reading of
    ``kube_pod_container_resource_requests`` only sees pods that exist right
    now, so a namespace whose CI jobs had finished reported *zero* requested
    CPU while still carrying a week of measured usage — producing impossible
    negative waste (``gitlab-runners`` read 0.00 requested vs 16.65 used) and
    making the cluster total swing by tens of cores between two runs minutes
    apart. Windowing both sides fixes that.

    The statistic is matched per resource, deliberately. CPU compares p95 to
    p95: pairing peak reservation against typical usage would inflate the gap
    and invites the fair objection that it measures our worst moment against
    their average one. Memory compares peak to peak, because memory has to fit
    at its maximum or the pod is OOM-killed — an average would understate it.

    The subquery form is ``<instant-expr>[window:step]`` — it turns an
    aggregation into a range vector that ``max_over_time`` /
    ``quantile_over_time`` then reduce. In the CPU usage queries the ``[5m]``
    inside ``rate`` is the rate window and the ``[window:5m]`` after the ``sum``
    is the subquery range; both are intended. Memory steps at 30m because
    working-set moves slower and the finer step buys nothing but query time.
    """
    req = "sum by (namespace) (kube_pod_container_resource_requests"
    return {
        "cpu_req_by_ns": (
            f'quantile_over_time(0.95, {req}{{resource="cpu"}})[{window}:5m])'
        ),
        "cpu_p95_by_ns": (
            f"quantile_over_time(0.95, sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "cpu_max_by_ns": (
            f"max_over_time(sum by (namespace) "
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "mem_req_by_ns": (f'max_over_time({req}{{resource="memory"}})[{window}:30m])'),
        "mem_max_by_ns": (
            f"max_over_time(sum by (namespace) "
            f"(container_memory_working_set_bytes{_C})[{window}:30m])"
        ),
        "cpu_req_total": (
            f"quantile_over_time(0.95, sum(kube_pod_container_resource_requests"
            f'{{resource="cpu"}})[{window}:5m])'
        ),
        "cpu_p95_total": (
            f"quantile_over_time(0.95, sum"
            f"(rate(container_cpu_usage_seconds_total{_C}[5m]))[{window}:5m])"
        ),
        "mem_req_total": (
            f"max_over_time(sum(kube_pod_container_resource_requests"
            f'{{resource="memory"}})[{window}:30m])'
        ),
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


def collect_namespaces(
    client: PrometheusClient, queries: dict[str, str]
) -> list[NsDemand]:
    """Per-namespace demand, sorted by reserved-but-unused CPU (worst first)."""
    cpu_req = _by_ns(client, queries["cpu_req_by_ns"])
    cpu_p95 = _by_ns(client, queries["cpu_p95_by_ns"])
    cpu_max = _by_ns(client, queries["cpu_max_by_ns"])
    mem_req = _by_ns(client, queries["mem_req_by_ns"])
    mem_max = _by_ns(client, queries["mem_max_by_ns"])
    names = cpu_req.keys() | cpu_p95.keys() | mem_req.keys() | mem_max.keys()
    rows = [
        NsDemand(
            namespace=ns,
            cpu_req=cpu_req.get(ns, 0.0),
            cpu_p95=cpu_p95.get(ns, 0.0),
            cpu_max=cpu_max.get(ns, 0.0),
            mem_req_gb=mem_req.get(ns, 0.0) / 1024**3,
            mem_max_gb=mem_max.get(ns, 0.0) / 1024**3,
        )
        for ns in names
    ]
    return sorted(rows, key=lambda r: r.cpu_phantom, reverse=True)


def collect_spikers(client: PrometheusClient, query: str) -> list[MemSpiker]:
    """Top pods by peak memory over the window, largest first."""
    rows = [
        MemSpiker(
            namespace=labels.get("namespace", "?"),
            pod=labels.get("pod", "?"),
            mem_max_gb=value / 1024**3,
        )
        for labels, value in client.instant(query)
    ]
    return sorted(rows, key=lambda r: r.mem_max_gb, reverse=True)


def _write_namespaces_csv(data_dir: str, rows: list[NsDemand]) -> Path:
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


def _write_spikers_csv(data_dir: str, spikers: list[MemSpiker]) -> None:
    write_csv(
        Path(data_dir) / "mem_spikers.csv",
        ["namespace", "pod", "memMaxGB"],
        [[s.namespace, s.pod, f"{s.mem_max_gb:.2f}"] for s in spikers],
    )


def collect_cluster(client: PrometheusClient, queries: dict[str, str]) -> ClusterTotals:
    """Cluster-wide totals: node count, requested vs actually used."""
    return ClusterTotals(
        nodes=client.scalar(queries["node_count"]) or 0.0,
        cpu_req=client.scalar(queries["cpu_req_total"]) or 0.0,
        cpu_p95=client.scalar(queries["cpu_p95_total"]) or 0.0,
        mem_req_gb=(client.scalar(queries["mem_req_total"]) or 0.0) / 1024**3,
        mem_max_gb=(client.scalar(queries["mem_max_total"]) or 0.0) / 1024**3,
    )


def _print_cluster(totals: ClusterTotals) -> None:
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
    data_dir: str, totals: ClusterTotals, window: str
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
    rows = collect_namespaces(client, queries)
    ok(f"{len(rows)} namespaces with metrics")
    out_path = _write_namespaces_csv(data_dir, rows)
    _write_spikers_csv(data_dir, collect_spikers(client, queries["mem_spikers"]))

    totals = collect_cluster(client, queries)
    _print_cluster(totals)
    _write_cluster_totals_csv(data_dir, totals, window)

    banner(4, "Write CSV")
    ok(f"wrote {out_path} (+ mem_spikers.csv, cluster_totals.csv)")
    return str(out_path)


__all__ = [
    "build_queries",
    "collect_cluster",
    "collect_namespaces",
    "collect_spikers",
    "execute_capacity_plan",
]
