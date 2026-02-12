"""Backward-compatible shim for namespace filtering."""

from mks.domain.namespace_policy import (
    SYSTEM_NAMESPACE_PREFIXES,
    SYSTEM_NAMESPACES,
    is_system_namespace,
)

__all__ = ["SYSTEM_NAMESPACES", "SYSTEM_NAMESPACE_PREFIXES", "is_system_namespace"]
