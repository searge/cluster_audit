#!/usr/bin/env python3
"""
Kubernetes Resource Audit Tool - Refactored
Tracks resource usage, limits, and trends over time
"""

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd


@dataclass
class ContainerResources:
    """Container resource specification"""

    name: str
    cpu_request: float  # millicores
    cpu_limit: float  # millicores
    memory_request: int  # bytes
    memory_limit: int  # bytes
    issues: list[str]

    @property
    def cpu_ratio(self) -> float:
        """CPU limit/request ratio"""
        return self.cpu_limit / self.cpu_request if self.cpu_request > 0 else 0

    @property
    def memory_ratio(self) -> float:
        """Memory limit/request ratio"""
        return self.memory_limit / self.memory_request if self.memory_request > 0 else 0

    @property
    def severity(self) -> str:
        """Issue severity level"""
        if any("NO_" in issue and "RESOURCES" in issue for issue in self.issues):
            return "CRITICAL"
        if any("HIGH_" in issue and "RATIO" in issue for issue in self.issues):
            return "HIGH"
        if any("NO_" in issue for issue in self.issues):
            return "MEDIUM"
        return "LOW"


@dataclass
class PodInfo:
    """Pod information with containers"""

    name: str
    namespace: str
    node: str
    containers: list[ContainerResources]

    @property
    def total_cpu_request(self) -> float:
        return sum(c.cpu_request for c in self.containers)

    @property
    def total_cpu_limit(self) -> float:
        return sum(c.cpu_limit for c in self.containers)

    @property
    def total_memory_request(self) -> int:
        return sum(c.memory_request for c in self.containers)

    @property
    def total_memory_limit(self) -> int:
        return sum(c.memory_limit for c in self.containers)

    @property
    def has_issues(self) -> bool:
        return any(c.issues for c in self.containers)


@dataclass
class NodeInfo:
    """Node capacity and type information"""

    name: str
    node_type: str
    cpu_capacity: float
    memory_capacity: int
    cpu_allocatable: float
    memory_allocatable: int
    pod_capacity: int


@dataclass
class PodDensityInfo:
    """Pod density information per node"""

    node_name: str
    node_type: str
    running_pods: int
    failed_pods: int
    pending_pods: int
    total_pods: int
    pod_capacity: int
    cpu_capacity: float
    memory_capacity: int
    cpu_requests: float
    memory_requests: int
    pod_utilization_pct: float
    cpu_utilization_pct: float
    memory_utilization_pct: float
    approaching_limit: bool


@dataclass
class NamespaceEfficiencyInfo:
    """Namespace resource efficiency information"""

    namespace: str
    cpu_requests: float
    memory_requests: int
    cpu_waste_potential: float
    memory_waste_potential: int
    efficiency_score: float
    pod_count: int
    container_count: int


@dataclass
class SchedulingIssue:
    """Scheduling issue information"""

    pod_name: str
    namespace: str
    issue_type: Literal["PENDING", "FAILED", "OVER_CAPACITY"]
    reason: str
    node_name: str | None
    duration_minutes: int | None
    cpu_request: float
    memory_request: int


@dataclass
class AuditSnapshot:
    """Complete audit snapshot at a point in time"""

    timestamp: datetime
    nodes: list[NodeInfo]
    pods: list[PodInfo]
    cluster_stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "nodes": [asdict(node) for node in self.nodes],
            "pods": [asdict(pod) for pod in self.pods],
            "cluster_stats": self.cluster_stats,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditSnapshot":
        """Create from dict"""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            nodes=[NodeInfo(**node) for node in data["nodes"]],
            pods=[
                PodInfo(
                    name=pod["name"],
                    namespace=pod["namespace"],
                    node=pod["node"],
                    containers=[ContainerResources(**c) for c in pod["containers"]],
                )
                for pod in data["pods"]
            ],
            cluster_stats=data["cluster_stats"],
        )


