"""Backward-compatible shim for workload audit module."""

from mks.application.workload_efficiency_service import (
    execute_workload_efficiency_audit,
)

execute = execute_workload_efficiency_audit

__all__ = ["execute", "execute_workload_efficiency_audit"]
