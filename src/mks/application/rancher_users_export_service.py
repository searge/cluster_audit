#!/usr/bin/env python3
"""Rancher Project Users Resolver."""

import asyncio
import importlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from mks.config import RancherConfig
from mks.domain.rancher_namespace import extract_rancher_namespace_project
from mks.infrastructure.kubectl_client import kubectl_json
from mks.infrastructure.rancher_client import (
    RancherApiError,
    RancherAuth,
    RancherClient,
)


@dataclass
class NamespaceInfo:
    """Namespace to Rancher project mapping details."""

    name: str
    project_id: str
    project_name: str
    display_name: str
    description: str


def get_namespaces_info(target_namespaces: set[str]) -> list[NamespaceInfo]:
    """Fetch namespace -> project mapping from annotations/labels."""
    data = kubectl_json("get namespaces")
    infos: list[NamespaceInfo] = []

    for ns in data.get("items", []):
        name = ns["metadata"]["name"]
        if name not in target_namespaces:
            continue

        project_meta = extract_rancher_namespace_project(ns)

        infos.append(
            NamespaceInfo(
                name=name,
                project_id=project_meta.project_id,
                project_name=project_meta.project_name,
                display_name=project_meta.display_name,
                description=project_meta.description,
            )
        )

    return infos


def resolve_rancher_credentials(
    rancher_config: RancherConfig | None,
) -> tuple[str, str | None, str | None, str | None]:
    """Resolve and validate Rancher credentials."""
    if rancher_config is None:
        raise ValueError("Rancher config was not provided")
    rancher_url = rancher_config.url
    rancher_token = rancher_config.token
    rancher_ak = rancher_config.ak
    rancher_sk = rancher_config.sk
    if not rancher_url:
        raise ValueError("RANCHER_URL not set (env or .env)")
    if not rancher_token and not (rancher_ak and rancher_sk):
        raise ValueError(
            "Rancher credentials not set (RANCHER_TOKEN or RANCHER_AK/RANCHER_SK)"
        )
    return rancher_url, rancher_token, rancher_ak, rancher_sk


def parse_namespaces(raw: str) -> set[str]:
    """Parse comma-separated namespaces list.

    Parameters
    ----------
    raw : str
        Raw CSV string with namespace names.

    Returns
    -------
    set[str]
        Normalized unique namespaces.
    """
    namespaces = {ns.strip() for ns in raw.split(",") if ns.strip()}
    if not namespaces:
        raise ValueError("No namespaces provided")
    return namespaces


def fetch_ns_infos(namespaces: set[str]) -> list[NamespaceInfo]:
    """Load namespace info, failing fast on kubectl errors."""
    ns_infos = get_namespaces_info(namespaces)
    if not ns_infos:
        raise ValueError("None of the requested namespaces were found")
    return ns_infos


def warn_missing_namespaces(requested: set[str], ns_infos: list[NamespaceInfo]) -> None:
    """Warn about namespaces that were not found."""
    missing = sorted(requested - {n.name for n in ns_infos})
    if missing:
        print(f"WARNING: Namespaces not found: {', '.join(missing)}")


def build_project_mapping(ns_infos: list[NamespaceInfo]) -> dict[str, dict[str, Any]]:
    """Build project -> namespaces mapping."""
    projects: dict[str, dict[str, Any]] = {}
    for info in ns_infos:
        if not info.project_id:
            projects[f"standalone-{info.name}"] = {
                "project_id": "",
                "project_name": info.project_name,
                "namespaces": [info],
                "bindings": [],
            }
            continue
        if info.project_id not in projects:
            projects[info.project_id] = {
                "project_id": info.project_id,
                "project_name": info.project_name,
                "namespaces": [info],
                "bindings": [],
            }
        else:
            projects[info.project_id]["namespaces"].append(info)
    return projects


async def _get_with_semaphore(
    semaphore: asyncio.Semaphore,
    coro: Any,
) -> Any:
    async with semaphore:
        return await coro


async def fetch_project_bindings(
    client: RancherClient,
    projects: dict[str, dict[str, Any]],
) -> None:
    """Fetch project bindings concurrently and attach them to mapping.

    Parameters
    ----------
    client : RancherClient
        Async Rancher API client.
    projects : dict[str, dict[str, Any]]
        Project mapping to enrich with bindings.
    """
    semaphore = asyncio.Semaphore(10)
    project_ids = [pid for pid in projects if pid]
    tasks = {
        project_id: asyncio.create_task(
            _get_with_semaphore(
                semaphore,
                client.get(
                    "/v3/projectroletemplatebindings",
                    params={"projectId": project_id},
                ),
            )
        )
        for project_id in project_ids
    }
    for project_id, task in tasks.items():
        data = await task
        projects[project_id]["bindings"] = data.get("data", [])


