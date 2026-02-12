"""RBAC audit use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.rbac_audit_service import execute_rbac_audit as _service
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_rbac_audit(*, reports_root: str | None = None) -> RunResult | None:
    """Execute RBAC audit."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_rbac_audit_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(data_dir=tmp_dir)
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="RBAC Audit",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run("rbac-audit", inputs={}, reports_root=reports_root)
    _service(data_dir=str(ctx.output_dir))
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="RBAC Audit",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            "Analyzed cluster and namespace bindings with project-like grouping.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = ["execute_rbac_audit"]
