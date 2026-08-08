"""Kubernetes API client for the two things Prometheus cannot answer.

Node flavours and the Rancher project a namespace belongs to are both plain
object metadata, and kube-state-metrics is configured here without label or
annotation metrics, so neither reaches Prometheus. This reads them from the API
directly.

Separate from `kubectl_client` on purpose rather than by oversight. That module
shells out to a kubectl binary, which is right for the capabilities an operator
runs from a workstation; this one runs inside a CronJob whose image is
non-root with a read-only filesystem, where the in-cluster ServiceAccount token
and no subprocess is the simpler arrangement. The library picks up either
context by itself, so the same code works from a laptop and from a pod.
"""

from typing import Any

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from mks.domain.quantity_parser import parse_cpu, parse_memory

# Without this the client inherits urllib3's default of no read timeout, so a
# blackholed API server hangs until the Job's activeDeadlineSeconds kills the
# pod. The caller can degrade around an error but not around a hang, and the
# week's snapshot would then be lost to something the ingest only needs in order
# to decorate the numbers.
_TIMEOUT_SECONDS = 30


class KubeApiError(RuntimeError):
    """The Kubernetes API could not be reached or returned something unusable."""


class KubeClient:
    """Read-only access to nodes and namespaces."""

    def __init__(self) -> None:
        """Load in-cluster credentials, falling back to the local kubeconfig."""
        try:
            config.load_incluster_config()
        except ConfigException:
            try:
                config.load_kube_config()
            except ConfigException as exc:
                raise KubeApiError(f"no usable Kubernetes context: {exc}") from exc
        self._core = client.CoreV1Api()

    def list_nodes(self) -> list[dict[str, Any]]:
        """Return ``{"name", "flavour", "pool", "cpu", "memory_gb"}`` per node.

        Both identifiers are returned because neither is reliable alone: OVH
        sets `instance-type` to the flavour name on some nodes and to an opaque
        flavour UUID on others, in the same pool, for identical hardware.

        `cpu` is allocatable rather than capacity: capacity counts what the
        kubelet and system reserve, which nothing can schedule onto, and pricing
        against it would understate what a usable core costs.

        Parsing sits inside the guard along with the call. An unexpected
        quantity suffix would otherwise escape as ValueError and take the whole
        ingest with it, when the caller is written to carry on without costs.
        """
        try:
            nodes = self._core.list_node(_request_timeout=_TIMEOUT_SECONDS)
            out: list[dict[str, Any]] = []
            for node in nodes.items:
                labels = node.metadata.labels or {}
                allocatable = node.status.allocatable or {}
                out.append(
                    {
                        "name": node.metadata.name,
                        "flavour": labels.get("node.kubernetes.io/instance-type")
                        or labels.get("beta.kubernetes.io/instance-type"),
                        "pool": labels.get("nodepool"),
                        "cpu": parse_cpu(allocatable.get("cpu", "0")) / 1000,
                        "memory_gb": parse_memory(allocatable.get("memory", "0"))
                        / 1024**3,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - ApiException, urllib3, parsing
            raise KubeApiError(f"listing nodes failed: {exc}") from exc
        return out

    def namespace_projects(self) -> dict[str, str]:
        """Map namespace to Rancher project id, skipping those without one.

        Rancher writes the id as both a label and an annotation; the label is
        read because it holds the bare id, which is what the Project objects in
        the Rancher management cluster are keyed on once the cluster prefix is
        stripped. Nothing downstream sees the display name from here.
        """
        try:
            namespaces = self._core.list_namespace(_request_timeout=_TIMEOUT_SECONDS)
            mapping: dict[str, str] = {}
            for namespace in namespaces.items:
                project = (namespace.metadata.labels or {}).get(
                    "field.cattle.io/projectId"
                )
                if project:
                    mapping[namespace.metadata.name] = project
        except Exception as exc:  # noqa: BLE001 - ApiException, urllib3
            raise KubeApiError(f"listing namespaces failed: {exc}") from exc
        return mapping


__all__ = ["KubeApiError", "KubeClient"]
