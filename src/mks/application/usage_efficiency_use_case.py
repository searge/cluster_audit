"""Usage efficiency use-case."""

from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.usage_efficiency_service import (
    execute_usage_efficiency_audit as _service,
)
from mks.application.use_case_utils import (
    finalize_success_run,
    render_stdout_with_tempdir,
)


def execute_usage_efficiency_audit(
    *, reports_root: str | None = None
) -> RunResult | None:
    """Execute usage efficiency audit."""
    if reports_root is None:
        render_stdout_with_tempdir(
            title="Usage Efficiency",
            temp_prefix="mks_usage_efficiency_",
            runner=lambda tmp_dir: _service(data_dir=tmp_dir),
        )
        return None

    ctx = create_run("usage-efficiency", inputs={}, reports_root=reports_root)
    _service(data_dir=str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Usage Efficiency",
            key_findings=[
                "Compared runtime metrics with requests/limits "
                "and produced recommendations.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_usage_efficiency_audit"]