class K8sResourceAuditor:
    """Main auditor class with historical tracking"""

    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now()

        # File paths
        self.snapshots_file = self.data_dir / "audit_snapshots.json"
        self.trends_file = self.data_dir / "trends.csv"

        # System namespaces to exclude from audit
        self.system_namespaces = {
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "default",
            "ingress-controller",
        }

    def is_system_namespace(self, namespace: str) -> bool:
        """Check if namespace should be excluded from audit"""
        return (
            namespace in self.system_namespaces
            or namespace.startswith("cattle-")
            or namespace.startswith("rancher-")
            or namespace.startswith("kube-")
        )

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            cmd = f"kubectl {command} -o json"
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True
            )
            return json.loads(result.stdout)  # type: ignore[no-any-return]
        except subprocess.CalledProcessError as e:
            print(f"❌ Error running kubectl: {e}")
            sys.exit(1)

    def parse_cpu(self, cpu_str: str) -> float:
        """Parse CPU string to millicores"""
        if not cpu_str or cpu_str == "0":
            return 0.0

        cpu_str = str(cpu_str).lower()
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1])
        if cpu_str.endswith("u"):
            return float(cpu_str[:-1]) / 1000
        return float(cpu_str) * 1000

    def parse_memory(self, memory_str: str) -> int:
        """Parse memory string to bytes"""
        if not memory_str or memory_str == "0":
            return 0

        memory_str = str(memory_str).upper()
        multipliers = {
            "KI": 1024,
            "K": 1000,
            "MI": 1024**2,
            "M": 1000**2,
            "GI": 1024**3,
            "G": 1000**3,
            "TI": 1024**4,
            "T": 1000**4,
        }

        for suffix, multiplier in multipliers.items():
            if memory_str.endswith(suffix):
                return int(float(memory_str[: -len(suffix)]) * multiplier)

        return int(memory_str) if memory_str.isdigit() else 0

    def get_node_type(self, node_name: str) -> str:
        """Determine node type from name"""
        if "monthly-b2-15" in node_name:
            return "monthly-b2-15"
        if "hourly-d2-8" in node_name:
            return "hourly-d2-8"
        return "unknown"

    def analyze_container(self, container: dict[str, Any]) -> ContainerResources:
        """Analyze single container resources"""
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})

        cpu_request = self.parse_cpu(requests.get("cpu", "0"))
        cpu_limit = self.parse_cpu(limits.get("cpu", "0"))
        memory_request = self.parse_memory(requests.get("memory", "0Ki"))
        memory_limit = self.parse_memory(limits.get("memory", "0Ki"))

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
            issues=issues,
        )

    def collect_cluster_data(self) -> AuditSnapshot:
        """Collect complete cluster state"""
        print("🔍 Collecting cluster data...")

        # Get nodes
        nodes_data = self.run_kubectl("get nodes")
        nodes: list[NodeInfo] = []

        for node in nodes_data["items"]:
            name = node["metadata"]["name"]
            capacity = node["status"]["capacity"]
            allocatable = node["status"]["allocatable"]

            nodes.append(
                NodeInfo(
                    name=name,
                    node_type=self.get_node_type(name),
                    cpu_capacity=self.parse_cpu(capacity.get("cpu", "0")),
                    memory_capacity=self.parse_memory(capacity.get("memory", "0Ki")),
                    cpu_allocatable=self.parse_cpu(allocatable.get("cpu", "0")),
                    memory_allocatable=self.parse_memory(
                        allocatable.get("memory", "0Ki")
                    ),
                    pod_capacity=int(capacity.get("pods", "0")),
                )
            )

        # Get pods
        pods_data = self.run_kubectl("get pods -A")
        pods: list[PodInfo] = []

        for pod in pods_data["items"]:
            # Skip system namespaces and only include running pods for resource analysis
            namespace = pod["metadata"]["namespace"]
            if pod["status"]["phase"] == "Running" and not self.is_system_namespace(namespace):
                containers = [
                    self.analyze_container(container)
                    for container in pod["spec"]["containers"]
                ]

                pods.append(
                    PodInfo(
                        name=pod["metadata"]["name"],
                        namespace=namespace,
                        node=pod["spec"].get("nodeName", "Unknown"),
                        containers=containers,
                    )
                )

        # Calculate cluster stats
        cluster_stats = self.calculate_cluster_stats(nodes, pods)

        return AuditSnapshot(
            timestamp=self.timestamp,
            nodes=nodes,
            pods=pods,
            cluster_stats=cluster_stats,
        )

    def calculate_cluster_stats(
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
            with open(self.snapshots_file) as f:
                existing_data = json.load(f)
                snapshots = existing_data.get("snapshots", [])

        # Add new snapshot
        snapshots.append(snapshot.to_dict())

        # Keep only last 30 snapshots
        snapshots = snapshots[-30:]

        # Save
        with open(self.snapshots_file, "w") as f:
            json.dump({"snapshots": snapshots}, f, indent=2)

        print(f"💾 Snapshot saved to {self.snapshots_file}")

    def load_snapshots(self) -> list[AuditSnapshot]:
        """Load historical snapshots"""
        if not self.snapshots_file.exists():
            return []

        with open(self.snapshots_file) as f:
            data = json.load(f)
            return [AuditSnapshot.from_dict(s) for s in data.get("snapshots", [])]

    def generate_reports(
        self, snapshot: AuditSnapshot, previous_snapshots: list[AuditSnapshot]
    ) -> None:
        """Generate comprehensive reports"""
        timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")

        print("📊 Generating CSV reports...")
        self.generate_pods_csv(snapshot, timestamp_str)
        self.generate_nodes_csv(snapshot, timestamp_str)
        self.generate_namespaces_csv(snapshot, timestamp_str)

        print("📈 Generating trend analysis...")
        self.generate_trends_csv(previous_snapshots + [snapshot])

        print("📋 Generating recommendations...")
        self.generate_recommendations_md(snapshot, previous_snapshots)

    def generate_pods_csv(self, snapshot: AuditSnapshot, timestamp: str) -> None:
        """Generate detailed pods report"""
        filename = self.data_dir / f"pods_detail_{timestamp}.csv"

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
                        "memory_request_mb": int(
                            container.memory_request / 1024 / 1024
                        ),
                        "memory_limit_mb": int(container.memory_limit / 1024 / 1024),
                        "cpu_ratio": f"{container.cpu_ratio:.1f}",
                        "memory_ratio": f"{container.memory_ratio:.1f}",
                        "issues": "|".join(container.issues),
                        "severity": container.severity,
                        "has_issues": len(container.issues) > 0,
                    }
                )

        df: pd.DataFrame = pd.DataFrame(rows)
        df.to_csv(filename, index=False)
        print(f"  → {filename}")

    def generate_nodes_csv(self, snapshot: AuditSnapshot, timestamp: str) -> None:
        """Generate node utilization report"""
        filename = self.data_dir / f"nodes_utilization_{timestamp}.csv"

        # Calculate per-node utilization
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
                    "cpu_request_ratio": f"{usage['cpu_requests'] / node.cpu_allocatable * 100:.1f}%"
                    if node.cpu_allocatable > 0
                    else "0%",
                    "cpu_limit_ratio": f"{usage['cpu_limits'] / node.cpu_allocatable * 100:.1f}%"
                    if node.cpu_allocatable > 0
                    else "0%",
                    "memory_request_ratio": f"{usage['memory_requests'] / node.memory_allocatable * 100:.1f}%"
                    if node.memory_allocatable > 0
                    else "0%",
                    "memory_limit_ratio": f"{usage['memory_limits'] / node.memory_allocatable * 100:.1f}%"
                    if node.memory_allocatable > 0
                    else "0%",
                    "issues_count": usage["issues_count"],
                    "pod_utilization": f"{usage['pod_count'] / node.pod_capacity * 100:.1f}%"
                    if node.pod_capacity > 0
                    else "0%",
                }
            )

        df: pd.DataFrame = pd.DataFrame(rows)
        df.to_csv(filename, index=False)
        print(f"  → {filename}")

    def generate_namespaces_csv(self, snapshot: AuditSnapshot, timestamp: str) -> None:
        """Generate namespace summary"""
        filename = self.data_dir / f"namespaces_summary_{timestamp}.csv"

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

            # Priority based on health score and resource usage
            if health_score < 50 or stats["cpu_limits"] > 10000:  # > 10 CPU cores
                priority = "HIGH"
            elif health_score < 80 or stats["cpu_limits"] > 5000:  # > 5 CPU cores
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

        df: pd.DataFrame = pd.DataFrame(rows)
        df = df.sort_values(["priority", "cpu_limits_m"], ascending=[False, False])
        df.to_csv(filename, index=False)
        print(f"  → {filename}")

    def generate_trends_csv(self, snapshots: list[AuditSnapshot]) -> None:
        """Generate trend analysis across snapshots"""
        if len(snapshots) < 2:
            return

        rows: list[dict[str, Any]] = []
        for snapshot in snapshots:
            rows.append(
                {
                    "timestamp": snapshot.timestamp.isoformat(),
                    "total_pods": snapshot.cluster_stats["total_pods"],
                    "total_containers": snapshot.cluster_stats["total_containers"],
                    "containers_with_issues": snapshot.cluster_stats[
                        "containers_with_issues"
                    ],
                    "issue_rate": f"{snapshot.cluster_stats['issue_rate'] * 100:.1f}%",
                    "cpu_requests_ratio": f"{snapshot.cluster_stats['resource_utilization']['cpu_requests_ratio'] * 100:.1f}%",
                    "cpu_limits_ratio": f"{snapshot.cluster_stats['resource_utilization']['cpu_limits_ratio'] * 100:.1f}%",
                    "memory_requests_ratio": f"{snapshot.cluster_stats['resource_utilization']['memory_requests_ratio'] * 100:.1f}%",
                    "memory_limits_ratio": f"{snapshot.cluster_stats['resource_utilization']['memory_limits_ratio'] * 100:.1f}%",
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

        df: pd.DataFrame = pd.DataFrame(rows)
        df.to_csv(self.trends_file, index=False)
        print(f"  → {self.trends_file}")

    def generate_recommendations_md(
        self, snapshot: AuditSnapshot, previous_snapshots: list[AuditSnapshot]
    ) -> None:
        """Generate actionable recommendations"""
        filename = (
            self.data_dir
            / f"recommendations_{snapshot.timestamp.strftime('%Y%m%d_%H%M%S')}.md"
        )

        stats = snapshot.cluster_stats

        with open(filename, "w") as f:
            f.write("# Kubernetes Resource Audit Report\n")
            f.write(
                f"**Generated**: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )

            # Executive Summary
            f.write("## 📊 Executive Summary\n\n")
            f.write("| Metric | Value |\n")
            f.write("|--------|-------|\n")
            f.write(f"| **Total Pods** | {stats['total_pods']} |\n")
            f.write(f"| **Total Containers** | {stats['total_containers']} |\n")
            f.write(
                f"| **Containers with Issues** | {stats['containers_with_issues']} ({stats['issue_rate'] * 100:.1f}%) |\n"
            )
            f.write(
                f"| **CPU Limits Overcommit** | {stats['resource_utilization']['cpu_limits_ratio'] * 100:.1f}% |\n"
            )
            f.write(
                f"| **Memory Limits Overcommit** | {stats['resource_utilization']['memory_limits_ratio'] * 100:.1f}% |\n\n"
            )

            # Severity Breakdown
            f.write("## 🚨 Issues by Severity\n\n")
            for severity, count in stats["severity_breakdown"].items():
                emoji = {
                    "CRITICAL": "🔴",
                    "HIGH": "🟠",
                    "MEDIUM": "🟡",
                    "LOW": "🟢",
                }.get(severity, "⚪")
                f.write(f"- {emoji} **{severity}**: {count} containers\n")
            f.write("\n")

            # Trends (if available)
            if previous_snapshots:
                f.write("## 📈 Trends\n\n")
                prev = previous_snapshots[-1]

                issue_trend = (
                    stats["containers_with_issues"]
                    - prev.cluster_stats["containers_with_issues"]
                )
                trend_emoji = (
                    "📈" if issue_trend > 0 else "📉" if issue_trend < 0 else "➡️"
                )

                f.write(
                    f"- **Issues trend**: {trend_emoji} {issue_trend:+d} since last audit\n"
                )
                f.write(
                    f"- **Pod growth**: {stats['total_pods'] - prev.cluster_stats['total_pods']:+d} pods\n\n"
                )

            # Top Priority Actions
            f.write("## 🎯 Priority Actions\n\n")

            if stats["severity_breakdown"].get("CRITICAL", 0) > 0:
                f.write(
                    f"### 🚨 URGENT: {stats['severity_breakdown']['CRITICAL']} containers without any limits\n"
                )
                f.write("Apply default LimitRange to namespaces:\n")
                f.write("```bash\n")
                f.write("kubectl apply -f - <<EOF\n")
                f.write("apiVersion: v1\n")
                f.write("kind: LimitRange\n")
                f.write("metadata:\n")
                f.write("  name: default-limits\n")
                f.write("spec:\n")
                f.write("  limits:\n")
                f.write("  - default:\n")
                f.write("      cpu: '200m'\n")
                f.write("      memory: '256Mi'\n")
                f.write("    defaultRequest:\n")
                f.write("      cpu: '100m'\n")
                f.write("      memory: '128Mi'\n")
                f.write("    type: Container\n")
                f.write("EOF\n")
                f.write("```\n\n")

            # Migration recommendations
            if stats["resource_utilization"]["cpu_limits_ratio"] > 1.5:
                f.write("### 🔄 Migration Impact\n")
                f.write(
                    f"Current CPU overcommit ({stats['resource_utilization']['cpu_limits_ratio'] * 100:.1f}%) will cause issues during migration.\n"
                )
                f.write("**Must fix** resource limits before node migration!\n\n")

            # Cleanup recommendations
            f.write("## 🧹 Maintenance Tasks\n\n")
            f.write("```bash\n")
            f.write("# Daily cleanup (add to cron)\n")
            f.write("kubectl delete pod --field-selector=status.phase==Failed -A\n")
            f.write("kubectl delete pod --field-selector=status.phase==Succeeded -A\n")
            f.write(
                "kubectl delete pod --field-selector=status.phase==Completed -A\n\n"
            )
            f.write("# Weekly audit\n")
            f.write("python3 resource_audit.py\n")
            f.write("```\n")

        print(f"  → {filename}")

    def analyze_pod_density(self, snapshot: AuditSnapshot) -> list[PodDensityInfo]:
        """Analyze pod density per node with capacity utilization"""
        all_pods_data = self.run_kubectl("get pods -A")

        # Count pods by status per node
        node_pod_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"running": 0, "failed": 0, "pending": 0, "total": 0}
        )

        for pod in all_pods_data["items"]:
            namespace = pod["metadata"]["namespace"]
            # Skip system namespaces
            if self.is_system_namespace(namespace):
                continue

            node_name = pod["spec"].get("nodeName", "Unknown")
            phase = pod["status"]["phase"]

            node_pod_stats[node_name]["total"] += 1
            if phase == "Running":
                node_pod_stats[node_name]["running"] += 1
            elif phase == "Failed":
                node_pod_stats[node_name]["failed"] += 1
            elif phase == "Pending":
                node_pod_stats[node_name]["pending"] += 1

        # Calculate resource usage per node
        node_usage: dict[str, dict[str, float]] = defaultdict(
            lambda: {"cpu_requests": 0.0, "memory_requests": 0}
        )

        for pod in snapshot.pods:
            if pod.node != "Unknown":
                node_usage[pod.node]["cpu_requests"] += pod.total_cpu_request
                node_usage[pod.node]["memory_requests"] += pod.total_memory_request

        density_info: list[PodDensityInfo] = []

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

            approaching_limit = pod_utilization > 90.0  # >90% of pod capacity

            density_info.append(
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
                    approaching_limit=approaching_limit,
                )
            )

        return density_info

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
        all_pods_data = self.run_kubectl("get pods -A")
        issues: list[SchedulingIssue] = []

        # Track resource usage per node
        node_usage: dict[str, dict[str, float]] = defaultdict(
            lambda: {"cpu_requests": 0.0, "memory_requests": 0, "pod_count": 0}
        )

        for pod in snapshot.pods:
            if pod.node != "Unknown":
                node_usage[pod.node]["cpu_requests"] += pod.total_cpu_request
                node_usage[pod.node]["memory_requests"] += pod.total_memory_request
                node_usage[pod.node]["pod_count"] += 1

        # Check for over-capacity nodes
        for node in snapshot.nodes:
            usage = node_usage[node.name]
            if usage["pod_count"] > node.pod_capacity:
                issues.append(
                    SchedulingIssue(
                        pod_name=f"node-{node.name}",
                        namespace="cluster",
                        issue_type="OVER_CAPACITY",
                        reason=f"Node has {usage['pod_count']} pods but capacity is {node.pod_capacity}",
                        node_name=node.name,
                        duration_minutes=None,
                        cpu_request=usage["cpu_requests"],
                        memory_request=int(usage["memory_requests"]),
                    )
                )

        # Check for pending and failed pods
        for pod in all_pods_data["items"]:
            phase = pod["status"]["phase"]
            pod_name = pod["metadata"]["name"]
            namespace = pod["metadata"]["namespace"]
            node_name = pod["spec"].get("nodeName")

            # Skip system namespaces
            if self.is_system_namespace(namespace):
                continue

            # Calculate pod resource requests
            cpu_request = 0.0
            memory_request = 0

            for container in pod["spec"]["containers"]:
                resources = container.get("resources", {})
                requests = resources.get("requests", {})
                cpu_request += self.parse_cpu(requests.get("cpu", "0"))
                memory_request += self.parse_memory(requests.get("memory", "0Ki"))

            if phase == "Pending":
                reason = "Unknown"
                if "status" in pod and "conditions" in pod["status"]:
                    for condition in pod["status"]["conditions"]:
                        if (
                            condition.get("type") == "PodScheduled"
                            and condition.get("status") == "False"
                        ):
                            reason = condition.get("reason", "Unknown")
                            break

                issues.append(
                    SchedulingIssue(
                        pod_name=pod_name,
                        namespace=namespace,
                        issue_type="PENDING",
                        reason=reason,
                        node_name=node_name,
                        duration_minutes=None,
                        cpu_request=cpu_request,
                        memory_request=memory_request,
                    )
                )

            elif phase == "Failed":
                reason = pod["status"].get("reason", "Unknown")
                issues.append(
                    SchedulingIssue(
                        pod_name=pod_name,
                        namespace=namespace,
                        issue_type="FAILED",
                        reason=reason,
                        node_name=node_name,
                        duration_minutes=None,
                        cpu_request=cpu_request,
                        memory_request=memory_request,
                    )
                )

        return issues

    def generate_extended_reports(self, snapshot: AuditSnapshot) -> None:
        """Generate extended operational metrics reports"""
        timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")

        print("📊 Generating extended operational reports...")

        # Pod density analysis
        density_info = self.analyze_pod_density(snapshot)
        self.generate_pod_density_csv(density_info, timestamp_str)

        # Namespace efficiency analysis
        efficiency_info = self.analyze_namespace_efficiency(snapshot)
        self.generate_namespace_efficiency_csv(efficiency_info, timestamp_str)

        # Scheduling issues detection
        scheduling_issues = self.detect_scheduling_issues(snapshot)
        self.generate_scheduling_issues_csv(scheduling_issues, timestamp_str)

    def generate_pod_density_csv(
        self, density_info: list[PodDensityInfo], timestamp: str
    ) -> None:
        """Generate pod density CSV report"""
        filename = self.data_dir / f"pod_density_{timestamp}.csv"

        rows: list[dict[str, Any]] = []
        for info in density_info:
            rows.append(
                {
                    "timestamp": self.timestamp.isoformat(),
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
        self, efficiency_info: list[NamespaceEfficiencyInfo], timestamp: str
    ) -> None:
        """Generate namespace efficiency CSV report"""
        filename = self.data_dir / f"namespace_efficiency_{timestamp}.csv"

        rows: list[dict[str, Any]] = []
        for info in efficiency_info:
            rows.append(
                {
                    "timestamp": self.timestamp.isoformat(),
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

        df = pd.DataFrame(rows)
        df.to_csv(filename, index=False)
        print(f"  → {filename}")

    def generate_scheduling_issues_csv(
        self, scheduling_issues: list[SchedulingIssue], timestamp: str
    ) -> None:
        """Generate scheduling issues CSV report"""
        filename = self.data_dir / f"scheduling_issues_{timestamp}.csv"

        rows: list[dict[str, Any]] = []
        for issue in scheduling_issues:
            rows.append(
                {
                    "timestamp": self.timestamp.isoformat(),
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
            self.generate_extended_reports(snapshot)
        else:
            # Generate standard reports
            self.generate_reports(snapshot, previous_snapshots)

        print(f"\n✅ Audit completed! Check {self.data_dir}/ for reports")
        print("📈 Run regularly to track trends and improvements")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Kubernetes Resource Audit Tool")
    parser.add_argument(
        "--mode",
        choices=["current", "extended"],
        default="current",
        help="Audit mode: current (standard reports) or extended (operational metrics)",
    )

    args = parser.parse_args()

    auditor = K8sResourceAuditor()
    auditor.run_audit(mode=args.mode)


if __name__ == "__main__":
    main()
