"""Backward-compatible shim for kubectl helpers."""

from mks.infrastructure.kubectl_client import KubectlError, kubectl_json, kubectl_text

__all__ = ["KubectlError", "kubectl_json", "kubectl_text"]
