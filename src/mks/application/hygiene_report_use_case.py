"""Hygiene report use-case."""

from mks.application.hygiene_report_service import (
    execute_hygiene_report as _service,
)
from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, execute_dated_report
from mks.config import load_config

_SPEC = ReportSpec(
    capability="hygiene-report",
    title="OVH MKS Hygiene Report",
    temp_prefix="mks_hygiene_report_",
    findings=(
        "Failed policy results joined with Rancher namespace owners "
        "(hygiene_report.jsonl contract).",
    ),
)


def execute_hygiene_report(
    *,
    reports_root: str | None = None,
    policy: str | None = None,
) -> RunResult | None:
    """Build the owners-enriched violations contract.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the files under ``reports/hygiene-report/<YYYYMMDD>/``.
    """
    config = load_config()
    return execute_dated_report(
        _SPEC,
        reports_root=reports_root,
        inputs={"policy": policy},
        run=lambda data_dir: _service(
            data_dir=data_dir,
            policy=policy,
            rancher_config=config.rancher,
        ),
    )


__all__ = ["execute_hygiene_report"]
