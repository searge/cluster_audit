"""Deletion investigation use-case."""

from mks.application.deletion_investigation_service import (
    execute_deletion_investigation as _service,
)
from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)
from mks.config import load_config


def execute_deletion_investigation(
    namespaces_raw: str,
    *,
    skip_rancher: bool = False,
    reports_root: str | None = None,
) -> RunResult | None:
    """Execute deletion investigation."""
    if reports_root is None:
        config = load_config()
        render_stdout_with_tempdir(
            title="Deletion Investigation",
            temp_prefix="mks_deletion_investigation_",
            runner=lambda tmp_dir: _service(
                namespaces_raw,
                output_dir=tmp_dir,
                skip_rancher=skip_rancher,
                rancher_config=config.rancher,
            ),
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
    config = load_config()
    report_path = _service(
        namespaces_raw,
        output_dir=str(ctx.output_dir),
        skip_rancher=skip_rancher,
        rancher_config=config.rancher,
    )
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Deletion Investigation",
            key_findings=[f"Primary report file: `{report_path.name}`."],
            warnings=(
                ["Rancher correlation disabled (--skip-rancher)."]
                if skip_rancher
                else None
            ),
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_deletion_investigation"]
