"""Backward-compatible shim for Rancher client."""

from mks.infrastructure.rancher_client import RancherApiError, RancherClient

__all__ = ["RancherApiError", "RancherClient"]
