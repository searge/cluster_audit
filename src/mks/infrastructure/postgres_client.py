"""Postgres client for capacity trend storage.

Prometheus keeps 15 days, so month-scale trends have to be accumulated
somewhere durable. This is the write side; Grafana reads the same database
directly as a datasource. Synchronous and small on purpose: ingest is a weekly
batch of a few hundred rows, not a serving path.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Any, LiteralString

import psycopg

_Conn = psycopg.Connection[tuple[Any, ...]]


class PostgresError(RuntimeError):
    """A database statement failed."""


class PostgresClient:
    """Minimal Postgres writer. One connection per call, no pooling.

    Statements are typed ``LiteralString``, so a value can only ever reach the
    database as a bound parameter — an f-string built from user input will not
    type-check.
    """

    def __init__(self, dsn: str, *, timeout_seconds: int = 30) -> None:
        """Create a client for a libpq DSN or ``postgresql://`` URL."""
        self._dsn = dsn
        self._timeout = timeout_seconds

    def _connect(self) -> _Conn:
        try:
            return psycopg.connect(self._dsn, connect_timeout=self._timeout)
        except psycopg.Error as exc:
            raise PostgresError(f"connect failed: {exc}") from exc

    @contextmanager
    def transaction(self) -> Generator["PostgresTransaction"]:
        """Run several statements as one unit of work.

        Every other method here opens its own connection, which is fine for a
        single statement and wrong for a group of them: a snapshot row committed
        without its namespace rows leaves the trend showing cluster totals for a
        timestamp that has no detail behind it, and the panels keyed on the
        latest snapshot go blank. Use this whenever a write is only meaningful
        alongside another one.
        """
        try:
            with self._connect() as conn, conn.cursor() as cur:
                yield PostgresTransaction(cur)
        except psycopg.Error as exc:
            raise PostgresError(f"transaction failed: {exc}") from exc

    def execute_script(self, script: LiteralString) -> None:
        """Run a multi-statement script (schema DDL) in one transaction."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(script)
        except psycopg.Error as exc:
            raise PostgresError(f"script failed: {exc}") from exc

    def insert_returning_id(
        self, statement: LiteralString, params: Sequence[Any]
    ) -> int:
        """Run an INSERT ... RETURNING id and return the generated identifier."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(statement, params)
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise PostgresError(f"insert failed: {exc}") from exc
        if row is None:
            raise PostgresError("insert returned no id")
        return int(row[0])

    def insert_many(
        self, statement: LiteralString, rows: Sequence[Sequence[Any]]
    ) -> int:
        """Bulk-insert ``rows``; returns the number submitted."""
        if not rows:
            return 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany(statement, rows)
        except psycopg.Error as exc:
            raise PostgresError(f"bulk insert failed: {exc}") from exc
        return len(rows)

    def scalar(self, statement: LiteralString) -> Any:
        """Run a single-value query; ``None`` when there is no row."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(statement)
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise PostgresError(f"query failed: {exc}") from exc
        return row[0] if row else None


class PostgresTransaction:
    """Statements inside an open transaction. Same shape as the client's own."""

    def __init__(self, cursor: psycopg.Cursor[Any]) -> None:
        """Wrap a cursor belonging to a caller-managed transaction."""
        self._cur = cursor

    def execute(self, statement: LiteralString, params: Sequence[Any] = ()) -> None:
        """Run one statement."""
        self._cur.execute(statement, params)

    def insert_returning_id(
        self, statement: LiteralString, params: Sequence[Any]
    ) -> int:
        """Run an INSERT ... RETURNING id and return the generated identifier."""
        self._cur.execute(statement, params)
        row = self._cur.fetchone()
        if row is None:
            raise PostgresError("insert returned no id")
        return int(row[0])

    def insert_many(
        self, statement: LiteralString, rows: Sequence[Sequence[Any]]
    ) -> int:
        """Bulk-insert ``rows``; returns the number submitted."""
        if not rows:
            return 0
        self._cur.executemany(statement, rows)
        return len(rows)


__all__ = ["PostgresClient", "PostgresError", "PostgresTransaction"]
