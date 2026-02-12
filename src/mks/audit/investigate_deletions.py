"""Backward-compatible shim for deletion investigation module."""

from mks.application.deletion_investigation_service import (
    execute_deletion_investigation,
)

execute = execute_deletion_investigation

__all__ = ["execute", "execute_deletion_investigation"]
