"""Resource audit use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.resource_audit_service import execute_resource_audit as _service
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_resource_audit(
    mode: str = "current",
    *,
    reports_root: str | None = None,
) -> RunResult | None:
    """Execute resource audit.

    When `reports_root` is None, runs in stdout-only mode and does not keep files.
    """
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_resource_audit_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(mode=mode, data_dir=tmp_dir)
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Resource Audit",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run(
        "resource-audit",
        inputs={"mode": mode},
        reports_root=reports_root,
    )
    _service(mode=mode, data_dir=str(ctx.output_dir))
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Resource Audit",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            "Collected cluster snapshot and generated resource audit artifacts.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = ["execute_resource_audit"]
