"""Workload efficiency use-case."""

from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)
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
        render_stdout_with_tempdir(
            title="Workload Efficiency",
            temp_prefix="mks_workload_efficiency_",
            runner=lambda tmp_dir: _service(
                include_system=include_system,
                data_dir=tmp_dir,
            ),
        )
        return None

    ctx = create_run(
        "workload-efficiency",
        inputs={"include_system": include_system},
        reports_root=reports_root,
    )
    _service(include_system=include_system, data_dir=str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Workload Efficiency",
            key_findings=[
                "Computed per-workload request/usage efficiency and waste metrics.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_workload_efficiency_audit"]
