"""Rancher project overview use-case."""

from mks.application.rancher_project_overview_service import (
    execute_rancher_project_overview as _service,
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


def execute_rancher_project_overview(
    *, reports_root: str | None = None
) -> RunResult | None:
    """Execute Rancher project overview."""
    if reports_root is None:
        render_stdout_with_tempdir(
            title="Rancher Project Overview",
            temp_prefix="mks_rancher_overview_",
            runner=lambda tmp_dir: _service(data_dir=tmp_dir),
        )
        return None

    ctx = create_run("rancher-project-overview", inputs={}, reports_root=reports_root)
    _service(data_dir=str(ctx.output_dir))
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="Rancher Project Overview",
            key_findings=[
                "Mapped namespaces to Rancher projects "
                "and generated overview artifacts.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_rancher_project_overview"]
