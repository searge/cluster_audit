"""Tests for application config."""

from __future__ import annotations

from mks.config import AuditConfig


def test_has_ovh_enabled() -> None:
    cfg = AuditConfig(
        ovh_app_key="ak",
        ovh_app_secret="as",
        ovh_consumer_key="ck",
        ovh_project_id="project",
    )
    assert cfg.has_ovh


def test_has_rancher_enabled() -> None:
    cfg = AuditConfig(rancher_url="https://rancher.example.com", rancher_token="token")
    assert cfg.has_rancher


def test_has_audit_logs_enabled() -> None:
    cfg = AuditConfig(ldp_websocket_url="wss://logs.example.com/tail")
    assert cfg.has_audit_logs
