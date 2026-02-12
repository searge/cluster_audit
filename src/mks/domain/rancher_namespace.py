"""Helpers for Rancher project metadata stored on namespace objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RancherNamespaceProject:
    """Project metadata extracted from namespace labels/annotations."""

    project_id: str
    project_name: str
    display_name: str
    description: str
    created: str


def extract_rancher_namespace_project(ns: dict[str, Any]) -> RancherNamespaceProject:
    """Extract Rancher project mapping fields from namespace object."""
    metadata = ns.get("metadata", {})
    name = metadata.get("name", "")
    annotations = metadata.get("annotations", {})
    labels = metadata.get("labels", {})

    project_id = (
        annotations.get("field.cattle.io/projectId")
        or labels.get("field.cattle.io/projectId")
        or ""
    )
    project_name = annotations.get("field.cattle.io/projectName", "") or project_id
    return RancherNamespaceProject(
        project_id=project_id,
        project_name=project_name,
        display_name=annotations.get("field.cattle.io/displayName", name),
        description=annotations.get("field.cattle.io/description", ""),
        created=metadata.get("creationTimestamp", ""),
    )
