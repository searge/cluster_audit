"""Volume reconcile service: sort detached volumes into deletion-safety waves.

Joins the project's detached Cinder volumes against the live cluster's
PersistentVolumes (CSI ``volumeHandle``) so each volume lands in a cohort:
wave1 (previous cluster generation), wave2 (unknown to the cluster and stale),
wave3 (Released/Failed PV leftovers to review with owners) or keep. Deletion
itself stays manual — this capability only produces the evidence and the
ready-to-paste id list for wave 1.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mks.application._ovh_session import ovh_session, require_project_id
from mks.application._step_report import banner, info, ok, warn
from mks.application.use_case_utils import write_csv
from mks.config import OvhConfig, Prices
from mks.domain.volume_reconcile import Cohort, PvRecord, ReconciledVolume, reconcile
from mks.infrastructure.kubectl_client import KubectlError, kubectl_json


def _fetch_pv_records() -> list[PvRecord]:
    """Read every PV's CSI handle, phase and claim from the live cluster."""
    payload = kubectl_json("get pv")
    records: list[PvRecord] = []
    for item in payload.get("items", []):
        spec: dict[str, Any] = item.get("spec", {})
        # CSI is the norm on MKS; legacy in-tree Cinder PVs carry the volume id
        # in spec.cinder.volumeID and must count as referenced too.
        handle = spec.get("csi", {}).get("volumeHandle") or (
            spec.get("cinder") or {}
        ).get("volumeID")
        if not handle:
            continue  # non-Cinder PV (hostPath etc.) cannot map to a project volume
        claim_ref = spec.get("claimRef") or {}
        claim = (
            f"{claim_ref.get('namespace', '')}/{claim_ref.get('name', '')}"
            if claim_ref
            else ""
        )
        records.append(
            PvRecord(
                volume_handle=str(handle),
                pv_name=str(item.get("metadata", {}).get("name", "")),
                phase=str(item.get("status", {}).get("phase", "")),
                reclaim_policy=str(spec.get("persistentVolumeReclaimPolicy", "")),
                claim=claim,
            )
        )
    return records


def _write_reconcile_csv(
    data_dir: str, results: list[ReconciledVolume], eur_per_gb: float
) -> Path:
    header = [
        "cohort",
        "id",
        "name",
        "sizeGB",
        "monthlyCostEUR",
        "status",
        "creationDate",
        "pv",
        "claim",
        "reason",
    ]
    order = {c: i for i, c in enumerate(Cohort)}
    rows = [
        [
            res.cohort.value,
            res.volume.id,
            res.volume.name,
            res.volume.size_gb,
            f"{res.volume.size_gb * eur_per_gb:.2f}",
            res.volume.status,
            res.volume.creation_date,
            res.pv_name,
            res.claim,
            res.reason,
        ]
        for res in sorted(
            results, key=lambda r: (order[r.cohort], r.volume.creation_date)
        )
    ]
    return write_csv(Path(data_dir) / "volume_reconcile.csv", header, rows)


def _write_wave1_ids(data_dir: str, results: list[ReconciledVolume]) -> Path:
    """Wave-1 candidates with the evidence needed to audit each verdict."""
    rows = [
        [
            res.volume.id,
            res.volume.name,
            res.volume.size_gb,
            res.volume.creation_date,
            res.reason,
        ]
        for res in results
        if res.cohort is Cohort.OLD_CLUSTER
    ]
    return write_csv(
        Path(data_dir) / "wave1_review_candidates.csv",
        ["id", "name", "sizeGB", "creationDate", "reason"],
        rows,
    )


def _summarize(
    results: list[ReconciledVolume], eur_per_gb: float
) -> dict[Cohort, tuple[int, int, float]]:
    stats: dict[Cohort, tuple[int, int, float]] = {}
    for cohort in Cohort:
        subset = [r for r in results if r.cohort is cohort]
        gigabytes = sum(r.volume.size_gb for r in subset)
        stats[cohort] = (len(subset), gigabytes, gigabytes * eur_per_gb)
    return stats


def execute_volume_reconcile(
    *, data_dir: str, ovh_config: OvhConfig, prices: Prices
) -> str:
    """Reconcile detached volumes against cluster PVs and write cohort CSVs.

    Returns the reconcile CSV path. Raises ``OvhApiError`` / ``KubectlError``
    on backend failure.
    """
    project_id = require_project_id(ovh_config)
    eur_per_gb = prices.volume_high_speed_eur_per_gb_month

    with ovh_session(ovh_config) as client:
        banner(2, "Fetch project volumes")
        volumes = client.list_volumes(project_id)
        detached = [v for v in volumes if not v.attached]
        ok(f"{len(volumes)} volumes, {len(detached)} detached")
        kube_ids = client.list_kube_ids(project_id)
        single_cluster = len(kube_ids) == 1
        if single_cluster:
            ok(f"single MKS cluster in project ({kube_ids[0]})")
        else:
            warn(
                f"{len(kube_ids)} MKS clusters in project - PV join covers only "
                "the kubeconfig cluster; delete waves demoted to keep"
            )

    banner(3, "Fetch cluster PersistentVolumes (kubectl)")
    try:
        pvs = _fetch_pv_records()
    except KubectlError as exc:
        raise RuntimeError(
            "kubectl 'get pv' failed - is the kubeconfig pointing at the MKS "
            f"cluster? ({exc})"
        ) from exc
    ok(f"{len(pvs)} CSI PVs in cluster")

    banner(4, "Classify detached volumes")
    results = reconcile(
        volumes, pvs, now=datetime.now(UTC), single_cluster=single_cluster
    )
    stats = _summarize(results, eur_per_gb)
    _print_cohorts(stats)

    reconcile_path = _write_reconcile_csv(data_dir, results, eur_per_gb)
    _write_wave1_ids(data_dir, results)
    _print_next_actions(stats)
    return str(reconcile_path)


def _print_cohorts(stats: dict[Cohort, tuple[int, int, float]]) -> None:
    for cohort in Cohort:
        count, gigabytes, cost = stats[cohort]
        line = (
            f"{cohort.value:<24} {count:>4} vols  {gigabytes:>6} GB"
            f"  ~{cost:7.2f} EUR/mo"
        )
        (warn if cohort is not Cohort.KEEP and count else ok)(line)


def _print_next_actions(stats: dict[Cohort, tuple[int, int, float]]) -> None:
    banner(5, "Next actions")
    wave1_count, _, wave1_cost = stats[Cohort.OLD_CLUSTER]
    if wave1_count:
        info(
            f"wave1: {wave1_count} volumes from previous cluster generation(s) "
            f"(~{wave1_cost:.2f} EUR/mo) - see wave1_review_candidates.csv; "
            "delete needs a consumer key with DELETE /cloud/project/*/volume/*"
        )
    info("wave2 after wave1 settles a week; wave3 only after owner review")


__all__ = ["execute_volume_reconcile"]
