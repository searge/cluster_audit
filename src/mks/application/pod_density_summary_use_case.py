"""Pod density summary use-case."""

from mks.application.pod_density_summary_service import (
    execute_pod_density_summary as _service,
)
from mks.application.run_writer import (
    RunResult,
    SummaryContent,
    create_run,
)
from mks.application.use_case_utils import finalize_success_run, render_stdout_only


def execute_pod_density_summary(*, reports_root: str | None = None) -> RunResult | None:
    """Execute pod density summary."""
    if reports_root is None:
        render_stdout_only(title="Pod Density Summary", runner=_service)
        return None

    ctx = create_run("pod-density-summary", inputs={}, reports_root=reports_root)
    _service()
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Pod Density Summary",
            key_findings=["Printed per-node pod density table to stdout."],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_pod_density_summary"]
