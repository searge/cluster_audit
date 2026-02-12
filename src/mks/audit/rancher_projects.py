"""Backward-compatible shim for Rancher project module."""

from mks.application.rancher_project_overview_service import (
    execute_rancher_project_overview,
)

execute = execute_rancher_project_overview

__all__ = ["execute", "execute_rancher_project_overview"]
