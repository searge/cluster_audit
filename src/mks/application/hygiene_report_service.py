"""Hygiene report service: policy violations joined with Rancher owners.

Produces the outbound contract for notification tooling (Swarm connector,
mailer): ``hygiene_report.jsonl`` — one finding per line with the responsible
owners resolved through Rancher project bindings — plus a per-namespace
summary CSV for humans.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from mks.application._step_report import banner, info, ok, warn
from mks.application.rancher_users_export_service import (
    build_project_mapping,
    build_rows,
    collect_projects_and_users,
    get_namespaces_info,
    resolve_rancher_credentials,
)
from mks.application.use_case_utils import write_csv
from mks.config import RancherConfig
from mks.domain.hygiene import (
    OwnedFinding,
    join_owners,
    owners_from_rows,
    parse_policyreports,
)
from mks.infrastructure.kubectl_client import kubectl_json


def _fetch_owner_rows(
    namespaces: set[str],
    *,
    rancher_config: RancherConfig | None,
    cache_dir: str,
    cache_ttl_seconds: int,
) -> list[dict[str, Any]]:
    """Resolve namespace owners via Rancher project bindings."""
    rancher_cfg = resolve_rancher_credentials(rancher_config)
    ns_infos = get_namespaces_info(namespaces)
    projects = build_project_mapping(ns_infos)
    user_map = asyncio.run(
        collect_projects_and_users(
            projects,
            rancher_cfg=rancher_cfg,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    )
    return build_rows(projects, user_map)


def _write_jsonl(data_dir: str, owned: list[OwnedFinding]) -> Path:
    path = Path(data_dir) / "hygiene_report.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for item in owned:
            handle.write(json.dumps(item.to_record(), ensure_ascii=False) + "\n")
    return path


def _write_summary_csv(data_dir: str, owned: list[OwnedFinding]) -> Path:
    per_ns: dict[str, dict[str, Any]] = {}
    for item in owned:
        entry = per_ns.setdefault(
            item.finding.namespace,
            {"findings": 0, "emails": set()},
        )
        entry["findings"] += 1
        entry["emails"].update(o.email for o in item.owners)
    rows = [
        [ns, data["findings"], len(data["emails"]), ";".join(sorted(data["emails"]))]
        for ns, data in sorted(per_ns.items())
    ]
    return write_csv(
        Path(data_dir) / "hygiene_summary.csv",
        ["namespace", "findings", "owners", "owner_emails"],
        rows,
    )


def execute_hygiene_report(
    *,
    data_dir: str,
    policy: str | None = None,
    rancher_config: RancherConfig | None = None,
    cache_dir: str = "cache/rancher_users",
    cache_ttl_seconds: int = 3600,
) -> str:
    """Collect failed policy results, resolve owners, write the contract files.

    Returns the JSONL path. Raises ``KubectlError`` / ``ValueError`` on backend
    failure or missing Rancher credentials.
    """
    banner(1, "Fetch PolicyReports (kubectl)")
    payload = kubectl_json("get policyreports -A")
    findings = parse_policyreports(payload, policy=policy)
    scope = f"policy={policy}" if policy else "all policies"
    ok(f"{len(findings)} failed results ({scope})")
    if not findings:
        info("nothing to report; writing empty contract files")
        _write_jsonl(data_dir, [])
        _write_summary_csv(data_dir, [])
        return str(Path(data_dir) / "hygiene_report.jsonl")

    banner(2, "Resolve namespace owners via Rancher")
    namespaces = {f.namespace for f in findings if f.namespace}
    rows = _fetch_owner_rows(
        namespaces,
        rancher_config=rancher_config,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    owners_by_ns = owners_from_rows(rows)
    ok(f"{len(namespaces)} namespaces, {len(owners_by_ns)} with resolvable owners")
    orphan_ns = sorted(namespaces - set(owners_by_ns))
    if orphan_ns:
        warn(f"no owner emails for: {', '.join(orphan_ns)}")

    banner(3, "Join and write contract")
    owned = join_owners(findings, owners_by_ns)
    jsonl_path = _write_jsonl(data_dir, owned)
    _write_summary_csv(data_dir, owned)
    covered = sum(1 for item in owned if item.owners)
    ok(f"{len(owned)} findings written, {covered} with owners")
    info("consumers: Swarm connector / mailer read hygiene_report.jsonl")
    return str(jsonl_path)


__all__ = ["execute_hygiene_report"]
