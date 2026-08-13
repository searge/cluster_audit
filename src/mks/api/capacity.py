"""Capacity trend API: the latest ``project_trend`` row, over HTTP.

Exists because the trend store is a ClusterIP Postgres behind a default-deny
NetworkPolicy, and the first consumer outside the cluster — Forge — should not
speak Postgres to it: a JDBC datasource would put the database password in a
third place (Ansible) that the rotation script does not know about, which is
the exact failure mode the rotation script documents. Over HTTP the password
stays next to the database, and Forge reaches this service through the Rancher
API-server proxy with the credentials it already holds.

Read-only by construction: the only statements are the two SELECTs in
``capacity_api_service``, and the role it connects as is refused every write.
"""

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from mks.application.capacity_api_service import (
    DEFAULT_WINDOW,
    latest_for_project,
    latest_per_project,
    offenders,
)
from mks.infrastructure.postgres_client import PostgresClient, PostgresError


def create_app(database_url: str) -> FastAPI:
    """Build the application around one client for the given DSN.

    A factory rather than a module-level app so tests can wire a fake DSN and
    the CLI command can pass the real one through without an import-time
    environment read, which Hard Rule 2 forbids outside ``mks.config``.
    """
    app = FastAPI(title="capacity-api", docs_url=None, redoc_url=None)
    client = PostgresClient(database_url, timeout_seconds=5)

    def healthz() -> dict[str, str]:
        # Deliberately does not touch the database: this answers "is the pod
        # up", and a probe that fails on a database hiccup would have the
        # kubelet restart a perfectly healthy process mid-failover.
        return {"status": "ok"}

    def projects_latest(
        window: str = Query(DEFAULT_WINDOW),
    ) -> list[dict[str, Any]]:
        return [asdict(row) for row in _guarded(latest_per_project, client, window)]

    def offenders_list(
        weeks: int = Query(4, ge=1, le=12),
        floor_cpu: float = Query(2.0, alias="floor-cpu-cores", ge=0),
        floor_storage: float = Query(50.0, alias="floor-storage-gib", ge=0),
        top: int = Query(10, ge=1, le=50),
        window: str = Query(DEFAULT_WINDOW),
    ) -> list[dict[str, Any]]:
        return [
            asdict(row)
            for row in _guarded(
                lambda: offenders(
                    client,
                    weeks=weeks,
                    floor_cpu_cores=floor_cpu,
                    floor_storage_gib=floor_storage,
                    top=top,
                    window=window,
                )
            )
        ]

    def project_latest(
        project_id: str,
        window: str = Query(DEFAULT_WINDOW),
    ) -> dict[str, Any]:
        row = _guarded(latest_for_project, client, project_id, window)
        if row is None:
            # 404, not 204: "this project has no capacity data" is a statement
            # about the resource, and the Forge side maps it to "show no card".
            raise HTTPException(status_code=404, detail="no trend row")
        return asdict(row)

    # Registered by call rather than by decorator so the type checker sees the
    # handlers being used; a decorator-registered closure reads as dead code.
    app.get("/healthz")(healthz)
    app.get("/v1/projects/latest")(projects_latest)
    app.get("/v1/offenders")(offenders_list)
    app.get("/v1/projects/{project_id}/latest")(project_latest)

    return app


def _guarded(fn: Any, *args: Any) -> Any:
    """Translate a database failure into 503 instead of a stack trace.

    The card this feeds must degrade to "no data" on the Forge side; a 503
    tells it exactly that, while a 500 with a traceback would page somebody for
    a database restart that CNPG handles on its own.
    """
    try:
        return fn(*args)
    except PostgresError as exc:
        raise HTTPException(status_code=503, detail="trend store unavailable") from exc


__all__ = ["create_app"]
