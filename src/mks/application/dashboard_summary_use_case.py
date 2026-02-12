"""Dashboard summary use-case."""

from contextlib import redirect_stdout
from io import StringIO

from mks.application.dashboard_summary_service import (
    execute_dashboard_summary as _service,
)
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_dashboard_summary(*, reports_root: str | None = None) -> RunResult | None:
    """Execute dashboard summary."""
    if reports_root is None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _service(data_dir="reports")
        render_stdout_report(
            title="Dashboard Summary",
            captured_stdout=buffer.getvalue(),
            output_files=(),
        )
        return None

    ctx = create_run("dashboard-summary", inputs={}, reports_root=reports_root)
    _service(data_dir=reports_root)
    summary = build_summary_lines(
        title="Dashboard Summary",
        capability=ctx.capability,
        inputs={"source_dir": reports_root},
        output_files=(),
        key_findings=[
            f"Aggregated historical data from `{reports_root}` and printed summary.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=(),
        summary_lines=summary,
    )


__all__ = ["execute_dashboard_summary"]
