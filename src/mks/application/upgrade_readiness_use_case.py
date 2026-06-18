"""Upgrade readiness use-case."""

from mks.application.run_writer import RunResult
from mks.application.upgrade_readiness_service import (
    execute_upgrade_readiness as _service,
)
from mks.application.use_case_utils import ReportSpec, run_kube_report

_SPEC = ReportSpec(
    capability="upgrade-readiness",
    title="OVH MKS Upgrade Readiness",
    temp_prefix="mks_upgrade_readiness_",
    findings=("Cluster version lag and available k8s upgrades captured.",),
)


def execute_upgrade_readiness(
    *,
    reports_root: str | None = None,
    kube_id: str | None = None,
) -> RunResult | None:
    """Report how far the MKS cluster lags behind available k8s releases.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSV under ``reports/upgrade-readiness/<YYYYMMDD>/``.
    """
    return run_kube_report(_SPEC, _service, reports_root=reports_root, kube_id=kube_id)


__all__ = ["execute_upgrade_readiness"]
