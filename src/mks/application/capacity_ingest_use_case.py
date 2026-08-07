"""Capacity ingest use-case."""

from mks.application.capacity_ingest_service import (
    execute_capacity_ingest as _service,
)
from mks.config import load_config


def execute_capacity_ingest(
    *,
    database_url: str | None = None,
    prometheus_url: str | None = None,
    window: str = "7d",
    cluster: str = "smile-ovh",
    verify_tls: bool = True,
) -> int | None:
    """Persist one capacity snapshot into Postgres for trend analysis.

    Falls back to ``DATABASE_URL`` and ``PROMETHEUS_URL`` from config. Returns
    ``None`` when either is missing, so the CLI can explain rather than crash.
    """
    config = load_config()
    dsn = database_url or config.database_url
    prom = prometheus_url or config.prometheus_url
    if not dsn or not prom:
        missing = " and ".join(
            name
            for name, value in (("DATABASE_URL", dsn), ("PROMETHEUS_URL", prom))
            if not value
        )
        print(f"{missing} not set — nothing to ingest.")
        return None
    return _service(
        database_url=dsn,
        prometheus_url=prom,
        window=window,
        cluster=cluster,
        verify_tls=verify_tls,
    )


__all__ = ["execute_capacity_ingest"]
