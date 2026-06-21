"""Capacity plan use-case."""

from mks.application.capacity_plan_service import (
    build_queries,
)
from mks.application.capacity_plan_service import (
    execute_capacity_plan as _service,
)
from mks.application.run_writer import RunResult
from mks.application.use_case_utils import ReportSpec, execute_dated_report
from mks.config import load_config

_SPEC = ReportSpec(
    capability="capacity-plan",
    title="OVH MKS Capacity Plan",
    temp_prefix="mks_capacity_plan_",
    findings=("Per-namespace requests vs peak usage captured for node sizing.",),
)


def _print_manual_queries(window: str) -> None:
    """Print the PromQL to run by hand when no Prometheus URL is configured."""
    print("PROMETHEUS_URL not set. Run these PromQL in Grafana/Prometheus manually:")
    for name, query in build_queries(window).items():
        print(f"\n# {name}\n{query}")


def execute_capacity_plan(
    *,
    reports_root: str | None = None,
    prometheus_url: str | None = None,
    window: str = "14d",
    verify_tls: bool = True,
) -> RunResult | None:
    """Report per-namespace requests vs peak usage from Prometheus.

    Uses ``PROMETHEUS_URL`` (or ``--prom-url``). With no URL it prints the PromQL
    to run by hand. Persist with ``reports_root`` -> ``reports/capacity-plan/<date>/``.
    """
    url = prometheus_url or load_config().prometheus_url
    if not url:
        _print_manual_queries(window)
        return None
    return execute_dated_report(
        _SPEC,
        reports_root=reports_root,
        inputs={"window": window},
        run=lambda data_dir: _service(
            data_dir=data_dir,
            prometheus_url=url,
            window=window,
            verify_tls=verify_tls,
        ),
    )


__all__ = ["execute_capacity_plan"]
