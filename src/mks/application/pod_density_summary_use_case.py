"""Pod density summary use-case."""

from contextlib import redirect_stdout
from io import StringIO

from mks.application.pod_density_summary_service import (
    execute_pod_density_summary as _service,
)
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_pod_density_summary(*, reports_root: str | None = None) -> RunResult | None:
    """Execute pod density summary."""
    if reports_root is None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _service()
        render_stdout_report(
            title="Pod Density Summary",
            captured_stdout=buffer.getvalue(),
            output_files=(),
        )
        return None

    ctx = create_run("pod-density-summary", inputs={}, reports_root=reports_root)
    buffer = StringIO()
    with redirect_stdout(buffer):
        _service()
    summary = build_summary_lines(
        title="Pod Density Summary",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=(),
        key_findings=["Printed per-node pod density table to stdout."],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=(),
        summary_lines=summary,
    )


__all__ = ["execute_pod_density_summary"]
