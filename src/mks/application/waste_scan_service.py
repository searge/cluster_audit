"""Waste scan service: find OVH resources that cost money but serve nothing.

Detached block-storage volumes (released PVCs that were never cleaned up) and
unassociated floating IPs both keep billing while doing no work.
"""

from pathlib import Path

from mks.application._ovh_session import ovh_session, require_project_id
from mks.application._step_report import banner, info, ok, warn
from mks.application.use_case_utils import write_csv
from mks.config import OvhConfig, Prices
from mks.infrastructure.ovh_client import (
    FloatingIp,
    OvhApiError,
    OvhClient,
    VolumeInfo,
)


def _find_orphan_floating_ips(client: OvhClient, project_id: str) -> list[FloatingIp]:
    """Collect unassociated floating IPs across all project regions.

    Regions where the floating-IP service is not activated return 404; those are
    skipped rather than aborting the whole scan.
    """
    orphans: list[FloatingIp] = []
    for region in client.list_regions(project_id):
        try:
            ips = client.list_floating_ips(project_id, region)
        except OvhApiError:
            continue
        orphans.extend(ip for ip in ips if not ip.associated)
    return orphans


def _write_volumes_csv(
    data_dir: str, volumes: list[VolumeInfo], eur_per_gb: float
) -> Path:
    header = [
        "id",
        "name",
        "region",
        "sizeGB",
        "monthlyCostEUR",
        "status",
        "creationDate",
    ]
    rows = [
        [
            vol.id,
            vol.name,
            vol.region,
            vol.size_gb,
            f"{vol.size_gb * eur_per_gb:.2f}",
            vol.status,
            vol.creation_date,
        ]
        for vol in sorted(volumes, key=lambda v: v.creation_date)
    ]
    return write_csv(Path(data_dir) / "orphan_volumes.csv", header, rows)


def _write_floating_ips_csv(data_dir: str, ips: list[FloatingIp]) -> Path:
    rows = [
        [ip.id, ip.ip, ip.region, ip.status] for ip in sorted(ips, key=lambda i: i.ip)
    ]
    return write_csv(
        Path(data_dir) / "orphan_floating_ips.csv",
        ["id", "ip", "region", "status"],
        rows,
    )


def execute_waste_scan(*, data_dir: str, ovh_config: OvhConfig, prices: Prices) -> str:
    """Scan a project for orphan volumes and floating IPs and write CSVs.

    Returns the orphan-volumes CSV path. Raises ``OvhApiError`` on API failure.
    """
    project_id = require_project_id(ovh_config)
    eur_per_gb = prices.volume_high_speed_eur_per_gb_month
    # Live inventory, so no disk cache here.
    with ovh_session(ovh_config) as client:
        banner(2, "Scan block-storage volumes")
        volumes = client.list_volumes(project_id)
        orphan_volumes = [v for v in volumes if not v.attached]
        ok(f"{len(volumes)} volumes, {len(orphan_volumes)} detached")

        banner(3, "Scan floating IPs")
        orphan_ips = _find_orphan_floating_ips(client, project_id)
        ok(f"{len(orphan_ips)} unassociated floating IPs")

    vols_path = _write_volumes_csv(data_dir, orphan_volumes, eur_per_gb)
    _write_floating_ips_csv(data_dir, orphan_ips)

    banner(4, "Summary")
    orphan_gb = sum(v.size_gb for v in orphan_volumes)
    orphan_cost = orphan_gb * eur_per_gb
    if orphan_volumes:
        warn(
            f"{len(orphan_volumes)} detached volumes, {orphan_gb} GB unused "
            f"(~{orphan_cost:.2f} EUR/mo at {eur_per_gb:.3f}/GB)"
        )
    else:
        ok("no detached volumes")
    if orphan_ips:
        warn(f"{len(orphan_ips)} unassociated floating IPs")
    else:
        ok("no unassociated floating IPs")
    info("detached volumes still bill as high-speed disk; release or reattach them")
    return str(vols_path)


__all__ = ["execute_waste_scan"]
