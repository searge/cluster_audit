"""Helpers for pod status aggregation in reports."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


def count_pods_by_namespace(
    pods: dict[str, Any],
    *,
    namespace_filter: Callable[[str], bool] | None = None,
) -> dict[str, dict[str, int]]:
    """Aggregate running/failed/pending/total pod counters by namespace."""
    ns_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"running": 0, "total": 0, "failed": 0, "pending": 0}
    )
    for pod in pods.get("items", []):
        namespace = pod["metadata"]["namespace"]
        if namespace_filter is not None and not namespace_filter(namespace):
            continue
        phase = pod["status"]["phase"]
        ns_stats[namespace]["total"] += 1
        if phase == "Running":
            ns_stats[namespace]["running"] += 1
        elif phase == "Failed":
            ns_stats[namespace]["failed"] += 1
        elif phase == "Pending":
            ns_stats[namespace]["pending"] += 1
    return dict(ns_stats)


def health_status_for_failed(failed_pods: int) -> str:
    """Return health indicator label by failed pod count."""
    if failed_pods == 0:
        return "🟢 Healthy"
    if failed_pods < 5:
        return f"🟡 {failed_pods} Failed"
    return f"🔴 {failed_pods} Failed"
