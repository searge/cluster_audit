"""Read side of the capacity trend store, for the serving API.

The ingest writes one ``project_trend`` row per Rancher project per snapshot;
consumers outside the cluster — Forge's capacity card is the first — need the
latest of those rows without speaking Postgres. These functions are that read,
kept as module-level functions so the FastAPI layer stays a thin adapter.

Money columns are deliberately not exposed. The card these rows feed shows
cores and gigabytes: a euro figure invites a chargeback argument that does not
apply, since OVH invoices the cluster as a whole. Grafana keeps the euro view
for anyone who wants it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mks.infrastructure.postgres_client import PostgresClient

# Every query filters on window_spec even though only '7d' exists today: the
# view carries one row per window, so a second window added later would silently
# double every "latest per project" result that did not filter.
DEFAULT_WINDOW = "7d"

_LATEST_PER_PROJECT = """
SELECT DISTINCT ON (project_id)
       project_id, project, taken_at, namespaces,
       cpu_req_cores, cpu_p95_cores, cpu_phantom_cores,
       mem_req_gb, mem_max_gb,
       storage_gib, storage_unmounted_gib
FROM project_trend
WHERE window_spec = %s
ORDER BY project_id, taken_at DESC, snapshot_id DESC
"""

_LATEST_FOR_PROJECT = """
SELECT project_id, project, taken_at, namespaces,
       cpu_req_cores, cpu_p95_cores, cpu_phantom_cores,
       mem_req_gb, mem_max_gb,
       storage_gib, storage_unmounted_gib
FROM project_trend
WHERE window_spec = %s AND project_id = %s
ORDER BY taken_at DESC, snapshot_id DESC
LIMIT 1
"""


@dataclass(frozen=True)
class ProjectTrendLatest:  # pylint: disable=too-many-instance-attributes
    """The newest ``project_trend`` row for one Rancher project.

    ``project_id`` is the bare Rancher id (``p-xxxxx``) — the same value Forge
    derives from its Rancher component, which is what makes the join work
    without any mapping table.
    """

    project_id: str
    project: str
    taken_at: datetime
    namespaces: int
    cpu_req_cores: float
    cpu_p95_cores: float
    cpu_phantom_cores: float
    mem_req_gb: float
    mem_max_gb: float
    storage_gib: float
    storage_unmounted_gib: float


def latest_per_project(
    client: PostgresClient, window: str = DEFAULT_WINDOW
) -> list[ProjectTrendLatest]:
    """The newest row of every project, for a pass that walks all of them."""
    return [_to_latest(row) for row in client.query(_LATEST_PER_PROJECT, (window,))]


def latest_for_project(
    client: PostgresClient, project_id: str, window: str = DEFAULT_WINDOW
) -> ProjectTrendLatest | None:
    """The newest row of one project, or ``None`` when the store has none."""
    rows = client.query(_LATEST_FOR_PROJECT, (window, project_id))
    return _to_latest(rows[0]) if rows else None


def _to_latest(row: tuple[Any, ...]) -> ProjectTrendLatest:
    # Numerics arrive as Decimal; the consumers are JSON and arithmetic, so
    # float is the honest type. None never occurs today (the view sums NOT NULL
    # columns), but a schema change must not turn into a crash on read.
    return ProjectTrendLatest(
        project_id=str(row[0]),
        project=str(row[1]),
        taken_at=row[2],
        namespaces=int(row[3]),
        cpu_req_cores=float(row[4] or 0),
        cpu_p95_cores=float(row[5] or 0),
        cpu_phantom_cores=float(row[6] or 0),
        mem_req_gb=float(row[7] or 0),
        mem_max_gb=float(row[8] or 0),
        storage_gib=float(row[9] or 0),
        storage_unmounted_gib=float(row[10] or 0),
    )


_OFFENDER_HISTORY = """
SELECT project_id,
       max(project)                       AS project,
       taken_at::date                     AS day,
       bool_and(
           cpu_req_cores - cpu_p95_cores >= %s
           OR storage_unmounted_gib >= %s
       )                                  AS over_floor
FROM project_trend
WHERE window_spec = %s
  AND taken_at >= now() - (%s || ' days')::interval
GROUP BY project_id, taken_at::date
"""

_OFFENDER_CURRENT = """
SELECT DISTINCT ON (project_id)
       project_id, project, taken_at, namespaces,
       cpu_req_cores, cpu_p95_cores, cpu_phantom_cores,
       mem_req_gb, mem_max_gb,
       storage_gib, storage_unmounted_gib,
       coalesce(wasted_eur_month, 0) + coalesce(storage_wasted_eur_month, 0)
