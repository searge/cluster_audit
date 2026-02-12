"""Workload efficiency use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report
from mks.application.workload_efficiency_service import (
    execute_workload_efficiency_audit as _service,
)


def execute_workload_efficiency_audit(
    *,
    include_system: bool = False,
    reports_root: str | None = None,
) -> RunResult | None:
    """Execute workload efficiency audit."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_workload_efficiency_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(include_system=include_system, data_dir=tmp_dir)
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Workload Efficiency",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run(
        "workload-efficiency",
        inputs={"include_system": include_system},
        reports_root=reports_root,
    )
    _service(include_system=include_system, data_dir=str(ctx.output_dir))
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Workload Efficiency",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            "Computed per-workload request/usage efficiency and waste metrics.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = ["execute_workload_efficiency_audit"]
