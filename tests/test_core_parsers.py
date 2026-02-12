"""Tests for shared resource parsers."""

from __future__ import annotations

from mks.core.parsers import parse_cpu, parse_memory


def test_parse_cpu_units() -> None:
    assert parse_cpu("250m") == 250
    assert parse_cpu("1") == 1000
    assert parse_cpu("1500u") == 1
    assert parse_cpu("2000000n") == 2


def test_parse_cpu_none_values() -> None:
    assert parse_cpu("") == 0
    assert parse_cpu("0") == 0
    assert parse_cpu("<none>") == 0


def test_parse_memory_units() -> None:
    assert parse_memory("1Ki") == 1024
    assert parse_memory("1Mi") == 1024**2
    assert parse_memory("1Gi") == 1024**3
    assert parse_memory("2G") == 2_000_000_000


def test_parse_memory_none_values() -> None:
    assert parse_memory("") == 0
    assert parse_memory("0") == 0
    assert parse_memory("<none>") == 0
