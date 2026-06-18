"""Rightsizing use-case."""

from mks.application.rightsizing_service import execute_rightsizing as _service
from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, run_kube_report
from mks.config import load_prices

_SPEC = ReportSpec(
    capability="rightsizing",
    title="OVH MKS Rightsizing",
    temp_prefix="mks_rightsizing_",
    findings=("Per-pool cost and utilization captured with scale-down hints.",),
)


def execute_rightsizing(
    *,
    reports_root: str | None = None,
    kube_id: str | None = None,
) -> RunResult | None:
    """Report node-pool cost vs utilization with rightsizing recommendations.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSV under ``reports/rightsizing/<YYYYMMDD>/``.
    """
    prices = load_prices()
    return run_kube_report(
        _SPEC, _service, reports_root=reports_root, kube_id=kube_id, prices=prices
    )


__all__ = ["execute_rightsizing"]
