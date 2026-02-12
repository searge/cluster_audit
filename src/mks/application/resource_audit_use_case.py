"""Resource audit use-case."""

from mks.application.resource_audit_service import execute_resource_audit as _service
from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)


def execute_resource_audit(
    mode: str = "current",
    *,
    reports_root: str | None = None,
) -> RunResult | None:
    """Execute resource audit.

    When `reports_root` is None, runs in stdout-only mode and does not keep files.
    """
    if reports_root is None:
        render_stdout_with_tempdir(
            title="Resource Audit",
            temp_prefix="mks_resource_audit_",
            runner=lambda tmp_dir: _service(mode=mode, data_dir=tmp_dir),
        )
        return None

    ctx = create_run(
        "resource-audit",
        inputs={"mode": mode},
        reports_root=reports_root,
    )
    _service(mode=mode, data_dir=str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Resource Audit",
            key_findings=[
                "Collected cluster snapshot and generated resource audit artifacts.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_resource_audit"]
