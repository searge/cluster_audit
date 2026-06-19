"""Waste scan use-case."""

from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, run_ovh_dated_report
from mks.application.waste_scan_service import execute_waste_scan as _service
from mks.config import load_prices

_SPEC = ReportSpec(
    capability="waste-scan",
    title="OVH MKS Waste Scan",
    temp_prefix="mks_waste_scan_",
    findings=("Detached volumes and unassociated floating IPs identified.",),
)


def execute_waste_scan(*, reports_root: str | None = None) -> RunResult | None:
    """Scan the project for orphan volumes and floating IPs.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSVs under ``reports/waste-scan/<YYYYMMDD>/``.
    """
    return run_ovh_dated_report(
        _SPEC, _service, reports_root=reports_root, prices=load_prices()
    )


__all__ = ["execute_waste_scan"]
