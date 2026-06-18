"""Cluster inventory service: OVH MKS control-plane truth -> CSV.

kubectl sees the cluster from the inside. The OVH API knows what kubectl cannot:
control-plane version and region, node-pool billing mode (monthly forfait vs
hourly) and autoscaling bounds. This capability snapshots that.
"""

import csv
from pathlib import Path

from mks.application._ovh_session import ovh_session, resolve_kube_id
from mks.application._step_report import banner, info, ok, warn
from mks.config import OvhConfig
from mks.infrastructure.ovh_client import (
    DEFAULT_MKS_PROJECT_ID,
    KubeCluster,
    KubeNode,
    NodePool,
)


def _report_cluster(cluster: KubeCluster) -> None:
    """Print and flag cluster-level state (STEP 2)."""
    ok(f"cluster '{cluster.name}' ({cluster.id})")
    info(f"region={cluster.region}  version={cluster.version}  plan={cluster.plan}")
    info(f"status={cluster.status}")
    if not cluster.is_up_to_date or not cluster.control_plane_is_up_to_date:
        upgrades = ", ".join(cluster.next_upgrade_versions) or "none listed"
        warn(f"cluster not up to date; available upgrades: {upgrades}")
    else:
        ok("cluster is up to date")


def _write_cluster_csv(data_dir: str, cluster: KubeCluster) -> Path:
    out_path = Path(data_dir) / "cluster.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id",
                "name",
                "region",
                "version",
                "status",
                "plan",
                "isUpToDate",
                "controlPlaneUpToDate",
                "nextUpgrades",
            ]
        )
        writer.writerow(
            [
                cluster.id,
                cluster.name,
                cluster.region,
                cluster.version,
                cluster.status,
                cluster.plan,
                cluster.is_up_to_date,
                cluster.control_plane_is_up_to_date,
                " ".join(cluster.next_upgrade_versions),
            ]
        )
    return out_path


def _write_nodepools_csv(data_dir: str, pools: list[NodePool]) -> Path:
    out_path = Path(data_dir) / "nodepools.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "name",
                "flavor",
                "billing",
                "autoscale",
                "minNodes",
                "maxNodes",
                "desiredNodes",
                "currentNodes",
                "availableNodes",
                "upToDateNodes",
                "status",
            ]
        )
        for pool in pools:
            writer.writerow(
                [
                    pool.name,
                    pool.flavor,
                    "monthly" if pool.monthly_billed else "hourly",
                    pool.autoscale,
                    pool.min_nodes,
                    pool.max_nodes,
                    pool.desired_nodes,
                    pool.current_nodes,
                    pool.available_nodes,
                    pool.up_to_date_nodes,
                    pool.status,
                ]
            )
    return out_path


def _write_nodes_csv(data_dir: str, nodes: list[KubeNode]) -> Path:
    out_path = Path(data_dir) / "nodes.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "flavor", "status", "version", "isUpToDate"])
        for node in sorted(nodes, key=lambda n: n.name):
            writer.writerow(
                [node.name, node.flavor, node.status, node.version, node.is_up_to_date]
            )
    return out_path


def _print_summary(pools: list[NodePool], nodes: list[KubeNode]) -> None:
    """Print a node-pool summary table (STEP 4)."""
    banner(4, "Summary")
    header = (
        f"  {'Node pool':<28}{'flavor':>8}{'billing':>10}{'cur/des':>10}{'min/max':>10}"
    )
    print(header)
    for pool in pools:
        billing = "monthly" if pool.monthly_billed else "hourly"
        cur_des = f"{pool.current_nodes}/{pool.desired_nodes}"
        min_max = f"{pool.min_nodes}/{pool.max_nodes}"
        print(
            f"  {pool.name:<28}{pool.flavor:>8}{billing:>10}{cur_des:>10}{min_max:>10}"
        )
    print(f"\n  total nodes: {len(nodes)}")


def execute_cluster_inventory(
    *,
    data_dir: str,
    ovh_config: OvhConfig,
    kube_id: str | None = None,
) -> str:
    """Snapshot an MKS cluster's control-plane state and write CSV artifacts.

    Returns the node-pools CSV path. Raises ``OvhApiError`` on API/auth failure
    and ``RuntimeError`` when no cluster is found.
    """
    project_id = ovh_config.project_id or DEFAULT_MKS_PROJECT_ID
    # MKS state is mutable (nodes scale), so no disk cache here.
    with ovh_session(ovh_config) as client:
        target_kube = resolve_kube_id(client, project_id, kube_id)

        banner(2, "Fetch cluster summary")
        cluster = client.get_kube(project_id, target_kube)
        _report_cluster(cluster)

        banner(3, "Fetch node pools and nodes")
        pools = client.list_nodepools(project_id, target_kube)
        nodes = client.list_nodes(project_id, target_kube)
        ok(f"{len(pools)} node pools, {len(nodes)} nodes")

    _write_cluster_csv(data_dir, cluster)
    pools_path = _write_nodepools_csv(data_dir, pools)
    _write_nodes_csv(data_dir, nodes)
    _print_summary(pools, nodes)
    return str(pools_path)


__all__ = ["execute_cluster_inventory"]
