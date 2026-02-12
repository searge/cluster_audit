"""Backward-compatible shim for dashboard module."""

from mks.application.dashboard_summary_service import execute_dashboard_summary

execute = execute_dashboard_summary

__all__ = ["execute", "execute_dashboard_summary"]
