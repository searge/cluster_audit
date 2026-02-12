"""Dashboard summary use-case."""

from mks.application.dashboard_summary_service import (
    execute_dashboard_summary as _service,
)
from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import finalize_success_run, render_stdout_only


def execute_dashboard_summary(*, reports_root: str | None = None) -> RunResult | None:
    """Execute dashboard summary."""
    if reports_root is None:
        render_stdout_only(
            title="Dashboard Summary", runner=lambda: _service(data_dir="reports")
        )
        return None

    ctx = create_run("dashboard-summary", inputs={}, reports_root=reports_root)
    _service(data_dir=reports_root)
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Dashboard Summary",
            key_findings=[
                f"Aggregated historical data from `{reports_root}` "
                "and printed summary.",
            ],
            inputs={"source_dir": reports_root},
        ),
    )


__all__ = ["execute_dashboard_summary"]
