"""Backward-compatible shim for pod density module."""

from mks.application.pod_density_summary_service import execute_pod_density_summary

execute = execute_pod_density_summary

__all__ = ["execute", "execute_pod_density_summary"]
