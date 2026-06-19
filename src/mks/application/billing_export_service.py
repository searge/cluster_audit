"""Billing export service: OVH billing API -> [OVH MKS] Payment CSV.

Rebuilds the payment sheet straight from the OVH billing API so monthly numbers
no longer have to be copied by hand. Each step prints an ``[ ok ]``/``[warn]``
line so a human can eyeball the run before trusting the CSV.

The MKS cluster is billed under one OVH Public Cloud project. Every billing line
for that project carries the project id as its ``domain`` field, which is how MKS
spend is separated from the dedicated servers and the RBX load balancer sharing
the account.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mks.application._ovh_session import require_project_id
from mks.application._step_report import banner as _banner
from mks.application._step_report import info as _info
from mks.application._step_report import ok as _ok
from mks.application._step_report import warn as _warn
from mks.config import OvhConfig
from mks.domain.billing import COLUMNS, classify, month_floor_iso, shift_month
from mks.infrastructure.ovh_client import (
    OvhApiError,
    OvhClient,
)


@dataclass(frozen=True)
class BillingExportParams:
    """Tuning options for a billing export run."""

    date_from: str = "2025-01"
    date_to: str | None = None
    month_offset: int = -1
    tolerance: float = 0.01
    project_id: str | None = None
    cache_dir: str = "cache/ovh_billing"
    cache_ttl_seconds: float | None = None


class _MonthBucket:
    """Mutable per-month accumulator (internal to the service, not a domain type)."""

    def __init__(self) -> None:
        self.columns: dict[str, float] = dict.fromkeys(COLUMNS, 0.0)
        self.bill_total = 0.0
        self.uncategorized: list[tuple[str, float]] = []

    def record(self, description: str, value: float) -> None:
        """Add one MKS billing line, classifying it to a column or UNMAPPED."""
        self.bill_total += value
        column = classify(description)
        if column is None:
            self.uncategorized.append((description, value))
        else:
            self.columns[column] += value

    def as_csv_row(self, label: str) -> list[str]:
        """Render the bucket as a CSV row in the sheet's column order."""
        row = [label]
        row.extend(
            f"{self.columns[col]:.2f}" if self.columns[col] else "" for col in COLUMNS
        )
        row.append(f"{self.bill_total:.2f}")
        return row

    @property
    def computed_total(self) -> float:
        """Return the sum of all classified column values."""
        return sum(self.columns.values())


def _resolve_window(date_from: str, date_to: str | None) -> tuple[str, str]:
    """Build the OVH query window; widen upper bound by +2 months.

    A bill is issued ~1 month after the consumption it covers, so the issue-date
    window must reach past the requested final consumption month.
    """
    from_year, from_month = (int(part) for part in date_from.split("-"))
    if date_to:
        to_year, to_month = (int(part) for part in date_to.split("-"))
    else:
        now = datetime.now(UTC)
        to_year, to_month = now.year, now.month
    end_idx = to_year * 12 + (to_month - 1) + 2
    end_year, end_month = divmod(end_idx, 12)
    return (
        month_floor_iso(from_year, from_month),
        month_floor_iso(end_year, end_month + 1),
    )


def _resolve_project(
    client: OvhClient, params: BillingExportParams, ovh_config: OvhConfig
) -> str:
    """Resolve and verify the MKS billing project id (STEP 2)."""
    _banner(2, "Resolve MKS project id (billing domain)")
    target_project = require_project_id(ovh_config, params.project_id)
    try:
        projects = client.list_projects()
    except OvhApiError as exc:
        _warn(f"could not list cloud projects ({exc}); trusting {target_project}")
        return target_project
    if not projects:
        _info(f"using project id {target_project}")
        return target_project
    if target_project not in projects:
        raise RuntimeError(
            f"project {target_project} not among account projects: {projects}"
        )
    description = client.get_project_description(target_project)
    _ok(f"project {target_project} found: '{description}'")
    return target_project


