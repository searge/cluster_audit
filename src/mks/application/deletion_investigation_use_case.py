"""Deletion investigation use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.deletion_investigation_service import (
    execute_deletion_investigation as _service,
)
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_deletion_investigation(
    namespaces_raw: str,
    *,
    skip_rancher: bool = False,
    reports_root: str | None = None,
) -> RunResult | None:
    """Execute deletion investigation."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_deletion_investigation_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(
                    namespaces_raw,
                    output_dir=tmp_dir,
                    skip_rancher=skip_rancher,
                )
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Deletion Investigation",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run(
        "deletion-investigation",
        inputs={
            "namespaces": namespaces_raw,
            "skip_rancher": skip_rancher,
        },
        reports_root=reports_root,
    )
    report_path = _service(
        namespaces_raw,
        output_dir=str(ctx.output_dir),
        skip_rancher=skip_rancher,
    )
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Deletion Investigation",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[f"Primary report file: `{report_path.name}`."],
        warnings=(
            ["Rancher correlation disabled (--skip-rancher)."] if skip_rancher else None
        ),
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = ["execute_deletion_investigation"]