FROM project_trend
WHERE window_spec = %s
ORDER BY project_id, taken_at DESC, snapshot_id DESC
"""

# Projects that are infrastructure rather than tenants. kube-system tops the
# phantom-CPU table most weeks; mailing ourselves about it would be honest but
# useless, and mailing a tenant list with System on it undermines the list.
DEFAULT_EXCLUDED_PROJECTS = ("System", "(no project)")


@dataclass(frozen=True)
class Offender:  # pylint: disable=too-many-instance-attributes
    """One project that met the offender rule, with the numbers its mail cites."""

    project_id: str
    project: str
    taken_at: datetime
    weeks_on_list: int
    cpu_req_cores: float
    cpu_p95_cores: float
    cpu_phantom_cores: float
    mem_req_gb: float
    mem_max_gb: float
    storage_gib: float
    storage_unmounted_gib: float
    wasted_eur_month: float


def offenders(  # pylint: disable=too-many-arguments,too-many-locals
    client: PostgresClient,
    *,
    weeks: int = 4,
    floor_cpu_cores: float = 2.0,
    floor_storage_gib: float = 50.0,
    top: int = 10,
    window: str = DEFAULT_WINDOW,
    excluded_projects: tuple[str, ...] = DEFAULT_EXCLUDED_PROJECTS,
) -> list[Offender]:
    """The projects whose waste is persistent enough to be worth a mail.

    Three rules, all of which must hold. The floor (phantom CPU or unmounted
    storage) keeps borderline over-provisioning out. Persistence - the floor
    held on EVERY snapshot of the window, and the data actually covers the
    window - keeps launches and load tests out; it also means the list stays
    empty until the store has accumulated `weeks` of history, which is the
    intended warm-up, not a bug. The top-N cap keeps the mail rare enough to
    stay meaningful.

    weeks_on_list counts consecutive weekly buckets, newest first, in which
    every snapshot met the floor - the escalation counter in the mail subject.
    It can exceed `weeks` once the habit is older than the window.
    """
    days = weeks * 7
    history = client.query(
        _OFFENDER_HISTORY, (floor_cpu_cores, floor_storage_gib, window, str(days))
    )

    by_project: dict[str, list[tuple[Any, ...]]] = {}
    for row in history:
        by_project.setdefault(str(row[0]), []).append(row)

    qualifying: dict[str, int] = {}
    for project_id, rows in by_project.items():
        rows.sort(key=lambda r: r[2])
        first_day, last_day = rows[0][2], rows[-1][2]
        # Coverage: a store younger than the window must not qualify anybody.
        if (last_day - first_day).days < days - 2:
            continue
        if not all(bool(r[3]) for r in rows):
            continue
        qualifying[project_id] = _weeks_on_list(rows)

    if not qualifying:
        return []

    current = client.query(_OFFENDER_CURRENT, (window,))
    result = [
        Offender(
            project_id=str(row[0]),
            project=str(row[1]),
            taken_at=row[2],
            weeks_on_list=qualifying[str(row[0])],
            cpu_req_cores=float(row[4] or 0),
            cpu_p95_cores=float(row[5] or 0),
            cpu_phantom_cores=float(row[6] or 0),
            mem_req_gb=float(row[7] or 0),
            mem_max_gb=float(row[8] or 0),
            storage_gib=float(row[9] or 0),
            storage_unmounted_gib=float(row[10] or 0),
            wasted_eur_month=float(row[11] or 0),
        )
        for row in current
        if str(row[0]) in qualifying and str(row[1]) not in excluded_projects
    ]
    result.sort(key=lambda o: o.wasted_eur_month, reverse=True)
    return result[:top]


def _weeks_on_list(rows: list[tuple[Any, ...]]) -> int:
    """Consecutive weekly buckets, newest first, where every snapshot met the floor.

    Buckets are counted back from the newest snapshot in 7-day steps. A bucket
    with no snapshots ends the streak: silence is not compliance, but it is
    not evidence of a habit either.
    """
    latest = rows[-1][2]
    streak = 0
    while True:
        bucket_end = latest - timedelta(days=7 * streak)
        bucket = [
            r for r in rows if bucket_end - timedelta(days=7) < r[2] <= bucket_end
        ]
        if not bucket or not all(bool(r[3]) for r in bucket):
            return streak
        streak += 1


__all__ = [
    "DEFAULT_EXCLUDED_PROJECTS",
    "DEFAULT_WINDOW",
    "Offender",
    "ProjectTrendLatest",
    "latest_for_project",
    "latest_per_project",
    "offenders",
]
