"""Shared namespace filtering helpers."""

SYSTEM_NAMESPACES: frozenset[str] = frozenset(
    {
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "default",
        "ingress-controller",
    }
)

SYSTEM_NAMESPACE_PREFIXES: tuple[str, ...] = ("cattle-", "rancher-", "kube-")


def is_system_namespace(
    namespace: str,
    *,
    include_system: bool = False,
    extra_namespaces: frozenset[str] = frozenset(),
    extra_prefixes: tuple[str, ...] = (),
) -> bool:
    """Return whether namespace should be treated as system."""
    if include_system:
        return False
    namespaces = SYSTEM_NAMESPACES | extra_namespaces
    prefixes = SYSTEM_NAMESPACE_PREFIXES + extra_prefixes
    return namespace in namespaces or namespace.startswith(prefixes)
