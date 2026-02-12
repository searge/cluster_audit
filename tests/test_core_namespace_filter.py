"""Tests for system namespace filtering."""

from __future__ import annotations

from mks.core.namespace_filter import is_system_namespace


def test_default_system_namespaces() -> None:
    assert is_system_namespace("kube-system")
    assert is_system_namespace("kube-public")
    assert is_system_namespace("ingress-controller")


def test_default_prefixes() -> None:
    assert is_system_namespace("cattle-fleet-system")
    assert is_system_namespace("rancher-operator-system")
    assert is_system_namespace("kube-foo")


def test_user_namespace() -> None:
    assert not is_system_namespace("payments")


def test_include_system_flag() -> None:
    assert not is_system_namespace("kube-system", include_system=True)


def test_extra_namespace_and_prefix() -> None:
    assert is_system_namespace(
        "custom-system",
        extra_namespaces=frozenset({"custom-system"}),
    )
    assert is_system_namespace("ops-infra", extra_prefixes=("ops-",))
