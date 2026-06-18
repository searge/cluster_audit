"""Cluster inventory use-case."""

from mks.application.cluster_inventory_service import (
    execute_cluster_inventory as _service,
)
from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, run_kube_report

_SPEC = ReportSpec(
    capability="cluster-inventory",
    title="OVH MKS Cluster Inventory",
    temp_prefix="mks_cluster_inventory_",
    findings=("Control-plane version, region and node-pool billing mode captured.",),
)


def execute_cluster_inventory(
    *,
    reports_root: str | None = None,
    kube_id: str | None = None,
) -> RunResult | None:
    """Snapshot MKS control-plane state (cluster, node pools, nodes).

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSVs under ``reports/cluster-inventory/<YYYYMMDD>/``.
    """
    return run_kube_report(_SPEC, _service, reports_root=reports_root, kube_id=kube_id)


__all__ = ["execute_cluster_inventory"]
