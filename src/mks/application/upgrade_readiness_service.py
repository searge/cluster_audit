"""Upgrade readiness service: how far the MKS cluster lags behind k8s releases."""

import csv
from pathlib import Path

from mks.application._ovh_session import ovh_session, resolve_kube_id
from mks.application._step_report import banner, info, ok, warn
from mks.config import OvhConfig
from mks.infrastructure.ovh_client import DEFAULT_MKS_PROJECT_ID, KubeCluster


def _write_csv(data_dir: str, cluster: KubeCluster) -> Path:
    out_path = Path(data_dir) / "upgrade_readiness.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["name", "version", "isUpToDate", "controlPlaneUpToDate", "nextUpgrades"]
        )
        writer.writerow(
            [
                cluster.name,
                cluster.version,
                cluster.is_up_to_date,
                cluster.control_plane_is_up_to_date,
                " ".join(cluster.next_upgrade_versions),
            ]
        )
    return out_path


def execute_upgrade_readiness(
    *,
    data_dir: str,
    ovh_config: OvhConfig,
    kube_id: str | None = None,
) -> str:
    """Report cluster version lag and available upgrades. Returns the CSV path."""
    project_id = ovh_config.project_id or DEFAULT_MKS_PROJECT_ID
    with ovh_session(ovh_config) as client:
        target_kube = resolve_kube_id(client, project_id, kube_id)
        banner(2, "Check cluster version")
        cluster = client.get_kube(project_id, target_kube)

    ok(f"cluster '{cluster.name}' on k8s {cluster.version}")
    if cluster.is_up_to_date and cluster.control_plane_is_up_to_date:
        ok("cluster is up to date")
    else:
        upgrades = ", ".join(cluster.next_upgrade_versions) or "none listed"
        cp_ok = cluster.control_plane_is_up_to_date
        warn(f"not up to date (control-plane up to date: {cp_ok})")
        info(f"available upgrades: {upgrades}")

    banner(3, "Write CSV")
    out_path = _write_csv(data_dir, cluster)
    ok(f"wrote {out_path}")
    return str(out_path)


__all__ = ["execute_upgrade_readiness"]
