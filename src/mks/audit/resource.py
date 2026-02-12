"""Backward-compatible shim for resource audit module."""

from mks.application.resource_audit_service import execute_resource_audit

execute = execute_resource_audit

__all__ = ["execute", "execute_resource_audit"]
