#!/usr/bin/env python3
"""
Rancher Project Users Resolver
Maps namespaces -> Rancher projects and lists project users/groups.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
try:
    from dotenv import load_dotenv as _load_dotenv
except Exception:  # pragma: no cover - fallback if dependency not available
    _load_dotenv = None


@dataclass
class NamespaceInfo:
    name: str
    project_id: str
    project_name: str
    display_name: str
    description: str


def load_env_file(path: Path) -> None:
    if _load_dotenv is not None:
        _load_dotenv(path, override=False)
        return

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def run_kubectl_json(command: str) -> dict[str, Any]:
    cmd = f"kubectl {command} -o json"
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def get_namespaces_info(target_namespaces: set[str]) -> list[NamespaceInfo]:
    data = run_kubectl_json("get namespaces")
    infos: list[NamespaceInfo] = []

    for ns in data.get("items", []):
        name = ns["metadata"]["name"]
        if name not in target_namespaces:
            continue

        annotations = ns["metadata"].get("annotations", {})
        labels = ns["metadata"].get("labels", {})

        project_id = (
            annotations.get("field.cattle.io/projectId")
            or labels.get("field.cattle.io/projectId")
            or ""
        )

        project_name = annotations.get("field.cattle.io/projectName", "")
        display_name = annotations.get("field.cattle.io/displayName", name)
        description = annotations.get("field.cattle.io/description", "")

        infos.append(
            NamespaceInfo(
                name=name,
                project_id=project_id,
                project_name=project_name or project_id,
                display_name=display_name,
                description=description,
            )
        )

    return infos


class RancherClient:
    def __init__(self, base_url: str, token: str | None, ak: str | None, sk: str | None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ak = ak
        self.sk = sk
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        elif ak and sk:
            self.session.auth = (ak, sk)

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 401 and self.token and self.ak and self.sk:
            # Retry with basic auth in case bearer token is not accepted
            self.session.headers.pop("Authorization", None)
            self.session.auth = (self.ak, self.sk)
            resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Rancher API error {resp.status_code} for {path}: {resp.text[:200]}"
            )
        return resp.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map namespaces to Rancher projects and list project users"
    )
    parser.add_argument(
        "--namespaces",
        required=True,
        help="Comma-separated namespace list",
    )
    parser.add_argument(
        "--data-dir",
        default="reports",
        help="Directory to save CSV report (default: reports)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    load_env_file(Path(".env"))

    rancher_url = os.getenv("RANCHER_URL")
    rancher_token = os.getenv("RANCHER_TOKEN")
    rancher_ak = os.getenv("RANCHER_AK")
    rancher_sk = os.getenv("RANCHER_SK")

    if not rancher_url:
        print("ERROR: RANCHER_URL not set (env or .env)")
        sys.exit(1)

    if not rancher_token and not (rancher_ak and rancher_sk):
        print("ERROR: Rancher credentials not set (RANCHER_TOKEN or RANCHER_AK/RANCHER_SK)")
        sys.exit(1)

    namespaces = {ns.strip() for ns in args.namespaces.split(",") if ns.strip()}
    if not namespaces:
        print("ERROR: No namespaces provided")
        sys.exit(1)

    try:
        ns_infos = get_namespaces_info(namespaces)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: kubectl failed: {exc}")
        sys.exit(1)

    if not ns_infos:
        print("ERROR: None of the requested namespaces were found")
        sys.exit(1)

    missing = sorted(namespaces - {n.name for n in ns_infos})
    if missing:
        print(f"WARNING: Namespaces not found: {', '.join(missing)}")

    client = RancherClient(rancher_url, rancher_token, rancher_ak, rancher_sk)

    # Build project mapping
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

    # Fetch project role bindings
    for project_id, pdata in projects.items():
        if not project_id:
            continue

        data = client.get(
            "/v3/projectroletemplatebindings",
            params={"projectId": project_id},
        )
        pdata["bindings"] = data.get("data", [])

    # Optional project name normalization from API if missing
    for project_id, pdata in projects.items():
        if not project_id:
            continue
        if pdata.get("project_name"):
            continue
        try:
            project_data = client.get(f"/v3/projects/{project_id}")
            pdata["project_name"] = project_data.get("name", project_id)
        except Exception:
            pdata["project_name"] = project_id

    # Build CSV rows
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
            for b in bindings:
                subject_type = "user" if b.get("userId") else "group"
                subject_id = b.get("userId") or b.get("groupPrincipalId", "")
                subject_name = b.get("userName") or b.get("groupName", "")
                rows.append(
                    {
                        "namespace": ns.name,
                        "project_id": project_id,
                        "project_name": project_name,
                        "subject_type": subject_type,
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "role_template_id": b.get("roleTemplateId", ""),
                        "role_template_name": b.get("roleTemplateName", ""),
                    }
                )

    # Write CSV
    data_dir = Path(args.data_dir)
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
    ]

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for row in rows:
            f.write(",".join(str(row[h]).replace(",", " ") for h in headers) + "\n")

    print(f"OK: Report saved: {out_file}")


if __name__ == "__main__":
    main()
