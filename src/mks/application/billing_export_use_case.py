"""Billing export use-case."""

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mks.application.billing_export_service import (
    BillingExportParams,
)
from mks.application.billing_export_service import (
    execute_billing_export as _service,
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


def execute_billing_export(
    params: BillingExportParams,
    *,
    reports_root: str | None = None,
) -> RunResult | None:
    """Export the [OVH MKS] Payment CSV from the OVH billing API.

    Prints a rich step-by-step preview by default; use ``reports_root`` to
    persist the CSV under ``reports/billing/<YYYYMMDD>/``.
    """
    ovh_config = load_config().ovh
    if reports_root is None:
        render_stdout_with_tempdir(
            title="OVH MKS Billing Export",
            temp_prefix="mks_billing_export_",
            runner=lambda tmp_dir: _service(
                data_dir=tmp_dir, ovh_config=ovh_config, params=params
            ),
        )
        return None

    ctx = create_run(
        "billing",
        inputs=asdict(params),
        reports_root=reports_root,
        run_id=datetime.now(UTC).strftime("%Y%m%d"),
    )
    out_file = _service(
        data_dir=str(ctx.output_dir), ovh_config=ovh_config, params=params
    )
    return finalize_success_run(
        ctx,
        summary=SummaryContent(
            title="OVH MKS Billing Export",
            key_findings=[
                f"CSV generated: `{Path(out_file).name}`.",
                "Lines filtered to the MKS project domain and classified.",
            ],
            inputs=ctx.inputs,
        ),
    )


__all__ = ["execute_billing_export"]
