"""Backward-compatible shim for usage audit module."""

from mks.application.usage_efficiency_service import execute_usage_efficiency_audit

execute = execute_usage_efficiency_audit

__all__ = ["execute", "execute_usage_efficiency_audit"]