async def normalize_project_names(
    client: RancherClient,
    projects: dict[str, dict[str, Any]],
    cache: Any,
    cache_ttl_seconds: int,
) -> None:
    """Fill missing project names using cache then API for misses only.

    Parameters
    ----------
    client : RancherClient
        Async Rancher API client.
    projects : dict[str, dict[str, Any]]
        Project mapping to enrich.
    cache : Cache
        Disk cache handle.
    cache_ttl_seconds : int
        TTL for cached values.
    """
    missing_ids: list[str] = []
    for project_id, pdata in projects.items():
        if not project_id or pdata.get("project_name"):
            continue
        cache_key = f"project:{project_id}"
        cached = cast(dict[str, Any] | None, cache.get(cache_key))
        if cached is not None:
            pdata["project_name"] = cached.get("name", project_id)
        else:
            missing_ids.append(project_id)

    semaphore = asyncio.Semaphore(10)
    tasks = {
        project_id: asyncio.create_task(
            _get_with_semaphore(semaphore, client.get(f"/v3/projects/{project_id}"))
        )
        for project_id in missing_ids
    }
    for project_id, task in tasks.items():
        try:
            project_data = await task
            cache.set(f"project:{project_id}", project_data, expire=cache_ttl_seconds)
            projects[project_id]["project_name"] = project_data.get("name", project_id)
        except RancherApiError:
            projects[project_id]["project_name"] = project_id


