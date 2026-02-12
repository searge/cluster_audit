"""Shared helpers for use-case stdout mode and run finalization."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mks.application.run_writer import (
    RunContext,
    RunResult,
    SummaryContent,
    build_summary_lines,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def render_stdout_with_tempdir(
    *,
    title: str,
    temp_prefix: str,
    runner: Callable[[str], Any],
) -> None:
    """Execute runner in temporary directory and render artifacts to stdout."""
    with TemporaryDirectory(prefix=temp_prefix) as tmp_dir:
        buffer = StringIO()
        with redirect_stdout(buffer):
            runner(tmp_dir)
        render_stdout_report(
            title=title,
            captured_stdout=buffer.getvalue(),
            output_files=list_output_files(Path(tmp_dir)),
        )


def render_stdout_only(*, title: str, runner: Callable[[], None]) -> None:
    """Execute runner and render stdout without artifacts."""
    buffer = StringIO()
    with redirect_stdout(buffer):
        runner()
    render_stdout_report(
        title=title,
        captured_stdout=buffer.getvalue(),
        output_files=(),
    )


def finalize_success_run(
    ctx: RunContext,
    *,
    summary: SummaryContent,
) -> RunResult:
    """Finalize successful run and write standardized summary."""
    output_files = list_output_files(ctx.output_dir)
    lines = build_summary_lines(
        summary, capability=ctx.capability, output_files=output_files
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=lines,
    )
