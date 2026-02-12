"""RBAC audit use-case."""

from mks.application.rbac_audit_service import execute_rbac_audit as _service
from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)


def execute_rbac_audit(*, reports_root: str | None = None) -> RunResult | None:
    """Execute RBAC audit."""
    if reports_root is None:
        render_stdout_with_tempdir(
            title="RBAC Audit",
            temp_prefix="mks_rbac_audit_",
            runner=lambda tmp_dir: _service(data_dir=tmp_dir),
        )
        return None

    ctx = create_run("rbac-audit", inputs={}, reports_root=reports_root)
    _service(data_dir=str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="RBAC Audit",
            key_findings=[
                "Analyzed cluster and namespace bindings with project-like grouping.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_rbac_audit"]
