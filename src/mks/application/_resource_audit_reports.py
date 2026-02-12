"""Report generation helpers for resource audit service."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from mks.application._resource_audit_models import (
    AuditSnapshot,
    NamespaceEfficiencyInfo,
    PodDensityInfo,
    SchedulingIssue,
)


def generate_pods_csv(snapshot: AuditSnapshot, timestamp: str, data_dir: Path) -> None:
    """Generate detailed pods report."""
    filename = data_dir / f"pods_detail_{timestamp}.csv"
    rows: list[dict[str, Any]] = []
    for pod in snapshot.pods:
        for container in pod.containers:
            rows.append(
                {
                    "timestamp": snapshot.timestamp.isoformat(),
                    "namespace": pod.namespace,
                    "pod_name": pod.name,
                    "container_name": container.name,
                    "node": pod.node,
                    "node_type": next(
                        (n.node_type for n in snapshot.nodes if n.name == pod.node),
                        "unknown",
                    ),
                    "cpu_request_m": int(container.cpu_request),
                    "cpu_limit_m": int(container.cpu_limit),
                    "memory_request_mb": int(container.memory_request / 1024 / 1024),
                    "memory_limit_mb": int(container.memory_limit / 1024 / 1024),
                    "cpu_ratio": f"{container.cpu_ratio:.1f}",
                    "memory_ratio": f"{container.memory_ratio:.1f}",
                    "issues": "|".join(container.issues),
                    "severity": container.severity,
                    "has_issues": len(container.issues) > 0,
                }
            )
    pd.DataFrame(rows).to_csv(filename, index=False)
    print(f"  → {filename}")


def generate_nodes_csv(snapshot: AuditSnapshot, timestamp: str, data_dir: Path) -> None:
    """Generate node utilization report."""
    filename = data_dir / f"nodes_utilization_{timestamp}.csv"
    node_usage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "pod_count": 0,
            "cpu_requests": 0.0,
            "cpu_limits": 0.0,
            "memory_requests": 0,
            "memory_limits": 0,
            "issues_count": 0,
        }
    )
    for pod in snapshot.pods:
        if pod.node != "Unknown":
            node_usage[pod.node]["pod_count"] += 1
            node_usage[pod.node]["cpu_requests"] += pod.total_cpu_request
            node_usage[pod.node]["cpu_limits"] += pod.total_cpu_limit
            node_usage[pod.node]["memory_requests"] += pod.total_memory_request
            node_usage[pod.node]["memory_limits"] += pod.total_memory_limit
            node_usage[pod.node]["issues_count"] += sum(
                1 for c in pod.containers if c.issues
            )

    rows: list[dict[str, Any]] = []
    for node in snapshot.nodes:
        usage = node_usage[node.name]
        cpu_request_ratio_value = (
            usage["cpu_requests"] / node.cpu_allocatable * 100
            if node.cpu_allocatable > 0
            else 0.0
        )
        cpu_limit_ratio_value = (
            usage["cpu_limits"] / node.cpu_allocatable * 100
            if node.cpu_allocatable > 0
            else 0.0
        )
        memory_request_ratio_value = (
            usage["memory_requests"] / node.memory_allocatable * 100
            if node.memory_allocatable > 0
            else 0.0
        )
        memory_limit_ratio_value = (
            usage["memory_limits"] / node.memory_allocatable * 100
            if node.memory_allocatable > 0
            else 0.0
        )
        pod_utilization_value = (
            usage["pod_count"] / node.pod_capacity * 100
            if node.pod_capacity > 0
            else 0.0
        )
        rows.append(
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "node_name": node.name,
                "node_type": node.node_type,
                "pod_count": usage["pod_count"],
                "pod_capacity": node.pod_capacity,
                "cpu_requests_m": int(usage["cpu_requests"]),
                "cpu_limits_m": int(usage["cpu_limits"]),
                "cpu_capacity_m": int(node.cpu_allocatable),
                "memory_requests_mb": int(usage["memory_requests"] / 1024 / 1024),
                "memory_limits_mb": int(usage["memory_limits"] / 1024 / 1024),
                "memory_capacity_mb": int(node.memory_allocatable / 1024 / 1024),
                "cpu_request_ratio": f"{cpu_request_ratio_value:.1f}%",
                "cpu_limit_ratio": f"{cpu_limit_ratio_value:.1f}%",
                "memory_request_ratio": f"{memory_request_ratio_value:.1f}%",
                "memory_limit_ratio": f"{memory_limit_ratio_value:.1f}%",
                "issues_count": usage["issues_count"],
                "pod_utilization": f"{pod_utilization_value:.1f}%",
            }
        )
    pd.DataFrame(rows).to_csv(filename, index=False)
    print(f"  → {filename}")


def generate_namespaces_csv(
    snapshot: AuditSnapshot,
    timestamp: str,
    data_dir: Path,
) -> None:
    """Generate namespace summary."""
    filename = data_dir / f"namespaces_summary_{timestamp}.csv"
    ns_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "pod_count": 0,
            "container_count": 0,
            "issues_count": 0,
            "cpu_requests": 0.0,
            "cpu_limits": 0.0,
            "memory_requests": 0,
            "memory_limits": 0,
            "severity_counts": defaultdict(int),
        }
    )
    for pod in snapshot.pods:
        ns = pod.namespace
        ns_stats[ns]["pod_count"] += 1
        ns_stats[ns]["cpu_requests"] += pod.total_cpu_request
        ns_stats[ns]["cpu_limits"] += pod.total_cpu_limit
        ns_stats[ns]["memory_requests"] += pod.total_memory_request
        ns_stats[ns]["memory_limits"] += pod.total_memory_limit
        for container in pod.containers:
            ns_stats[ns]["container_count"] += 1
            if container.issues:
                ns_stats[ns]["issues_count"] += 1
                ns_stats[ns]["severity_counts"][container.severity] += 1

    rows: list[dict[str, Any]] = []
    for ns, stats in ns_stats.items():
        health_score = (
            max(0, 100 - (stats["issues_count"] / stats["container_count"] * 100))
            if stats["container_count"] > 0
            else 100
        )
        if health_score < 50 or stats["cpu_limits"] > 10000:
            priority = "HIGH"
        elif health_score < 80 or stats["cpu_limits"] > 5000:
            priority = "MEDIUM"
        else:
            priority = "LOW"
        rows.append(
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "namespace": ns,
                "pod_count": stats["pod_count"],
                "container_count": stats["container_count"],
                "issues_count": stats["issues_count"],
                "health_score": f"{health_score:.1f}%",
                "priority": priority,
                "cpu_requests_m": int(stats["cpu_requests"]),
                "cpu_limits_m": int(stats["cpu_limits"]),
                "memory_requests_mb": int(stats["memory_requests"] / 1024 / 1024),
                "memory_limits_mb": int(stats["memory_limits"] / 1024 / 1024),
                "critical_issues": stats["severity_counts"]["CRITICAL"],
                "high_issues": stats["severity_counts"]["HIGH"],
                "medium_issues": stats["severity_counts"]["MEDIUM"],
                "low_issues": stats["severity_counts"]["LOW"],
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values(["priority", "cpu_limits_m"], ascending=[False, False])
    df.to_csv(filename, index=False)
    print(f"  → {filename}")


def generate_trends_csv(snapshots: list[AuditSnapshot], trends_file: Path) -> None:
    """Generate trend analysis across snapshots."""
    if len(snapshots) < 2:
        return
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        resource_utilization = snapshot.cluster_stats["resource_utilization"]
        rows.append(
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "total_pods": snapshot.cluster_stats["total_pods"],
                "total_containers": snapshot.cluster_stats["total_containers"],
                "containers_with_issues": snapshot.cluster_stats[
                    "containers_with_issues"
                ],
                "issue_rate": f"{snapshot.cluster_stats['issue_rate'] * 100:.1f}%",
                "cpu_requests_ratio": (
                    f"{resource_utilization['cpu_requests_ratio'] * 100:.1f}%"
                ),
                "cpu_limits_ratio": (
                    f"{resource_utilization['cpu_limits_ratio'] * 100:.1f}%"
                ),
                "memory_requests_ratio": (
                    f"{resource_utilization['memory_requests_ratio'] * 100:.1f}%"
                ),
                "memory_limits_ratio": (
                    f"{resource_utilization['memory_limits_ratio'] * 100:.1f}%"
                ),
                "critical_issues": snapshot.cluster_stats["severity_breakdown"].get(
                    "CRITICAL", 0
                ),
                "high_issues": snapshot.cluster_stats["severity_breakdown"].get(
                    "HIGH", 0
                ),
                "medium_issues": snapshot.cluster_stats["severity_breakdown"].get(
                    "MEDIUM", 0
                ),
                "low_issues": snapshot.cluster_stats["severity_breakdown"].get(
                    "LOW", 0
                ),
            }
        )
    pd.DataFrame(rows).to_csv(trends_file, index=False)
    print(f"  → {trends_file}")


def generate_recommendations_md(
    snapshot: AuditSnapshot,
    previous_snapshots: list[AuditSnapshot],
    data_dir: Path,
) -> None:
    """Generate actionable markdown recommendations report."""
    filename = (
        data_dir / f"recommendations_{snapshot.timestamp.strftime('%Y%m%d_%H%M%S')}.md"
    )
    stats = snapshot.cluster_stats
    lines = _build_recommendations_lines(snapshot, previous_snapshots, stats)
    filename.write_text("".join(lines), encoding="utf-8")
    print(f"  → {filename}")


def _build_recommendations_lines(
    snapshot: AuditSnapshot,
    previous_snapshots: list[AuditSnapshot],
    stats: dict[str, Any],
) -> list[str]:
    lines = _build_summary_section(snapshot, stats)
    lines.extend(_build_severity_section(stats))
    lines.extend(_build_trends_section(stats, previous_snapshots))
    lines.extend(_build_priority_actions_section(stats))
    lines.extend(_build_maintenance_section())
    return lines


def _build_summary_section(snapshot: AuditSnapshot, stats: dict[str, Any]) -> list[str]:
    utilization = stats["resource_utilization"]
    return [
        "# Kubernetes Resource Audit Report\n",
        f"**Generated**: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        "## 📊 Executive Summary\n\n",
        "| Metric | Value |\n",
        "|--------|-------|\n",
        f"| **Total Pods** | {stats['total_pods']} |\n",
        f"| **Total Containers** | {stats['total_containers']} |\n",
        (
            f"| **Containers with Issues** | {stats['containers_with_issues']} "
            f"({stats['issue_rate'] * 100:.1f}%) |\n"
        ),
        (
            f"| **CPU Limits Overcommit** | "
            f"{utilization['cpu_limits_ratio'] * 100:.1f}% |\n"
        ),
        (
            "| **Memory Limits Overcommit** | "
            f"{utilization['memory_limits_ratio'] * 100:.1f}% |\n\n"
        ),
    ]


def _build_severity_section(stats: dict[str, Any]) -> list[str]:
    lines = ["## 🚨 Issues by Severity\n\n"]
    for severity, count in stats["severity_breakdown"].items():
        emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(
            severity,
            "⚪",
        )
        lines.append(f"- {emoji} **{severity}**: {count} containers\n")
    lines.append("\n")
    return lines


def _build_trends_section(
    stats: dict[str, Any], previous_snapshots: list[AuditSnapshot]
) -> list[str]:
    if not previous_snapshots:
        return []
    prev = previous_snapshots[-1]
    issue_trend = (
        stats["containers_with_issues"] - prev.cluster_stats["containers_with_issues"]
    )
    trend_emoji = "📈" if issue_trend > 0 else "📉" if issue_trend < 0 else "➡️"
    return [
        "## 📈 Trends\n\n",
        f"- **Issues trend**: {trend_emoji} {issue_trend:+d} since last audit\n",
        (
            f"- **Pod growth**: "
            f"{stats['total_pods'] - prev.cluster_stats['total_pods']:+d} pods\n\n"
        ),
    ]


def _build_priority_actions_section(stats: dict[str, Any]) -> list[str]:
    lines = ["## 🎯 Priority Actions\n\n"]
    critical_count = stats["severity_breakdown"].get("CRITICAL", 0)
    if critical_count > 0:
        lines.extend(
            [
                f"### 🚨 URGENT: {critical_count} containers without any limits\n",
                "Apply default LimitRange to namespaces:\n",
                "```bash\n",
                "kubectl apply -f - <<EOF\n",
                "apiVersion: v1\n",
                "kind: LimitRange\n",
                "metadata:\n",
                "  name: default-limits\n",
                "spec:\n",
                "  limits:\n",
                "  - default:\n",
                "      cpu: '200m'\n",
                "      memory: '256Mi'\n",
                "    defaultRequest:\n",
                "      cpu: '100m'\n",
                "      memory: '128Mi'\n",
                "    type: Container\n",
                "EOF\n",
                "```\n\n",
            ]
        )
    cpu_limits_ratio = stats["resource_utilization"]["cpu_limits_ratio"]
    if cpu_limits_ratio > 1.5:
        lines.extend(
            [
                "### 🔄 Migration Impact\n",
                (
                    "Current CPU overcommit "
                    f"({cpu_limits_ratio * 100:.1f}%) "
                    "will cause issues during migration.\n"
                ),
                "**Must fix** resource limits before node migration!\n\n",
            ]
        )
    return lines


def _build_maintenance_section() -> list[str]:
    return [
        "## 🧹 Maintenance Tasks\n\n",
        "```bash\n",
        "# Daily cleanup (add to cron)\n",
        "kubectl delete pod --field-selector=status.phase==Failed -A\n",
        "kubectl delete pod --field-selector=status.phase==Succeeded -A\n",
        "kubectl delete pod --field-selector=status.phase==Completed -A\n\n",
        "# Weekly audit\n",
        "mks resource-audit --report reports\n",
        "```\n",
    ]


def generate_pod_density_csv(
    density_info: list[PodDensityInfo],
    timestamp: str,
    timestamp_iso: str,
    data_dir: Path,
) -> None:
    """Generate pod density CSV report."""
    filename = data_dir / f"pod_density_{timestamp}.csv"
    rows: list[dict[str, Any]] = []
    for info in density_info:
        rows.append(
            {
                "timestamp": timestamp_iso,
                "node_name": info.node_name,
                "node_type": info.node_type,
                "running_pods": info.running_pods,
                "failed_pods": info.failed_pods,
                "pending_pods": info.pending_pods,
                "total_pods": info.total_pods,
                "pod_capacity": info.pod_capacity,
                "pod_utilization_pct": f"{info.pod_utilization_pct:.1f}",
                "cpu_requests_m": int(info.cpu_requests),
                "cpu_capacity_m": int(info.cpu_capacity),
                "cpu_utilization_pct": f"{info.cpu_utilization_pct:.1f}",
                "memory_requests_mb": int(info.memory_requests / 1024 / 1024),
                "memory_capacity_mb": int(info.memory_capacity / 1024 / 1024),
                "memory_utilization_pct": f"{info.memory_utilization_pct:.1f}",
                "approaching_pod_limit": info.approaching_limit,
                "alert_level": "HIGH" if info.approaching_limit else "NORMAL",
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["approaching_pod_limit", "pod_utilization_pct"], ascending=[False, False]
    )
    df.to_csv(filename, index=False)
    print(f"  → {filename}")


def generate_namespace_efficiency_csv(
    efficiency_info: list[NamespaceEfficiencyInfo],
    timestamp: str,
    timestamp_iso: str,
    data_dir: Path,
) -> None:
    """Generate namespace efficiency CSV report."""
    filename = data_dir / f"namespace_efficiency_{timestamp}.csv"
    rows: list[dict[str, Any]] = []
    for info in efficiency_info:
        rows.append(
            {
                "timestamp": timestamp_iso,
                "namespace": info.namespace,
                "pod_count": info.pod_count,
                "container_count": info.container_count,
                "cpu_requests_m": int(info.cpu_requests),
                "memory_requests_mb": int(info.memory_requests / 1024 / 1024),
                "cpu_waste_potential_m": int(info.cpu_waste_potential),
                "memory_waste_potential_mb": int(
                    info.memory_waste_potential / 1024 / 1024
                ),
                "efficiency_score": f"{info.efficiency_score:.1f}",
                "cpu_per_pod_m": int(info.cpu_requests / info.pod_count)
                if info.pod_count > 0
                else 0,
                "memory_per_pod_mb": int(
                    info.memory_requests / 1024 / 1024 / info.pod_count
                )
                if info.pod_count > 0
                else 0,
                "waste_priority": "HIGH"
                if info.cpu_waste_potential > 1000
                else "MEDIUM"
                if info.cpu_waste_potential > 500
                else "LOW",
            }
        )
    pd.DataFrame(rows).to_csv(filename, index=False)
    print(f"  → {filename}")


def generate_scheduling_issues_csv(
    scheduling_issues: list[SchedulingIssue],
    timestamp: str,
    timestamp_iso: str,
    data_dir: Path,
) -> None:
    """Generate scheduling issues CSV report."""
    filename = data_dir / f"scheduling_issues_{timestamp}.csv"
    rows: list[dict[str, Any]] = []
    for issue in scheduling_issues:
        rows.append(
            {
                "timestamp": timestamp_iso,
                "pod_name": issue.pod_name,
                "namespace": issue.namespace,
                "issue_type": issue.issue_type,
                "reason": issue.reason,
                "node_name": issue.node_name or "None",
                "duration_minutes": issue.duration_minutes or 0,
                "cpu_request_m": int(issue.cpu_request),
                "memory_request_mb": int(issue.memory_request / 1024 / 1024),
                "severity": "CRITICAL"
                if issue.issue_type == "OVER_CAPACITY"
                else "HIGH"
                if issue.issue_type == "PENDING"
                else "MEDIUM",
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values(["severity", "cpu_request_m"], ascending=[False, False])
    df.to_csv(filename, index=False)
    print(f"  → {filename}")
