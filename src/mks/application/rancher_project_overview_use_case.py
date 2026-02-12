"""Rancher project overview use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.rancher_project_overview_service import (
    execute_rancher_project_overview as _service,
)
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_rancher_project_overview(
    *, reports_root: str | None = None
) -> RunResult | None:
    """Execute Rancher project overview."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_rancher_overview_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(data_dir=tmp_dir)
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Rancher Project Overview",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run("rancher-project-overview", inputs={}, reports_root=reports_root)
    _service(data_dir=str(ctx.output_dir))
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Rancher Project Overview",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            "Mapped namespaces to Rancher projects and generated overview artifacts.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = ["execute_rancher_project_overview"]
