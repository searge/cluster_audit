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
    SummaryContent,
    create_run,
    list_output_files,
)
from mks.application.stdout_renderer import render_stdout_report
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)


def execute_rancher_users_export(
    namespaces_raw: str,
    *,
    reports_root: str | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> RunResult | None:
    """Execute Rancher users export."""
    if reports_root is None:
        render_stdout_with_tempdir(
            title="Rancher Users Export",
            temp_prefix="mks_rancher_users_",
            runner=lambda tmp_dir: _service(
                namespaces_raw,
                data_dir=tmp_dir,
                cache_dir=cache_dir,
                cache_ttl_seconds=cache_ttl_seconds,
            ),
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
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Rancher Users Export",
            key_findings=[
                f"CSV generated: `{Path(out_file).name}`.",
                "Async requests + disk cache used for project/user lookups.",
            ],
            inputs=ctx.inputs,
        ),
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
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Rancher Users Export (Async)",
            key_findings=[
                f"CSV generated: `{Path(out_file).name}`.",
                "Async requests + disk cache used for project/user lookups.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = [
    "execute_rancher_users_export",
    "execute_rancher_users_export_async",
]
