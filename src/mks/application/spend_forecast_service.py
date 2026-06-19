"""Spend forecast service: OVH usage API -> month-to-date + projected spend.

`billing-export` reports the past (issued bills). This reports the live current
month: spend so far and OVH's projected end-of-month total.
"""

import csv
from pathlib import Path

from mks.application._ovh_session import ovh_session, require_project_id
from mks.application._step_report import banner, info, ok
from mks.config import OvhConfig
from mks.infrastructure.ovh_client import UsageSnapshot


def _write_csv(data_dir: str, current: UsageSnapshot, forecast: UsageSnapshot) -> Path:
    out_path = Path(data_dir) / "spend_forecast.csv"
    remaining = forecast.total_price - current.total_price
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value_eur"])
        writer.writerow(["month_to_date", f"{current.total_price:.2f}"])
        writer.writerow(["forecast_end_of_month", f"{forecast.total_price:.2f}"])
        writer.writerow(["projected_remaining", f"{remaining:.2f}"])
    return out_path


def execute_spend_forecast(*, data_dir: str, ovh_config: OvhConfig) -> str:
    """Fetch current-month and forecast project spend and write a CSV.

    Returns the CSV path. Raises ``OvhApiError`` on API/auth failure.
    """
    project_id = require_project_id(ovh_config)
    # Usage is live data, so no disk cache here.
    with ovh_session(ovh_config) as client:
        banner(2, "Fetch current-month and forecast usage")
        current = client.get_usage_current(project_id)
        forecast = client.get_usage_forecast(project_id)

    ok(f"period {current.period_from[:10]} .. {current.period_to[:10]}")
    info(f"month-to-date:        {current.total_price:9.2f} EUR")
    info(f"forecast end-of-month:{forecast.total_price:9.2f} EUR")
    info(f"projected remaining:  {forecast.total_price - current.total_price:9.2f} EUR")

    banner(3, "Write CSV")
    out_path = _write_csv(data_dir, current, forecast)
    ok(f"wrote {out_path}")
    return str(out_path)


__all__ = ["execute_spend_forecast"]
