#!/usr/bin/env python3
"""
Kubernetes Resource Audit Tool - Refactored
Tracks resource usage, limits, and trends over time
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mks.application._resource_audit_models import (
    AuditSnapshot,
    ContainerResources,
    NamespaceEfficiencyInfo,
    NodeInfo,
    PodDensityInfo,
    PodInfo,
    SchedulingIssue,
)
from mks.application._resource_audit_reports import (
    generate_namespace_efficiency_csv,
    generate_namespaces_csv,
    generate_nodes_csv,
    generate_pod_density_csv,
    generate_pods_csv,
    generate_recommendations_md,
    generate_scheduling_issues_csv,
    generate_trends_csv,
)
from mks.domain.namespace_policy import is_system_namespace
from mks.domain.quantity_parser import parse_cpu, parse_memory
from mks.infrastructure.kubectl_client import KubectlError, kubectl_json


class K8sResourceAuditor:
    """Main auditor class with historical tracking"""

    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now()

        # File paths
        self.snapshots_file = self.data_dir / "audit_snapshots.json"
        self.trends_file = self.data_dir / "trends.csv"

    def _run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            return kubectl_json(command)
        except KubectlError as e:
            print(f"❌ Error running kubectl: {e}")
            raise

    @staticmethod
    def _extract_node_type(node: dict[str, Any]) -> str:
        """Determine node type from standard Kubernetes labels."""
        labels = node.get("metadata", {}).get("labels", {})
        return str(
            labels.get("node.kubernetes.io/instance-type")
            or labels.get("beta.kubernetes.io/instance-type")
            or "unknown"
        )

    def _analyze_container(self, container: dict[str, Any]) -> ContainerResources:
        """Analyze single container resources"""
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})

        cpu_request = float(parse_cpu(requests.get("cpu", "0")))
        cpu_limit = float(parse_cpu(limits.get("cpu", "0")))
        memory_request = parse_memory(requests.get("memory", "0Ki"))
        memory_limit = parse_memory(limits.get("memory", "0Ki"))

        # Detect issues
        issues = []

        if cpu_request == 0 and cpu_limit == 0:
            issues.append("NO_CPU_RESOURCES")
        elif cpu_request == 0:
            issues.append("NO_CPU_REQUEST")
        elif cpu_limit == 0:
            issues.append("NO_CPU_LIMIT")

        if memory_request == 0 and memory_limit == 0:
            issues.append("NO_MEMORY_RESOURCES")
        elif memory_request == 0:
            issues.append("NO_MEMORY_REQUEST")
        elif memory_limit == 0:
            issues.append("NO_MEMORY_LIMIT")

        # Check ratios
        if cpu_limit > 0 and cpu_request > 0:
            ratio = cpu_limit / cpu_request
            if ratio > 10:
                issues.append(f"HIGH_CPU_RATIO_{ratio:.1f}x")

        if memory_limit > 0 and memory_request > 0:
            ratio = memory_limit / memory_request
            if ratio > 5:
                issues.append(f"HIGH_MEMORY_RATIO_{ratio:.1f}x")

        return ContainerResources(
            name=container["name"],
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            memory_request=memory_request,
            memory_limit=memory_limit,
            issues=tuple(issues),
        )

    def collect_cluster_data(self) -> AuditSnapshot:
        """Collect complete cluster state"""
        print("🔍 Collecting cluster data...")

        # Get nodes
        nodes_data = self._run_kubectl("get nodes")
        nodes: list[NodeInfo] = []

        for node in nodes_data["items"]:
            name = node["metadata"]["name"]
            capacity = node["status"]["capacity"]
            allocatable = node["status"]["allocatable"]

            nodes.append(
                NodeInfo(
                    name=name,
                    node_type=self._extract_node_type(node),
                    cpu_capacity=float(parse_cpu(capacity.get("cpu", "0"))),
                    memory_capacity=parse_memory(capacity.get("memory", "0Ki")),
                    cpu_allocatable=float(parse_cpu(allocatable.get("cpu", "0"))),
                    memory_allocatable=parse_memory(allocatable.get("memory", "0Ki")),
                    pod_capacity=int(capacity.get("pods", "0")),
                )
            )

        # Get pods
        pods_data = self._run_kubectl("get pods -A")
        pods: list[PodInfo] = []

        for pod in pods_data["items"]:
            # Skip system namespaces and only include running pods for resource analysis
            namespace = pod["metadata"]["namespace"]
            if pod["status"]["phase"] == "Running" and not is_system_namespace(
                namespace
            ):
                containers = tuple(
                    self._analyze_container(container)
                    for container in pod["spec"]["containers"]
                )

                pods.append(
                    PodInfo(
                        name=pod["metadata"]["name"],
                        namespace=namespace,
                        node=pod["spec"].get("nodeName", "Unknown"),
                        containers=containers,
                    )
                )

        # Calculate cluster stats
        cluster_stats = self._calculate_cluster_stats(nodes, pods)

        return AuditSnapshot(
            timestamp=self.timestamp,
            nodes=tuple(nodes),
            pods=tuple(pods),
            cluster_stats=cluster_stats,
        )

    def _calculate_cluster_stats(
        self, nodes: list[NodeInfo], pods: list[PodInfo]
    ) -> dict[str, Any]:
        """Calculate cluster-wide statistics"""
        total_containers = sum(len(pod.containers) for pod in pods)
        containers_with_issues = sum(
            1 for pod in pods for container in pod.containers if container.issues
        )

        # Group by severity
        severity_counts: dict[str, int] = defaultdict(int)
        for pod in pods:
            for container in pod.containers:
                if container.issues:
                    severity_counts[container.severity] += 1

        # Resource totals
        total_cpu_requests = sum(pod.total_cpu_request for pod in pods)
        total_cpu_limits = sum(pod.total_cpu_limit for pod in pods)
        total_memory_requests = sum(pod.total_memory_request for pod in pods)
        total_memory_limits = sum(pod.total_memory_limit for pod in pods)

        cluster_cpu_capacity = sum(node.cpu_allocatable for node in nodes)
        cluster_memory_capacity = sum(node.memory_allocatable for node in nodes)

        return {
            "total_nodes": len(nodes),
            "total_pods": len(pods),
            "total_containers": total_containers,
            "containers_with_issues": containers_with_issues,
            "issue_rate": containers_with_issues / total_containers
            if total_containers > 0
            else 0,
            "severity_breakdown": dict(severity_counts),
            "resource_utilization": {
                "cpu_requests_ratio": total_cpu_requests / cluster_cpu_capacity
                if cluster_cpu_capacity > 0
                else 0,
                "cpu_limits_ratio": total_cpu_limits / cluster_cpu_capacity
                if cluster_cpu_capacity > 0
                else 0,
                "memory_requests_ratio": total_memory_requests / cluster_memory_capacity
                if cluster_memory_capacity > 0
                else 0,
                "memory_limits_ratio": total_memory_limits / cluster_memory_capacity
                if cluster_memory_capacity > 0
                else 0,
            },
        }

    def save_snapshot(self, snapshot: AuditSnapshot) -> None:
        """Save snapshot to persistent storage"""
        snapshots: list[dict[str, Any]] = []

        # Load existing snapshots
        if self.snapshots_file.exists():
            with open(self.snapshots_file, encoding="utf-8") as f:
                existing_data = json.load(f)
                snapshots = existing_data.get("snapshots", [])

        # Add new snapshot
        snapshots.append(snapshot.to_dict())

        # Keep only last 30 snapshots
        snapshots = snapshots[-30:]

        # Save
        with open(self.snapshots_file, "w", encoding="utf-8") as f:
            json.dump({"snapshots": snapshots}, f, indent=2)

        print(f"💾 Snapshot saved to {self.snapshots_file}")

    def load_snapshots(self) -> list[AuditSnapshot]:
        """Load historical snapshots"""
        if not self.snapshots_file.exists():
            return []

        with open(self.snapshots_file, encoding="utf-8") as f:
            data = json.load(f)
            return [AuditSnapshot.from_dict(s) for s in data.get("snapshots", [])]

    def _generate_reports(
        self, snapshot: AuditSnapshot, previous_snapshots: list[AuditSnapshot]
    ) -> None:
        """Generate comprehensive reports"""
        timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")

        print("📊 Generating CSV reports...")
        generate_pods_csv(snapshot, timestamp_str, self.data_dir)
        generate_nodes_csv(snapshot, timestamp_str, self.data_dir)
        generate_namespaces_csv(snapshot, timestamp_str, self.data_dir)

        print("📈 Generating trend analysis...")
        generate_trends_csv(previous_snapshots + [snapshot], self.trends_file)

        print("📋 Generating recommendations...")
        generate_recommendations_md(snapshot, previous_snapshots, self.data_dir)

    def analyze_pod_density(self, snapshot: AuditSnapshot) -> list[PodDensityInfo]:
        """Analyze pod density per node with capacity utilization"""
        all_pods_data = self._run_kubectl("get pods -A")
        node_pod_stats = self._collect_node_pod_stats(all_pods_data)
        node_usage = self._collect_node_usage(snapshot)
        return self._build_density_rows(snapshot, node_pod_stats, node_usage)

    def analyze_namespace_efficiency(
        self, snapshot: AuditSnapshot
    ) -> list[NamespaceEfficiencyInfo]:
        """Analyze namespace resource efficiency excluding system namespaces"""
        ns_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "cpu_requests": 0.0,
                "memory_requests": 0,
                "pod_count": 0,
                "container_count": 0,
            }
        )

        # System namespaces are already filtered in collect_cluster_data
        for pod in snapshot.pods:
            ns_stats[pod.namespace]["cpu_requests"] += pod.total_cpu_request
            ns_stats[pod.namespace]["memory_requests"] += pod.total_memory_request
            ns_stats[pod.namespace]["pod_count"] += 1
            ns_stats[pod.namespace]["container_count"] += len(pod.containers)

        efficiency_info: list[NamespaceEfficiencyInfo] = []

        for ns, stats in ns_stats.items():
            # Simulate potential waste (30% of requests as potential waste baseline)
            cpu_waste_potential = stats["cpu_requests"] * 0.3
            memory_waste_potential = int(stats["memory_requests"] * 0.3)

            # Calculate efficiency score (higher is better, based on resource density)
            cpu_per_pod = (
                stats["cpu_requests"] / stats["pod_count"]
                if stats["pod_count"] > 0
                else 0
            )
            efficiency_score = min(100.0, (cpu_per_pod / 1000) * 100)  # Scale to 0-100

            efficiency_info.append(
                NamespaceEfficiencyInfo(
                    namespace=ns,
                    cpu_requests=stats["cpu_requests"],
                    memory_requests=stats["memory_requests"],
                    cpu_waste_potential=cpu_waste_potential,
                    memory_waste_potential=memory_waste_potential,
                    efficiency_score=efficiency_score,
                    pod_count=stats["pod_count"],
                    container_count=stats["container_count"],
                )
            )

        return sorted(
            efficiency_info, key=lambda x: x.cpu_waste_potential, reverse=True
        )

    def detect_scheduling_issues(
        self, snapshot: AuditSnapshot
    ) -> list[SchedulingIssue]:
        """Detect pending pods, failed pods, and capacity issues"""
        all_pods_data = self._run_kubectl("get pods -A")
        node_usage = self._collect_node_usage(snapshot, with_pod_count=True)
        issues = self._detect_over_capacity_nodes(snapshot, node_usage)
        for pod in all_pods_data["items"]:
            namespace = pod["metadata"]["namespace"]
            if is_system_namespace(namespace):
                continue
            issue = self._build_non_running_issue(pod)
            if issue is not None:
                issues.append(issue)

        return issues

    def _collect_node_pod_stats(
        self, all_pods_data: dict[str, Any]
    ) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"running": 0, "failed": 0, "pending": 0, "total": 0}
        )
        for pod in all_pods_data["items"]:
            namespace = pod["metadata"]["namespace"]
            if is_system_namespace(namespace):
                continue
            node_name = pod["spec"].get("nodeName", "Unknown")
            phase = pod["status"]["phase"]
            stats[node_name]["total"] += 1
            if phase == "Running":
                stats[node_name]["running"] += 1
            elif phase == "Failed":
                stats[node_name]["failed"] += 1
            elif phase == "Pending":
                stats[node_name]["pending"] += 1
        return stats

    def _collect_node_usage(
        self,
        snapshot: AuditSnapshot,
        *,
        with_pod_count: bool = False,
    ) -> dict[str, dict[str, float]]:
        usage_template = {"cpu_requests": 0.0, "memory_requests": 0.0}
        if with_pod_count:
            usage_template["pod_count"] = 0.0

        def _new_usage_bucket() -> dict[str, float]:
            return usage_template.copy()

        node_usage: dict[str, dict[str, float]] = defaultdict(_new_usage_bucket)
        for pod in snapshot.pods:
            if pod.node == "Unknown":
                continue
            node_usage[pod.node]["cpu_requests"] += pod.total_cpu_request
            node_usage[pod.node]["memory_requests"] += pod.total_memory_request
            if with_pod_count:
                node_usage[pod.node]["pod_count"] += 1
        return node_usage

    def _build_density_rows(
        self,
        snapshot: AuditSnapshot,
        node_pod_stats: dict[str, dict[str, int]],
        node_usage: dict[str, dict[str, float]],
    ) -> list[PodDensityInfo]:
        rows: list[PodDensityInfo] = []
        for node in snapshot.nodes:
            stats = node_pod_stats[node.name]
            usage = node_usage[node.name]
            pod_utilization = (
                (stats["total"] / node.pod_capacity * 100)
                if node.pod_capacity > 0
                else 0
            )
            cpu_utilization = (
                (usage["cpu_requests"] / node.cpu_allocatable * 100)
                if node.cpu_allocatable > 0
                else 0
            )
            memory_utilization = (
                (usage["memory_requests"] / node.memory_allocatable * 100)
                if node.memory_allocatable > 0
                else 0
            )
            rows.append(
                PodDensityInfo(
                    node_name=node.name,
                    node_type=node.node_type,
                    running_pods=stats["running"],
                    failed_pods=stats["failed"],
                    pending_pods=stats["pending"],
                    total_pods=stats["total"],
                    pod_capacity=node.pod_capacity,
                    cpu_capacity=node.cpu_allocatable,
                    memory_capacity=node.memory_allocatable,
                    cpu_requests=usage["cpu_requests"],
                    memory_requests=int(usage["memory_requests"]),
                    pod_utilization_pct=pod_utilization,
                    cpu_utilization_pct=cpu_utilization,
                    memory_utilization_pct=memory_utilization,
                    approaching_limit=pod_utilization > 90.0,
                )
            )
        return rows

    def _detect_over_capacity_nodes(
        self,
        snapshot: AuditSnapshot,
        node_usage: dict[str, dict[str, float]],
    ) -> list[SchedulingIssue]:
        issues: list[SchedulingIssue] = []
        for node in snapshot.nodes:
            usage = node_usage[node.name]
            if usage["pod_count"] <= node.pod_capacity:
                continue
            issues.append(
                SchedulingIssue(
                    pod_name=f"node-{node.name}",
                    namespace="cluster",
                    issue_type="OVER_CAPACITY",
                    reason=(
                        f"Node has {int(usage['pod_count'])} pods "
                        f"but capacity is {node.pod_capacity}"
                    ),
                    node_name=node.name,
                    duration_minutes=None,
                    cpu_request=usage["cpu_requests"],
                    memory_request=int(usage["memory_requests"]),
                )
            )
        return issues

    def _build_non_running_issue(self, pod: dict[str, Any]) -> SchedulingIssue | None:
        phase = pod["status"]["phase"]
        if phase not in {"Pending", "Failed"}:
            return None
        cpu_request, memory_request = self._calculate_pod_requests(pod)
        if phase == "Pending":
            reason = self._extract_pending_reason(pod)
            return SchedulingIssue(
                pod_name=pod["metadata"]["name"],
                namespace=pod["metadata"]["namespace"],
                issue_type="PENDING",
                reason=reason,
                node_name=pod["spec"].get("nodeName"),
                duration_minutes=None,
                cpu_request=cpu_request,
                memory_request=memory_request,
            )
        reason = str(pod["status"].get("reason", "Unknown"))
        return SchedulingIssue(
            pod_name=pod["metadata"]["name"],
            namespace=pod["metadata"]["namespace"],
            issue_type="FAILED",
            reason=reason,
            node_name=pod["spec"].get("nodeName"),
            duration_minutes=None,
            cpu_request=cpu_request,
            memory_request=memory_request,
        )

    def _calculate_pod_requests(self, pod: dict[str, Any]) -> tuple[float, int]:
        cpu_request = 0.0
        memory_request = 0
        for container in pod["spec"]["containers"]:
            resources = container.get("resources", {})
            requests = resources.get("requests", {})
            cpu_request += float(parse_cpu(requests.get("cpu", "0")))
            memory_request += parse_memory(requests.get("memory", "0Ki"))
        return cpu_request, memory_request

    @staticmethod
    def _extract_pending_reason(pod: dict[str, Any]) -> str:
        status = pod.get("status", {})
        for condition in status.get("conditions", []):
            if (
                condition.get("type") == "PodScheduled"
                and condition.get("status") == "False"
            ):
                return str(condition.get("reason", "Unknown"))
        return "Unknown"

    def _generate_extended_reports(self, snapshot: AuditSnapshot) -> None:
        """Generate extended operational metrics reports"""
        timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")
        timestamp_iso = self.timestamp.isoformat()

        print("📊 Generating extended operational reports...")

        # Pod density analysis
        density_info = self.analyze_pod_density(snapshot)
        generate_pod_density_csv(
            density_info,
            timestamp_str,
            timestamp_iso,
            self.data_dir,
        )

        # Namespace efficiency analysis
        efficiency_info = self.analyze_namespace_efficiency(snapshot)
        generate_namespace_efficiency_csv(
            efficiency_info,
            timestamp_str,
            timestamp_iso,
            self.data_dir,
        )

        # Scheduling issues detection
        scheduling_issues = self.detect_scheduling_issues(snapshot)
        generate_scheduling_issues_csv(
            scheduling_issues,
            timestamp_str,
            timestamp_iso,
            self.data_dir,
        )

    def run_audit(self, mode: str = "current") -> None:
        """Run complete audit process"""
        print("🚀 Starting Kubernetes Resource Audit")
        print(f"📅 Timestamp: {self.timestamp}")
        print(f"🔧 Mode: {mode}")

        # Collect current state
        snapshot = self.collect_cluster_data()

        # Load historical data
        previous_snapshots = self.load_snapshots()

        # Save current snapshot
        self.save_snapshot(snapshot)

        if mode == "extended":
            # Generate extended operational reports
            self._generate_extended_reports(snapshot)
        else:
            # Generate standard reports
            self._generate_reports(snapshot, previous_snapshots)

        print(f"\n✅ Audit completed! Check {self.data_dir}/ for reports")
        print("📈 Run regularly to track trends and improvements")


def execute_resource_audit(mode: str = "current", data_dir: str = "reports") -> None:
    """Execute resource audit use-case."""
    auditor = K8sResourceAuditor(data_dir=data_dir)
    auditor.run_audit(mode=mode)


def execute(mode: str = "current", data_dir: str = "reports") -> None:
    """Backward-compatible alias for execute_resource_audit."""
    execute_resource_audit(mode=mode, data_dir=data_dir)


if __name__ == "__main__":
    execute_resource_audit()
