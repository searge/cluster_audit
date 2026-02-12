"""Backward-compatible shim for RBAC audit module."""

from mks.application.rbac_audit_service import execute_rbac_audit

execute = execute_rbac_audit

__all__ = ["execute", "execute_rbac_audit"]
