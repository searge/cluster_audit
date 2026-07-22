"""Volume reconcile use-case."""

from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, run_ovh_dated_report
from mks.application.volume_reconcile_service import (
    execute_volume_reconcile as _service,
)
from mks.config import load_prices

_SPEC = ReportSpec(
    capability="volume-reconcile",
    title="OVH MKS Volume Reconcile",
    temp_prefix="mks_volume_reconcile_",
    findings=(
        "Detached volumes classified into deletion-safety waves "
        "(old-cluster / no-PV / released-retain / keep).",
    ),
)


def execute_volume_reconcile(*, reports_root: str | None = None) -> RunResult | None:
    """Reconcile detached volumes against cluster PVs into deletion waves.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSVs under ``reports/volume-reconcile/<YYYYMMDD>/``.
    """
    return run_ovh_dated_report(
        _SPEC, _service, reports_root=reports_root, prices=load_prices()
    )


__all__ = ["execute_volume_reconcile"]
