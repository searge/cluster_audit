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
from datetime import datetime
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


__all__ = [
    "DEFAULT_WINDOW",
    "ProjectTrendLatest",
    "latest_for_project",
    "latest_per_project",
]