def _collect_buckets(
    client: OvhClient,
    bill_ids: list[str],
    target_project: str,
    month_offset: int,
) -> dict[str, _MonthBucket]:
    """Fetch bill details, filter to the MKS project, and bucket by month (STEP 4)."""
    _banner(4, "Fetch details, filter to MKS, classify lines")
    buckets: dict[str, _MonthBucket] = {}
    line_count = 0
    for line in client.fetch_bill_lines(bill_ids):
        if line.domain != target_project:
            continue
        label = shift_month(line.issue_date[:7] + "-01", month_offset)
        buckets.setdefault(label, _MonthBucket()).record(line.description, line.value)
        line_count += 1
    if not buckets:
        raise RuntimeError(f"no billing lines matched project {target_project}")
    _ok(f"{line_count} MKS lines across {len(buckets)} months")
    return buckets


def _verify(buckets: dict[str, _MonthBucket], tolerance: float) -> None:
    """Reconcile each month and surface unmapped lines (STEP 5)."""
    _banner(5, "Verify each month (computed vs OVH total, uncategorized lines)")
    all_clean = True
    for label in sorted(buckets):
        bucket = buckets[label]
        gap = abs(bucket.computed_total - bucket.bill_total)
        # Zero-euro lines (e.g. free-tier object-storage bandwidth) carry no cost
        # and would only add UNMAPPED noise, so they never flag a month.
        billed_unmapped = [(d, v) for d, v in bucket.uncategorized if v]
        flagged = bool(billed_unmapped) or gap > tolerance
        all_clean = all_clean and not flagged
        line = (
            f"{label}: computed={bucket.computed_total:8.2f}  "
            f"ovh={bucket.bill_total:8.2f}  gap={gap:5.2f}"
        )
        (_warn if flagged else _ok)(line)
        for description, value in billed_unmapped:
            _info(f"   UNMAPPED {value:8.2f} | {description[:60]}")
    if all_clean:
        _ok("all months reconcile and every line is mapped")
    else:
        _warn("review the [warn] months above before trusting the CSV")


def _write_csv(data_dir: str, buckets: dict[str, _MonthBucket]) -> Path:
    """Write the payment CSV in the sheet's column order (STEP 6)."""
    _banner(6, "Write CSV")
    out_path = Path(data_dir) / "ovh_mks_payment.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Month", *COLUMNS, "Total excl. VAT"])
        for label in sorted(buckets):
            writer.writerow(buckets[label].as_csv_row(label))
    _ok(f"wrote {out_path} ({len(buckets)} rows)")
    return out_path


def _print_summary(buckets: dict[str, _MonthBucket]) -> None:
    """Print a human-readable summary table (STEP 7)."""
    _banner(7, "Summary")
    print(
        f"  {'Month':<12}"
        + "".join(f"{c[:11]:>12}" for c in COLUMNS)
        + f"{'Total':>12}"
    )
    for label in sorted(buckets):
        bucket = buckets[label]
        cells = "".join(f"{bucket.columns[c]:>12.2f}" for c in COLUMNS)
        print(f"  {label:<12}{cells}{bucket.bill_total:>12.2f}")


def execute_billing_export(
    *,
    data_dir: str,
    ovh_config: OvhConfig,
    params: BillingExportParams,
) -> str:
    """Fetch OVH bills, classify MKS lines, and write the payment CSV.

    Returns the path of the written CSV. Raises ``OvhApiError`` on API/auth
    failure and ``RuntimeError`` when no MKS lines are found.
    """
    _banner(1, "Connect to OVH API & verify identity")
    with OvhClient(
        ovh_config,
        cache_dir=params.cache_dir,
        cache_ttl_seconds=params.cache_ttl_seconds,
    ) as client:
        identity = client.get_identity()
        _ok(f"authenticated as {identity.nichandle} (currency {identity.currency})")
        if identity.currency != "EUR":
            _warn(f"currency is {identity.currency}, sheet assumes EUR")

        target_project = _resolve_project(client, params, ovh_config)

        _banner(3, "List bills in range")
        window_from, window_to = _resolve_window(params.date_from, params.date_to)
        _info(f"window {window_from} .. {window_to}")
        bill_ids = client.list_bills(window_from, window_to)
        _ok(f"{len(bill_ids)} bills in window")
        if not bill_ids:
            raise RuntimeError("no bills in window; nothing to do")

        buckets = _collect_buckets(
            client, bill_ids, target_project, params.month_offset
        )

    _verify(buckets, params.tolerance)
    out_path = _write_csv(data_dir, buckets)
    _print_summary(buckets)
    return str(out_path)


__all__ = [
    "BillingExportParams",
    "execute_billing_export",
]