async def fetch_user_map(
    client: RancherClient,
    projects: dict[str, dict[str, Any]],
    cache: Any,
    cache_ttl_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Build user details map from cache + async API misses.

    Parameters
    ----------
    client : RancherClient
        Async Rancher API client.
    projects : dict[str, dict[str, Any]]
        Project mapping containing bindings with user IDs.
    cache : Cache
        Disk cache handle.
    cache_ttl_seconds : int
        TTL for cached values.

    Returns
    -------
    dict[str, dict[str, Any]]
        User-id keyed details map.
    """
    all_user_ids = {
        binding.get("userId", "")
        for pdata in projects.values()
        for binding in pdata.get("bindings", [])
        if binding.get("userId", "")
    }

    user_map: dict[str, dict[str, Any]] = {}
    missing_user_ids: list[str] = []
    for user_id in all_user_ids:
        cache_key = f"user:{user_id}"
        cached = cast(dict[str, Any] | None, cache.get(cache_key))
        if cached is not None:
            user_map[user_id] = cached
        else:
            missing_user_ids.append(user_id)

    semaphore = asyncio.Semaphore(20)
    tasks = {
        user_id: asyncio.create_task(
            _get_with_semaphore(semaphore, client.get_user(user_id))
        )
        for user_id in missing_user_ids
    }
    for user_id, task in tasks.items():
        try:
            user_data = await task
        except RancherApiError:
            user_data = {}
        user_map[user_id] = user_data
        cache.set(f"user:{user_id}", user_data, expire=cache_ttl_seconds)

    return user_map


def build_rows(
    projects: dict[str, dict[str, Any]],
    user_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build CSV rows with user enrichment."""
    rows: list[dict[str, Any]] = []
    for project_id, pdata in projects.items():
        namespaces_list = pdata.get("namespaces", [])
        bindings = pdata.get("bindings", [])
        project_name = pdata.get("project_name", project_id)
        if not bindings:
            for ns in namespaces_list:
                rows.append(
                    {
                        "namespace": ns.name,
                        "project_id": project_id,
                        "project_name": project_name,
                        "subject_type": "(none)",
                        "subject_id": "",
                        "subject_name": "",
                        "role_template_id": "",
                        "role_template_name": "",
                    }
                )
            continue

        for ns in namespaces_list:
            for binding in bindings:
                user_id = binding.get("userId", "")
                subject_type = "user" if user_id else "group"
                subject_id = user_id or binding.get("groupPrincipalId", "")
                subject_name = binding.get("userName") or binding.get("groupName", "")
                user_details = user_map.get(user_id, {}) if user_id else {}
                rows.append(
                    {
                        "namespace": ns.name,
                        "project_id": project_id,
                        "project_name": project_name,
                        "subject_type": subject_type,
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "role_template_id": binding.get("roleTemplateId", ""),
                        "role_template_name": binding.get("roleTemplateName", ""),
                        "user_username": user_details.get("username", ""),
                        "user_display_name": user_details.get("displayName", ""),
                        "user_name": user_details.get("name", ""),
                        "user_email": user_details.get("email", ""),
                        "user_enabled": user_details.get("enabled", ""),
                    }
                )
    return rows


def write_csv(rows: list[dict[str, Any]], data_dir: Path) -> Path:
    """Write CSV report to disk."""
    data_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = data_dir / f"rancher_project_users_{timestamp}.csv"
    headers = [
        "namespace",
        "project_id",
        "project_name",
        "subject_type",
        "subject_id",
        "subject_name",
        "role_template_id",
        "role_template_name",
        "user_username",
        "user_display_name",
        "user_name",
        "user_email",
        "user_enabled",
    ]
    with open(out_file, "w", encoding="utf-8") as f:
        lines = [",".join(headers)]
        lines.extend(
            ",".join(str(row.get(h, "")).replace(",", " ") for h in headers)
            for row in rows
        )
        f.writelines(f"{line}\n" for line in lines)
    return out_file


async def _collect_projects_and_users(
    projects: dict[str, dict[str, Any]],
    *,
    rancher_cfg: tuple[str, str | None, str | None, str | None],
    cache_dir: str,
    cache_ttl_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Enrich project mapping with bindings and return user details map."""
    rancher_url, rancher_token, rancher_ak, rancher_sk = rancher_cfg
    async with RancherClient(
        rancher_url,
        RancherAuth(token=rancher_token, ak=rancher_ak, sk=rancher_sk),
    ) as client:
        cache_cls = importlib.import_module("diskcache").Cache
        with cache_cls(cache_dir) as cache:
            await fetch_project_bindings(client, projects)
            await normalize_project_names(client, projects, cache, cache_ttl_seconds)
            return await fetch_user_map(client, projects, cache, cache_ttl_seconds)


async def execute_rancher_users_export_async(
    namespaces_raw: str,
    data_dir: str = "reports",
    *,
    rancher_config: RancherConfig | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> Path:
    """Run Rancher users export asynchronously.

    Parameters
    ----------
    namespaces_raw : str
        Comma-separated namespaces.
    data_dir : str
        Output directory for report.
    cache_dir : str
        On-disk cache directory.
    cache_ttl_seconds : int
        Cache TTL in seconds.

    Returns
    -------
    Path
        Path to generated CSV report.
    """
    namespaces = parse_namespaces(namespaces_raw)
    rancher_url, rancher_token, rancher_ak, rancher_sk = resolve_rancher_credentials(
        rancher_config
    )

    ns_infos = fetch_ns_infos(namespaces)
    warn_missing_namespaces(namespaces, ns_infos)
    projects = build_project_mapping(ns_infos)

    user_map = await _collect_projects_and_users(
        projects,
        rancher_cfg=(rancher_url, rancher_token, rancher_ak, rancher_sk),
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )

    rows = build_rows(projects, user_map)
    return write_csv(rows, Path(data_dir))


def execute_rancher_users_export(
    namespaces_raw: str,
    data_dir: str = "reports",
    *,
    rancher_config: RancherConfig | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> Path:
    """Run Rancher users export from synchronous context.

    Parameters
    ----------
    namespaces_raw : str
        Comma-separated namespaces.
    data_dir : str
        Output directory for report.
    cache_dir : str
        On-disk cache directory.
    cache_ttl_seconds : int
        Cache TTL in seconds.

    Returns
    -------
    Path
        Path to generated CSV report.
    """
    return asyncio.run(
        execute_rancher_users_export_async(
            namespaces_raw,
            data_dir=data_dir,
            rancher_config=rancher_config,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    )


async def execute_async(
    namespaces_raw: str,
    data_dir: str = "reports",
    *,
    rancher_config: RancherConfig | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> Path:
    """Backward-compatible alias for execute_rancher_users_export_async."""
    return await execute_rancher_users_export_async(
        namespaces_raw,
        data_dir=data_dir,
        rancher_config=rancher_config,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )


def execute(
    namespaces_raw: str,
    data_dir: str = "reports",
    *,
    rancher_config: RancherConfig | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> Path:
    """Backward-compatible alias for execute_rancher_users_export."""
    return execute_rancher_users_export(
        namespaces_raw,
        data_dir=data_dir,
        rancher_config=rancher_config,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )
