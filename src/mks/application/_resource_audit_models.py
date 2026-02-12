"""Data models for Kubernetes resource audit snapshots and reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, NamedTuple


@dataclass
class ContainerResources:
    """Container resource specification."""

    name: str
    cpu_request: float
    cpu_limit: float
    memory_request: int
    memory_limit: int
    issues: list[str]

    @property
    def cpu_ratio(self) -> float:
        """CPU limit/request ratio."""
        return self.cpu_limit / self.cpu_request if self.cpu_request > 0 else 0

    @property
    def memory_ratio(self) -> float:
        """Memory limit/request ratio."""
        return self.memory_limit / self.memory_request if self.memory_request > 0 else 0

    @property
    def severity(self) -> str:
        """Issue severity level."""
        if any("NO_" in issue and "RESOURCES" in issue for issue in self.issues):
            return "CRITICAL"
        if any("HIGH_" in issue and "RATIO" in issue for issue in self.issues):
            return "HIGH"
        if any("NO_" in issue for issue in self.issues):
            return "MEDIUM"
        return "LOW"


@dataclass
class PodInfo:
    """Pod information with containers."""

    name: str
    namespace: str
    node: str
    containers: list[ContainerResources]

    @property
    def total_cpu_request(self) -> float:
        """Return total CPU requests across pod containers in millicores."""
        return sum(c.cpu_request for c in self.containers)

    @property
    def total_cpu_limit(self) -> float:
        """Return total CPU limits across pod containers in millicores."""
        return sum(c.cpu_limit for c in self.containers)

    @property
    def total_memory_request(self) -> int:
        """Return total memory requests across pod containers in bytes."""
        return sum(c.memory_request for c in self.containers)

    @property
    def total_memory_limit(self) -> int:
        """Return total memory limits across pod containers in bytes."""
        return sum(c.memory_limit for c in self.containers)

    @property
    def has_issues(self) -> bool:
        """Return whether any container inside the pod has detected issues."""
        return any(c.issues for c in self.containers)


@dataclass
class NodeInfo:
    """Node capacity and type information."""

    name: str
    node_type: str
    cpu_capacity: float
    memory_capacity: int
    cpu_allocatable: float
    memory_allocatable: int
    pod_capacity: int


class PodDensityInfo(NamedTuple):
    """Pod density information per node."""

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


class NamespaceEfficiencyInfo(NamedTuple):
    """Namespace resource efficiency information."""

    namespace: str
    cpu_requests: float
    memory_requests: int
    cpu_waste_potential: float
    memory_waste_potential: int
    efficiency_score: float
    pod_count: int
    container_count: int


class SchedulingIssue(NamedTuple):
    """Scheduling issue information."""

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
    """Complete audit snapshot at a point in time."""

    timestamp: datetime
    nodes: list[NodeInfo]
    pods: list[PodInfo]
    cluster_stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "nodes": [asdict(node) for node in self.nodes],
            "pods": [asdict(pod) for pod in self.pods],
            "cluster_stats": self.cluster_stats,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditSnapshot:
        """Create from dict."""
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
