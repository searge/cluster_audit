"""Pure cohort logic for reconciling detached OVH volumes against cluster PVs.

A detached Cinder volume is only safe to delete once we know the cluster does
not reference it. Volumes are sorted into cohorts by joining on the CSI
``volumeHandle`` and by the cluster tag embedded in the managed-volume name
(``ovh-managed-kubernetes-<tag>-pvc-<uuid>``): a tag that no attached volume
carries belongs to a previous cluster generation and can never be re-attached
by the current one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from mks.infrastructure.ovh_client import VolumeInfo

_MANAGED_NAME = re.compile(r"^ovh-managed-kubernetes-(?P<tag>[a-z0-9]+)-pvc-")

#: Detached for less than this many days -> keep (may be mid-reschedule).
RECENT_DAYS = 90


class Cohort(StrEnum):
    """Deletion-safety buckets, safest-to-delete first."""

    OLD_CLUSTER = "wave1-old-cluster"  # tag from a previous cluster generation
    NO_PV_STALE = "wave2-no-pv"  # unknown to the cluster, older than RECENT_DAYS
    RELEASED_RETAIN = "wave3-released-retain"  # PV Released/Failed (Retain leftover)
    KEEP = "keep"  # in use, reserved, bound, or too recent to judge


@dataclass(frozen=True)
class PvRecord:
    """The cluster-side view of one PersistentVolume."""

    volume_handle: str
    pv_name: str
    phase: str  # Bound | Released | Failed | Available
    reclaim_policy: str
    claim: str  # "namespace/name" or ""


@dataclass(frozen=True)
class ReconciledVolume:
    """One detached volume with its safety verdict."""

    volume: VolumeInfo
    cohort: Cohort
    reason: str
    pv_name: str = ""
    claim: str = ""


def cluster_tag(volume_name: str) -> str | None:
    """Extract the cluster tag from a managed-volume name, if present."""
    match = _MANAGED_NAME.match(volume_name)
    return match.group("tag") if match else None


def current_cluster_tags(volumes: list[VolumeInfo]) -> set[str]:
    """Tags seen on currently attached volumes = live cluster generation(s)."""
    return {
        tag
        for vol in volumes
        if vol.attached and (tag := cluster_tag(vol.name)) is not None
    }


def _age_days(creation_date: str, now: datetime) -> float | None:
    try:
        created = datetime.fromisoformat(creation_date)
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds() / 86400


def _classify_with_pv(volume: VolumeInfo, pv: PvRecord) -> ReconciledVolume:
    """Judge a detached volume that still has a PV in the cluster."""
    if pv.phase in ("Released", "Failed"):
        return ReconciledVolume(
            volume,
            Cohort.RELEASED_RETAIN,
            f"PV {pv.phase}, reclaim={pv.reclaim_policy}",
            pv_name=pv.pv_name,
            claim=pv.claim,
        )
    return ReconciledVolume(
        volume,
        Cohort.KEEP,
        f"PV exists (phase={pv.phase})",
        pv_name=pv.pv_name,
        claim=pv.claim,
    )


def classify_detached(
    volume: VolumeInfo,
    *,
    pv_by_handle: dict[str, PvRecord],
    live_tags: set[str],
    now: datetime,
) -> ReconciledVolume:
    """Assign one detached volume to a deletion cohort.

    ``volume`` must already be detached; attached volumes never reach here.
    """
    if volume.status != "available":
        return ReconciledVolume(
            volume, Cohort.KEEP, f"status={volume.status} (not settled)"
        )

    pv = pv_by_handle.get(volume.id)
    if pv is not None:
        return _classify_with_pv(volume, pv)
    return _classify_unreferenced(volume, live_tags=live_tags, now=now)


def _classify_unreferenced(
    volume: VolumeInfo, *, live_tags: set[str], now: datetime
) -> ReconciledVolume:
    """Judge a detached volume that no PV in the cluster references."""
    tag = cluster_tag(volume.name)
    if tag is None:
        # No cluster-generation evidence at all -> never a delete candidate.
        return ReconciledVolume(
            volume, Cohort.KEEP, "non-managed volume name; manual review"
        )
    if live_tags and tag not in live_tags:
        return ReconciledVolume(
            volume,
            Cohort.OLD_CLUSTER,
            f"cluster tag '{tag}' not in {sorted(live_tags)}",
        )

    age = _age_days(volume.creation_date, now)
    if age is None:
        return ReconciledVolume(volume, Cohort.KEEP, "unparseable creationDate")
    if age < RECENT_DAYS:
        return ReconciledVolume(
            volume, Cohort.KEEP, f"only {age:.0f}d old (<{RECENT_DAYS}d)"
        )
    return ReconciledVolume(volume, Cohort.NO_PV_STALE, f"no PV, {age:.0f}d old")


def _demote_ambiguous(result: ReconciledVolume) -> ReconciledVolume:
    """Downgrade delete waves to keep when cluster scope is not proven."""
    if result.cohort not in (Cohort.OLD_CLUSTER, Cohort.NO_PV_STALE):
        return result
    return ReconciledVolume(
        result.volume,
        Cohort.KEEP,
        f"scope-ambiguous (multi-cluster project); was {result.cohort.value}: "
        f"{result.reason}",
        pv_name=result.pv_name,
        claim=result.claim,
    )


def reconcile(
    volumes: list[VolumeInfo],
    pvs: list[PvRecord],
    *,
    now: datetime,
    single_cluster: bool = True,
) -> list[ReconciledVolume]:
    """Classify every detached volume in the project.

    ``single_cluster=False`` means the project hosts more than one MKS cluster,
    so the PV join and tag heuristic only cover part of the project: delete
    waves are demoted to ``keep`` rather than risking another cluster's data.
    """
    pv_by_handle = {pv.volume_handle: pv for pv in pvs}
    live_tags = current_cluster_tags(volumes)
    results = [
        classify_detached(vol, pv_by_handle=pv_by_handle, live_tags=live_tags, now=now)
        for vol in volumes
        if not vol.attached
    ]
    if single_cluster:
        return results
    return [_demote_ambiguous(res) for res in results]


__all__ = [
    "RECENT_DAYS",
    "Cohort",
    "PvRecord",
    "ReconciledVolume",
    "classify_detached",
    "cluster_tag",
    "current_cluster_tags",
    "reconcile",
]
