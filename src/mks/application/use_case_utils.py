"""Shared helpers for use-case stdout mode and run finalization."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mks.application.run_writer import (
    RunContext,
    RunResult,
    SummaryContent,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report
from mks.config import load_config


def write_csv(path: Path, header: Sequence[Any], rows: Iterable[Sequence[Any]]) -> Path:
    """Write a UTF-8 CSV with a header row, returning the path."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


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


@dataclass(frozen=True)
class ReportSpec:
    """Static metadata for a dated-report capability."""

    capability: str
    title: str
    temp_prefix: str
    findings: tuple[str, ...]


def execute_dated_report(
    spec: ReportSpec,
    *,
    reports_root: str | None,
    run: Callable[[str], Any],
    inputs: dict[str, Any] | None = None,
) -> RunResult | None:
    """Run a capability in stdout or persisted mode under a dated run id.

    ``run`` receives the output directory and produces the artifacts. In stdout
    mode (``reports_root is None``) nothing is kept; otherwise artifacts land in
    ``reports/<capability>/<YYYYMMDD>/`` with a standardized summary.
    """
    if reports_root is None:
        render_stdout_with_tempdir(
            title=spec.title, temp_prefix=spec.temp_prefix, runner=run
        )
        return None
    ctx = create_run(
        spec.capability,
        inputs=inputs or {},
        reports_root=reports_root,
        run_id=datetime.now(UTC).strftime("%Y%m%d"),
    )
    run(str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title=spec.title, key_findings=list(spec.findings), inputs=ctx.inputs
        ),
    )


def run_ovh_dated_report(
    spec: ReportSpec,
    service: Callable[..., Any],
    *,
    reports_root: str | None,
    inputs: dict[str, Any] | None = None,
    **service_kwargs: Any,
) -> RunResult | None:
    """Load OVH config and run an OVH-API capability as a dated report.

    ``service`` is called ``service(data_dir=..., ovh_config=..., **service_kwargs)``.
    """
    ovh_config = load_config().ovh
    return execute_dated_report(
        spec,
        reports_root=reports_root,
        inputs=inputs,
        run=lambda data_dir: service(
            data_dir=data_dir, ovh_config=ovh_config, **service_kwargs
        ),
    )


def run_kube_report(
    spec: ReportSpec,
    service: Callable[..., Any],
    *,
    reports_root: str | None,
    kube_id: str | None,
    **service_kwargs: Any,
) -> RunResult | None:
    """Run a per-cluster OVH report, threading ``kube_id`` to service and inputs."""
    return run_ovh_dated_report(
        spec,
        service,
        reports_root=reports_root,
        inputs={"kube_id": kube_id},
        kube_id=kube_id,
        **service_kwargs,
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
