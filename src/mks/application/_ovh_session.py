"""Shared OVH session helper for OVH-API capabilities.

Opens an :class:`OvhClient`, prints the STEP 1 connect banner and the
authenticated identity, and yields the live client. Capabilities that only need
a plain (uncached) session use this to avoid repeating the connect boilerplate.
"""

from collections.abc import Generator
from contextlib import contextmanager

from mks.application._step_report import banner, ok
from mks.config import OvhConfig
from mks.infrastructure.ovh_client import OvhClient


@contextmanager
def ovh_session(ovh_config: OvhConfig) -> Generator[OvhClient]:
    """Yield an authenticated OVH client, printing the connect step."""
    banner(1, "Connect to OVH API")
    with OvhClient(ovh_config) as client:
        identity = client.get_identity()
        ok(f"authenticated as {identity.nichandle}")
        yield client


def resolve_kube_id(client: OvhClient, project_id: str, kube_id: str | None) -> str:
    """Resolve the target cluster id, defaulting to the project's sole cluster."""
    if kube_id:
        return kube_id
    kube_ids = client.list_kube_ids(project_id)
    if not kube_ids:
        raise RuntimeError(f"no MKS clusters found in project {project_id}")
    if len(kube_ids) > 1:
        raise RuntimeError(
            f"multiple clusters in project {project_id}: {kube_ids}; "
            "set OVH_KUBE_ID or pass --kube-id"
        )
    return kube_ids[0]


__all__ = ["ovh_session", "resolve_kube_id"]
