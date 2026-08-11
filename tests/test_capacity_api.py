"""Tests for the capacity read service and its HTTP adapter.

The database is faked at the PostgresClient boundary: the SQL itself is
exercised weekly by the real ingest and daily by Grafana, while what this code
owns is the row mapping, the window filter being applied at all, and the HTTP
contract Forge programs against (404 for an unknown project, 503 when the
store is down).
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from mks.api import create_app
from mks.application.capacity_api_service import (
    latest_for_project,
    latest_per_project,
)
from mks.infrastructure.postgres_client import PostgresClient, PostgresError

TAKEN_AT = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)


def _row(project_id: str = "p-x5fpr") -> tuple[Any, ...]:
    return (
        project_id,
        "air-corsica",
        TAKEN_AT,
        2,
        "9.150",
        "0.700",
        "8.450",
        "24.0",
        "3.1",
        "133.0",
        "125.0",
    )


class _FakeClient(PostgresClient):
    """Answers query() from a canned list and records what was asked."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        super().__init__("postgresql://unused")
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, statement: str, params: Any = ()) -> list[tuple[Any, ...]]:
        """Record the call and answer from the canned rows."""
        self.calls.append((statement, tuple(params)))
        return self.rows


def test_rows_map_to_floats_not_decimals() -> None:
    """Decimal in, float out: the consumers are JSON and arithmetic."""
    client = _FakeClient([_row()])

    (row,) = latest_per_project(client)

    assert row.project_id == "p-x5fpr"
    assert row.cpu_req_cores == 9.15
    assert row.storage_unmounted_gib == 125.0
    assert isinstance(row.cpu_req_cores, float)


def test_every_query_filters_on_the_window() -> None:
    """A second window_spec in the view must not double the results."""
    client = _FakeClient([_row()])

    latest_per_project(client, window="14d")
    latest_for_project(client, "p-x5fpr", window="14d")

    for statement, params in client.calls:
        assert "window_spec = %s" in statement
        assert params[0] == "14d"


def test_unknown_project_is_none() -> None:
    """An empty result set is an answer, not an error."""
    assert latest_for_project(_FakeClient([]), "p-nope") is None


def test_http_latest_for_project() -> None:
    """The row Forge programs against: floats, and a datetime that serializes."""
    app = create_app("postgresql://unused")
    row = latest_for_project(_FakeClient([_row("p-x5fpr")]), "p-x5fpr")
    with patch("mks.api.capacity.latest_for_project", return_value=row):
        response = TestClient(app).get("/v1/projects/p-x5fpr/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "p-x5fpr"
    assert body["storage_gib"] == 133.0
    # datetime must serialize, not crash the encoder
    assert body["taken_at"].startswith("2026-08-10")


def test_http_unknown_project_is_404() -> None:
    """404 is the contract for "no capacity data"; Forge maps it to no card."""
    app = create_app("postgresql://unused")
    with patch("mks.api.capacity.latest_for_project", return_value=None):
        response = TestClient(app).get("/v1/projects/p-nope/latest")

    assert response.status_code == 404


def test_http_database_failure_is_503_not_500() -> None:
    """Forge degrades to "no card" on 503; a 500 would look like a Forge bug."""
    app = create_app("postgresql://unused")
    with patch(
        "mks.api.capacity.latest_per_project",
        side_effect=PostgresError("connect failed"),
    ):
        response = TestClient(app).get("/v1/projects/latest")

    assert response.status_code == 503


def test_healthz_does_not_touch_the_database() -> None:
    """The probe answers "is the pod up", not "is Postgres up"."""
    app = create_app("postgresql://unreachable.invalid")

    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
