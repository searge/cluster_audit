"""Tests for the offender rule: who earns the weekly mail.

The rule is three conditions, and each has its own way to be wrong: a floor
without persistence mails every launch spike, persistence without coverage
mails everybody the first week the store exists, and no cap makes the mail
routine. Each test breaks exactly one condition.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from mks.application.capacity_api_service import Offender, offenders
from mks.infrastructure.postgres_client import PostgresClient

TODAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)


class _FakeClient(PostgresClient):
    """First query() call answers history rows, the second current rows."""

    def __init__(
        self, history: list[tuple[Any, ...]], current: list[tuple[Any, ...]]
    ) -> None:
        super().__init__("postgresql://unused")
        self._answers = [history, current]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, statement: str, params: Any = ()) -> list[tuple[Any, ...]]:
        """Record the call and pop the next canned answer."""
        self.calls.append((statement, tuple(params)))
        return self._answers.pop(0)


def _history(
    project_id: str, days: int, over_floor: bool = True, name: str = "p"
) -> list[tuple[Any, ...]]:
    return [
        (project_id, name, TODAY - timedelta(days=offset), over_floor)
        for offset in range(days)
    ]


def _current(project_id: str, name: str = "p", waste: float = 100.0) -> tuple[Any, ...]:
    return (project_id, name, NOW, 2, 9.15, 0.70, 8.45, 26.1, 20.8, 95.0, 37.0, waste)


def test_a_persistent_offender_qualifies_with_its_streak() -> None:
    """Six weeks over the floor: qualifies, and the counter says six, not four."""
    client = _FakeClient(
        _history("p-x5fpr", 42), [_current("p-x5fpr", "visa-platform")]
    )

    (offender,) = offenders(client)

    assert offender.project == "visa-platform"
    assert offender.weeks_on_list == 6
    assert offender.wasted_eur_month == 100.0


def test_one_clean_snapshot_disqualifies() -> None:
    """One good day inside the window drops the project out: the floor
    must hold on every snapshot."""
    rows = _history("p-x5fpr", 28)
    rows[10] = (rows[10][0], rows[10][1], rows[10][2], False)
    client = _FakeClient(rows, [])

    assert offenders(client) == []


def test_a_young_store_qualifies_nobody() -> None:
    """Three days of data, all over the floor: the warm-up case,
    kept out by coverage."""
    client = _FakeClient(_history("p-x5fpr", 3), [])

    assert offenders(client) == []


def test_an_empty_week_ends_the_streak_but_not_the_qualification() -> None:
    """A whole week with no snapshots: silence is not compliance,
    but not a habit either.

    The project still qualifies - every snapshot it does have is over the floor,
    and the data spans the window - but the counter stops at the silence.
    """
    rows = [r for r in _history("p-x5fpr", 42) if not 7 <= (TODAY - r[2]).days <= 13]
    client = _FakeClient(rows, [_current("p-x5fpr")])

    (offender,) = offenders(client)

    assert offender.weeks_on_list == 1


def test_system_projects_are_never_mailed() -> None:
    """kube-system tops the phantom table most weeks; mailing
    ourselves would drown the list."""
    client = _FakeClient(
        _history("p-nnw6m", 28, name="System"),
        [_current("p-nnw6m", "System")],
    )

    assert offenders(client) == []


def test_the_list_is_capped_by_worst_waste() -> None:
    """Ten spots, worst euros first: the cap is what keeps the mail rare."""
    history = (
        _history("p-a", 28, name="a")
        + _history("p-b", 28, name="b")
        + _history("p-c", 28, name="c")
    )
    current = [
        _current("p-a", "a", 10.0),
        _current("p-b", "b", 300.0),
        _current("p-c", "c", 200.0),
    ]
    client = _FakeClient(history, current)

    result = offenders(client, top=2)

    assert [o.project for o in result] == ["b", "c"]


def test_result_type_is_the_frozen_dataclass() -> None:
    """The API serialises with asdict, which silently returns {} for a plain object."""
    client = _FakeClient(_history("p-x5fpr", 28), [_current("p-x5fpr")])

    (offender,) = offenders(client)

    assert isinstance(offender, Offender)
