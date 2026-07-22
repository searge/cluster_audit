"""Tests for the volume-reconcile cohort logic."""

from datetime import UTC, datetime

from mks.domain.volume_reconcile import (
    Cohort,
    PvRecord,
    classify_detached,
    cluster_tag,
    current_cluster_tags,
    reconcile,
)
from mks.infrastructure.ovh_client import VolumeInfo

NOW = datetime(2026, 7, 7, tzinfo=UTC)


def _vol(
    vid: str = "v1",
    name: str = "ovh-managed-kubernetes-x1s3q1-pvc-abc",
    status: str = "available",
    attached: bool = False,
    created: str = "2022-11-03T10:32:11Z",
) -> VolumeInfo:
    return VolumeInfo(
        id=vid,
        name=name,
        region="SBG5",
        size_gb=8,
        status=status,
        attached=attached,
        creation_date=created,
    )


def _pv(handle: str, phase: str = "Bound", reclaim: str = "Delete") -> PvRecord:
    return PvRecord(
        volume_handle=handle,
        pv_name="pvc-abc",
        phase=phase,
        reclaim_policy=reclaim,
        claim="ns/app-data",
    )


def test_cluster_tag_parses_managed_name() -> None:
    """Managed-volume names yield their cluster tag; others yield None."""
    assert cluster_tag("ovh-managed-kubernetes-x1s3q1-pvc-123") == "x1s3q1"
    assert cluster_tag("my-manual-volume") is None


def test_current_tags_only_from_attached() -> None:
    """Only attached volumes contribute live cluster tags."""
    vols = [
        _vol(attached=True, name="ovh-managed-kubernetes-new123-pvc-a"),
        _vol(name="ovh-managed-kubernetes-old456-pvc-b"),
    ]
    assert current_cluster_tags(vols) == {"new123"}


def test_old_cluster_tag_is_wave1() -> None:
    """A tag absent from live tags lands in wave 1."""
    res = classify_detached(
        _vol(name="ovh-managed-kubernetes-old456-pvc-b"),
        pv_by_handle={},
        live_tags={"new123"},
        now=NOW,
    )
    assert res.cohort is Cohort.OLD_CLUSTER


def test_released_pv_is_wave3() -> None:
    """A Released PV puts the volume into the review wave."""
    res = classify_detached(
        _vol(),
        pv_by_handle={"v1": _pv("v1", phase="Released", reclaim="Retain")},
        live_tags=set(),
        now=NOW,
    )
    assert res.cohort is Cohort.RELEASED_RETAIN
    assert res.claim == "ns/app-data"


def test_bound_pv_is_kept() -> None:
    """A Bound PV keeps the volume."""
    res = classify_detached(
        _vol(),
        pv_by_handle={"v1": _pv("v1", phase="Bound")},
        live_tags=set(),
        now=NOW,
    )
    assert res.cohort is Cohort.KEEP


def test_non_managed_name_is_kept_for_manual_review() -> None:
    """A volume without the managed-name prefix is never a delete candidate."""
    res = classify_detached(
        _vol(name="manual-volume", created="2024-01-01T00:00:00Z"),
        pv_by_handle={},
        live_tags={"new123"},
        now=NOW,
    )
    assert res.cohort is Cohort.KEEP
    assert "manual review" in res.reason


def test_recent_volume_is_kept() -> None:
    """Fresh detached managed volume is kept."""
    res = classify_detached(
        _vol(
            name="ovh-managed-kubernetes-new123-pvc-z",
            created="2026-06-20T00:00:00Z",
        ),
        pv_by_handle={},
        live_tags=set(),
        now=NOW,
    )
    assert res.cohort is Cohort.KEEP


def test_non_available_status_is_kept() -> None:
    """Non-available status is never a delete candidate."""
    res = classify_detached(
        _vol(status="reserved"), pv_by_handle={}, live_tags=set(), now=NOW
    )
    assert res.cohort is Cohort.KEEP


def test_current_tag_without_pv_falls_through_to_age() -> None:
    """Live-tag volume without PV is judged by age."""
    res = classify_detached(
        _vol(
            name="ovh-managed-kubernetes-new123-pvc-c",
            created="2023-01-01T00:00:00Z",
        ),
        pv_by_handle={},
        live_tags={"new123"},
        now=NOW,
    )
    assert res.cohort is Cohort.NO_PV_STALE


def test_multi_cluster_project_demotes_delete_waves() -> None:
    """With several MKS clusters in the project, delete waves become keep."""
    vols = [
        _vol(vid="a", attached=True, name="ovh-managed-kubernetes-new123-pvc-a"),
        _vol(vid="b", name="ovh-managed-kubernetes-old456-pvc-b"),
    ]
    results = reconcile(vols, [], now=NOW, single_cluster=False)
    assert results[0].cohort is Cohort.KEEP
    assert "scope-ambiguous" in results[0].reason
    assert "wave1-old-cluster" in results[0].reason


def test_reconcile_skips_attached() -> None:
    """reconcile() classifies only detached volumes."""
    vols = [
        _vol(vid="a", attached=True, name="ovh-managed-kubernetes-new123-pvc-a"),
        _vol(vid="b", name="ovh-managed-kubernetes-old456-pvc-b"),
    ]
    results = reconcile(vols, [], now=NOW)
    assert [r.volume.id for r in results] == ["b"]
    assert results[0].cohort is Cohort.OLD_CLUSTER
