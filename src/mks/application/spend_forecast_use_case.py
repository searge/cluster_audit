"""Spend forecast use-case."""

from mks.application.run_writer import RunResult
from mks.application.spend_forecast_service import (
    execute_spend_forecast as _service,
)
from mks.application.use_case_utils import ReportSpec, run_ovh_dated_report

_SPEC = ReportSpec(
    capability="spend-forecast",
    title="OVH MKS Spend Forecast",
    temp_prefix="mks_spend_forecast_",
    findings=("Month-to-date and projected end-of-month project spend captured.",),
)


def execute_spend_forecast(*, reports_root: str | None = None) -> RunResult | None:
    """Report current-month and projected end-of-month OVH spend.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSV under ``reports/spend-forecast/<YYYYMMDD>/``.
    """
    return run_ovh_dated_report(_SPEC, _service, reports_root=reports_root)


__all__ = ["execute_spend_forecast"]
