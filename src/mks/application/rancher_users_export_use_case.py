"""Rancher users export use-case."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mks.application.rancher_users_export_service import (
    execute_rancher_users_export as _service,
)
from mks.application.rancher_users_export_service import (
    execute_rancher_users_export_async as _service_async,
)
from mks.application.run_writer import (
    RunResult,
    build_summary_lines,
    create_run,
    finalize_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report


def execute_rancher_users_export(
    namespaces_raw: str,
    *,
    reports_root: str | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> RunResult | None:
    """Execute Rancher users export."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_rancher_users_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                _service(
                    namespaces_raw,
                    data_dir=tmp_dir,
                    cache_dir=cache_dir,
                    cache_ttl_seconds=cache_ttl_seconds,
                )
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Rancher Users Export",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run(
        "rancher-users-export",
        inputs={
            "namespaces": namespaces_raw,
            "cache_dir": cache_dir,
            "cache_ttl_seconds": cache_ttl_seconds,
        },
        reports_root=reports_root,
    )
    out_file = _service(
        namespaces_raw,
        data_dir=str(ctx.output_dir),
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Rancher Users Export",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            f"CSV generated: `{Path(out_file).name}`.",
            "Async requests + disk cache used for project/user lookups.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


async def execute_rancher_users_export_async(
    namespaces_raw: str,
    *,
    reports_root: str | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> RunResult | None:
    """Async variant of Rancher users export use-case."""
    if reports_root is None:
        with TemporaryDirectory(prefix="mks_rancher_users_async_") as tmp_dir:
            buffer = StringIO()
            with redirect_stdout(buffer):
                await _service_async(
                    namespaces_raw,
                    data_dir=tmp_dir,
                    cache_dir=cache_dir,
                    cache_ttl_seconds=cache_ttl_seconds,
                )
            output_files = list_output_files(Path(tmp_dir))
            render_stdout_report(
                title="Rancher Users Export (Async)",
                captured_stdout=buffer.getvalue(),
                output_files=output_files,
            )
        return None

    ctx = create_run(
        "rancher-users-export",
        inputs={
            "namespaces": namespaces_raw,
            "cache_dir": cache_dir,
            "cache_ttl_seconds": cache_ttl_seconds,
        },
        reports_root=reports_root,
    )
    out_file = await _service_async(
        namespaces_raw,
        data_dir=str(ctx.output_dir),
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    output_files = list_output_files(ctx.output_dir)
    summary = build_summary_lines(
        title="Rancher Users Export (Async)",
        capability=ctx.capability,
        inputs=ctx.inputs,
        output_files=output_files,
        key_findings=[
            f"CSV generated: `{Path(out_file).name}`.",
            "Async requests + disk cache used for project/user lookups.",
        ],
    )
    return finalize_run(
        ctx,
        status="success",
        output_files=output_files,
        summary_lines=summary,
    )


__all__ = [
    "execute_rancher_users_export",
    "execute_rancher_users_export_async",
]
