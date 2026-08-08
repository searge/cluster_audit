"""Shared capacity types: requests vs observed peak usage.

Used by both the report capability (`capacity-plan`, CSV) and the ingest
capability (`capacity-ingest`, Postgres), so they cannot drift apart. Pure
data, no IO.
"""

from dataclasses import dataclass
from datetime import datetime

from mks.domain.cost import CostBasis


@dataclass(frozen=True)
class NsDemand:
    """Per-namespace requests vs observed peak usage."""

    namespace: str
    cpu_req: float  # cores
    cpu_p95: float  # cores
    cpu_max: float  # cores; burst signal — p95-based quota unsafe when cpu_max >> p95
    mem_req_gb: float
    mem_max_gb: float
    # Rancher project the namespace belongs to. Only the opaque id is available
    # from a downstream cluster; None for namespaces Rancher does not manage.
    project_id: str | None = None

    @property
    def cpu_phantom(self) -> float:
        """Reserved-but-unused CPU: what a right-sizing pass would reclaim."""
        return self.cpu_req - self.cpu_p95


@dataclass(frozen=True)
class ClusterTotals:
    """Cluster-wide requests vs observed peak, plus the node count they drive."""

    nodes: float
    cpu_req: float  # cores
    cpu_p95: float  # cores
    mem_req_gb: float
    mem_max_gb: float


@dataclass(frozen=True)
class MemSpiker:
    """A pod's peak memory over the window — the OOM-risk signal."""

    namespace: str
    pod: str
    mem_max_gb: float


@dataclass(frozen=True)
class CapacitySnapshot:
    """One complete observation of the cluster: totals plus the detail rows."""

    cluster: str
    taken_at: datetime
    window: str
    totals: ClusterTotals
    namespaces: tuple[NsDemand, ...]
    spikers: tuple[MemSpiker, ...]
    # None when no node flavour could be priced, in which case the snapshot is
    # stored without euro figures rather than with invented ones.
    cost: CostBasis | None = None


__all__ = ["CapacitySnapshot", "ClusterTotals", "MemSpiker", "NsDemand"]
