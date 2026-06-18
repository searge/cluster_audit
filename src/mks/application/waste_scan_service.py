"""Waste scan service: find OVH resources that cost money but serve nothing.

Detached block-storage volumes (released PVCs that were never cleaned up) and
unassociated floating IPs both keep billing while doing no work.
"""

import csv
from pathlib import Path

from mks.application._ovh_session import ovh_session
from mks.application._step_report import banner, info, ok, warn
from mks.config import OvhConfig
from mks.infrastructure.ovh_client import (
    DEFAULT_MKS_PROJECT_ID,
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


def _write_volumes_csv(data_dir: str, volumes: list[VolumeInfo]) -> Path:
    out_path = Path(data_dir) / "orphan_volumes.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "region", "sizeGB", "status", "creationDate"])
        for vol in sorted(volumes, key=lambda v: v.creation_date):
            writer.writerow(
                [
                    vol.id,
                    vol.name,
                    vol.region,
                    vol.size_gb,
                    vol.status,
                    vol.creation_date,
                ]
            )
    return out_path


def _write_floating_ips_csv(data_dir: str, ips: list[FloatingIp]) -> Path:
    out_path = Path(data_dir) / "orphan_floating_ips.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "ip", "region", "status"])
        for ip in sorted(ips, key=lambda i: i.ip):
            writer.writerow([ip.id, ip.ip, ip.region, ip.status])
    return out_path


def execute_waste_scan(*, data_dir: str, ovh_config: OvhConfig) -> str:
    """Scan a project for orphan volumes and floating IPs and write CSVs.

    Returns the orphan-volumes CSV path. Raises ``OvhApiError`` on API failure.
    """
    project_id = ovh_config.project_id or DEFAULT_MKS_PROJECT_ID
    # Live inventory, so no disk cache here.
    with ovh_session(ovh_config) as client:
        banner(2, "Scan block-storage volumes")
        volumes = client.list_volumes(project_id)
        orphan_volumes = [v for v in volumes if not v.attached]
        ok(f"{len(volumes)} volumes, {len(orphan_volumes)} detached")

        banner(3, "Scan floating IPs")
        orphan_ips = _find_orphan_floating_ips(client, project_id)
        ok(f"{len(orphan_ips)} unassociated floating IPs")

    vols_path = _write_volumes_csv(data_dir, orphan_volumes)
    _write_floating_ips_csv(data_dir, orphan_ips)

    banner(4, "Summary")
    orphan_gb = sum(v.size_gb for v in orphan_volumes)
    if orphan_volumes:
        warn(f"{len(orphan_volumes)} detached volumes, {orphan_gb} GB unused")
    else:
        ok("no detached volumes")
    if orphan_ips:
        warn(f"{len(orphan_ips)} unassociated floating IPs")
    else:
        ok("no unassociated floating IPs")
    info("detached volumes still bill as high-speed disk; release or reattach them")
    return str(vols_path)


__all__ = ["execute_waste_scan"]
