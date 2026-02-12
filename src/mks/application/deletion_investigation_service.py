#!/usr/bin/env python3
"""
Investigate resource deletions in Kubernetes namespaces.

Checks multiple sources to find who deleted pods/resources:
  1. K8s events (short-lived, ~1h TTL)
  2. Deployment/StatefulSet scale history via kubectl
  3. Rancher API: project bindings (who has access)
  4. Rancher API: tokens (last activity timestamps)
  5. Namespace annotations (modification timestamps)

Usage:
  python scripts/investigate_deletions.py --namespaces ns1,ns2,ns3
  python scripts/investigate_deletions.py --namespaces oro-commerce-demo,drupalmcptest
"""

import base64
import json
import os
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Rancher client (reused pattern from rancher_project_users.py)
# ---------------------------------------------------------------------------
class RancherClient:
    """Minimal Rancher API client using stdlib HTTP."""

    def __init__(
        self, base_url: str, token: str | None, ak: str | None, sk: str | None
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ak = ak
        self.sk = sk
        self.default_headers = {"Accept": "application/json"}

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET JSON from Rancher API."""
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"

        def do_request(headers: dict[str, str]) -> tuple[int, str]:
            req = Request(url, headers=headers, method="GET")
            try:
                with urlopen(req, timeout=30) as resp:
                    return resp.status, resp.read().decode("utf-8")
            except HTTPError as exc:
                body = exc.read().decode("utf-8") if exc.fp else ""
                return exc.code, body
            except URLError as exc:
                raise RuntimeError(f"Rancher API request failed: {exc}") from exc

        headers = dict(self.default_headers)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.ak and self.sk:
            cred = base64.b64encode(f"{self.ak}:{self.sk}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"

        status, body = do_request(headers)
        if status == 401 and self.token and self.ak and self.sk:
            cred = base64.b64encode(f"{self.ak}:{self.sk}".encode()).decode()
            retry_headers = dict(self.default_headers)
            retry_headers["Authorization"] = f"Basic {cred}"
            status, body = do_request(retry_headers)

        if status >= 400:
            raise RuntimeError(f"Rancher API error {status} for {path}: {body[:200]}")
        return cast(dict[str, Any], json.loads(body))

    def try_get(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """GET that returns None on error instead of raising."""
        try:
            return self.get(path, params)
        except RuntimeError as exc:
            print(f"  WARN: {exc}")
            return None


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------
def kubectl_json(command: str) -> dict[str, Any] | None:
    """Run kubectl and return parsed JSON, or None on error."""
    cmd = f"kubectl {command} -o json"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=True
        )
        return cast(dict[str, Any], json.loads(result.stdout))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def kubectl_text(command: str) -> str:
    """Run kubectl and return raw text output."""
    cmd = f"kubectl {command}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=True
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


# ---------------------------------------------------------------------------
# Investigation functions
# ---------------------------------------------------------------------------
def investigate_k8s_events(namespace: str) -> list[dict[str, str]]:
    """Fetch k8s events for a namespace, focusing on deletions/kills."""
    data = kubectl_json(f"get events -n {namespace} --sort-by=.lastTimestamp")
    if not data:
        return []

    interesting_reasons = {
        "Killing",
        "Deleted",
        "Evicted",
        "FailedKillPod",
        "ScalingReplicaSet",
        "SuccessfulDelete",
        "DeploymentRollback",
    }
    events: list[dict[str, str]] = []
    for item in data.get("items", []):
        reason = item.get("reason", "")
        if (
            reason in interesting_reasons
            or "delet" in (item.get("message", "")).lower()
        ):
            events.append(
                {
                    "timestamp": item.get("lastTimestamp", "")
                    or item.get("metadata", {}).get("creationTimestamp", ""),
                    "reason": reason,
                    "kind": item.get("involvedObject", {}).get("kind", ""),
                    "name": item.get("involvedObject", {}).get("name", ""),
                    "message": item.get("message", "")[:120],
                    "count": str(item.get("count", 1)),
                    "source": item.get("source", {}).get("component", ""),
                }
            )
    return events


def investigate_namespace_meta(namespace: str) -> dict[str, Any]:
    """Get namespace metadata: annotations, labels, timestamps."""
    data = kubectl_json(f"get namespace {namespace}")
    if not data:
        return {}

    meta = data.get("metadata", {})
    annotations = meta.get("annotations", {})
    labels = meta.get("labels", {})
    return {
        "name": namespace,
        "created": meta.get("creationTimestamp", ""),
        "uid": meta.get("uid", ""),
        "project_id": annotations.get("field.cattle.io/projectId", ""),
        "project_name": annotations.get("field.cattle.io/projectName", ""),
        "resource_version": meta.get("resourceVersion", ""),
        "lifecycle": annotations.get("lifecycle.cattle.io/create.namespace-auth", ""),
        "labels": {k: v for k, v in labels.items() if "cattle" in k or "rancher" in k},
    }


def investigate_deployments(namespace: str) -> list[dict[str, Any]]:
    """Check deployment status: replicas, conditions, last update."""
    data = kubectl_json(f"get deployments -n {namespace}")
    if not data:
        return []

    deps: list[dict[str, Any]] = []
    for item in data.get("items", []):
        spec = item.get("spec", {})
        status = item.get("status", {})
        conditions = status.get("conditions", [])

        last_condition = ""
        last_transition = ""
        for c in conditions:
            if c.get("type") == "Available":
                last_condition = c.get("message", "")
                last_transition = c.get("lastTransitionTime", "")

        deps.append(
            {
                "name": item["metadata"]["name"],
                "replicas_desired": spec.get("replicas", 0),
                "replicas_ready": status.get("readyReplicas", 0),
                "replicas_available": status.get("availableReplicas", 0),
                "last_transition": last_transition,
                "last_condition": last_condition[:100],
                "created": item["metadata"].get("creationTimestamp", ""),
            }
        )
    return deps


def investigate_replicasets(namespace: str) -> list[dict[str, Any]]:
    """Check recent ReplicaSet changes (scale to 0 = potential deletion)."""
    data = kubectl_json(f"get replicasets -n {namespace}")
    if not data:
        return []

    rs_list: list[dict[str, Any]] = []
    for item in data.get("items", []):
        spec_replicas = item.get("spec", {}).get("replicas", 0)
        status = item.get("status", {})
        created = item["metadata"].get("creationTimestamp", "")
        rs_list.append(
            {
                "name": item["metadata"]["name"],
                "replicas_spec": spec_replicas,
                "replicas_ready": status.get("readyReplicas", 0),
                "created": created,
            }
        )

    # Sort by creation, most recent first
    rs_list.sort(key=lambda x: x["created"], reverse=True)
    return rs_list[:10]  # last 10


def investigate_pods_age(namespace: str) -> list[dict[str, Any]]:
    """List pods with their age and restart count."""
    data = kubectl_json(f"get pods -n {namespace}")
    if not data:
        return []

    pods: list[dict[str, Any]] = []
    for item in data.get("items", []):
        containers = item.get("status", {}).get("containerStatuses", [])
        total_restarts = sum(c.get("restartCount", 0) for c in containers)
        pods.append(
            {
                "name": item["metadata"]["name"],
                "phase": item.get("status", {}).get("phase", ""),
                "created": item["metadata"].get("creationTimestamp", ""),
                "restarts": total_restarts,
                "node": item.get("spec", {}).get("nodeName", ""),
            }
        )
    return pods


def investigate_rancher_project_access(
    client: RancherClient, project_id: str
) -> list[dict[str, Any]]:
    """List who has access to a Rancher project."""
    data = client.try_get(
        "/v3/projectroletemplatebindings",
        params={"projectId": project_id},
    )
    if not data:
        return []

    bindings: list[dict[str, Any]] = []
    for b in data.get("data", []):
        user_id = b.get("userId", "")
        user_name = b.get("userName", "")
        display = user_name

        # Try to resolve user details
        if user_id and not user_name:
            user_data = client.try_get(f"/v3/users/{user_id}")
            if user_data:
                display = (
                    user_data.get("displayName", "")
                    or user_data.get("username", "")
                    or user_id
                )

        bindings.append(
            {
                "user_id": user_id,
                "user_name": display,
                "group": b.get("groupPrincipalId", ""),
                "role": b.get("roleTemplateId", ""),
                "created": b.get("created", ""),
            }
        )
    return bindings


def investigate_rancher_tokens(
    client: RancherClient,
) -> list[dict[str, Any]]:
    """List active Rancher tokens with last-used timestamps."""
    data = client.try_get("/v3/tokens", params={"limit": "100"})
    if not data:
        return []

    tokens: list[dict[str, Any]] = []
    for t in data.get("data", []):
        if t.get("expired", False):
            continue
        tokens.append(
            {
                "name": t.get("name", ""),
                "user_id": t.get("userId", ""),
                "user_name": t.get("userName", "") or t.get("description", ""),
                "auth_provider": t.get("authProvider", ""),
                "created": t.get("created", ""),
                "last_used": t.get("lastUsedAt", ""),
                "is_derived": t.get("isDerived", False),
                "cluster_id": t.get("clusterId", ""),
            }
        )

    # Sort by last_used, most recent first
    tokens.sort(key=lambda x: x.get("last_used", ""), reverse=True)
    return tokens


def investigate_rancher_cluster_members(
    client: RancherClient, cluster_id: str
) -> list[dict[str, Any]]:
    """List cluster role bindings (cluster-level access)."""
    data = client.try_get(
        "/v3/clusterroletemplatebindings",
        params={"clusterId": cluster_id},
    )
    if not data:
        return []

    members: list[dict[str, Any]] = []
    for b in data.get("data", []):
        user_id = b.get("userId", "")
        user_name = b.get("userName", "")

        if user_id and not user_name:
            user_data = client.try_get(f"/v3/users/{user_id}")
            if user_data:
                user_name = (
                    user_data.get("displayName", "")
                    or user_data.get("username", "")
                    or user_id
                )

        members.append(
            {
                "user_id": user_id,
                "user_name": user_name,
                "group": b.get("groupPrincipalId", ""),
                "role": b.get("roleTemplateId", ""),
                "created": b.get("created", ""),
            }
        )
    return members


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def print_section(title: str) -> None:
    """Print a top-level section separator to stdout."""
    width = 72
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_subsection(title: str) -> None:
    """Print a subsection title to stdout."""
    print(f"\n--- {title} ---")


def _report_namespace_details(
    ns: str,
    *,
    client: RancherClient | None,
    out: Callable[[str], None],
) -> None:
    """Emit investigation details for one namespace."""
    out("")
    out("=" * 72)
    out(f"  NAMESPACE: {ns}")
    out("=" * 72)

    meta = _report_namespace_metadata(ns, out=out)
    if meta is None:
        out(f"  WARNING: Namespace '{ns}' not found!")
        return

    _report_current_pods(ns, out=out)
    _report_deployments(ns, out=out)
    _report_recent_replicasets(ns, out=out)
    _report_deletion_events(ns, out=out)

    project_id = meta.get("project_id", "")
    if client and project_id:
        _report_rancher_project_access(client, project_id=project_id, out=out)


def _report_namespace_metadata(
    ns: str,
    *,
    out: Callable[[str], None],
) -> dict[str, Any] | None:
    """Print namespace metadata and return collected metadata map."""
    out("\n--- Namespace metadata ---")
    meta = investigate_namespace_meta(ns)
    if not meta:
        return None
    out(f"  Created:        {meta.get('created', 'N/A')}")
    out(f"  UID:            {meta.get('uid', 'N/A')}")
    out(f"  Project ID:     {meta.get('project_id', 'N/A')}")
    out(f"  Project Name:   {meta.get('project_name', 'N/A')}")
    out(f"  Resource Ver:   {meta.get('resource_version', 'N/A')}")
    if meta.get("labels"):
        out(f"  Rancher labels: {meta['labels']}")
    return meta


def _report_current_pods(ns: str, *, out: Callable[[str], None]) -> None:
    """Print currently running pods in a namespace."""
    out("\n--- Current pods ---")
    pods = investigate_pods_age(ns)
    if not pods:
        out("  No pods running.")
        return
    out(f"  {'POD':<55} {'PHASE':<12} {'RESTARTS':>8}  CREATED")
    for pod in pods:
        out(
            f"  {pod['name']:<55} {pod['phase']:<12} "
            f"{pod['restarts']:>8}  {pod['created']}"
        )


def _report_deployments(ns: str, *, out: Callable[[str], None]) -> None:
    """Print deployments and their replica status."""
    out("\n--- Deployments ---")
    deployments = investigate_deployments(ns)
    if not deployments:
        out("  No deployments.")
        return
    out(
        f"  {'DEPLOYMENT':<45} {'DESIRED':>7} {'READY':>7} "
        f"{'AVAIL':>7}  LAST TRANSITION"
    )
    for dep in deployments:
        out(
            f"  {dep['name']:<45} "
            f"{dep['replicas_desired']:>7} "
            f"{dep['replicas_ready']:>7} "
            f"{dep['replicas_available']:>7}  "
            f"{dep['last_transition']}"
        )


def _report_recent_replicasets(ns: str, *, out: Callable[[str], None]) -> None:
    """Print recent replicasets for the namespace."""
    out("\n--- Recent ReplicaSets (last 10) ---")
    replicasets = investigate_replicasets(ns)
    if not replicasets:
        return
    out(f"  {'REPLICASET':<55} {'SPEC':>5} {'READY':>5}  CREATED")
    for rs in replicasets:
        out(
            f"  {rs['name']:<55} {rs['replicas_spec']:>5} "
            f"{rs['replicas_ready']:>5}  {rs['created']}"
        )


def _report_deletion_events(ns: str, *, out: Callable[[str], None]) -> None:
    """Print namespace events related to deletion and scaling."""
    out("\n--- K8s events (deletion/kill/scale related) ---")
    events = investigate_k8s_events(ns)
    if not events:
        out("  No deletion-related events found (events expire in ~1h).")
        return
    for event in events:
        out(
            f"  [{event['timestamp']}] {event['reason']:<22} "
            f"{event['kind']:<15} {event['name']:<40}"
        )
        out(f"    {event['message']}")


def _report_rancher_project_access(
    client: RancherClient,
    *,
    project_id: str,
    out: Callable[[str], None],
) -> None:
    """Print Rancher project role bindings for a namespace project."""
    out(f"\n--- Rancher project access ({project_id}) ---")
    access = investigate_rancher_project_access(client, project_id)
    if not access:
        out("  No project bindings found.")
        return
    out(f"  {'USER/GROUP':<40} {'ROLE':<25} CREATED")
    for item in access:
        who = item["user_name"] or item["group"] or "(unknown)"
        out(f"  {who:<40} {item['role']:<25} {item['created']}")


def _report_cluster_wide_activity(
    *,
    client: RancherClient,
    cluster_id: str,
    out: Callable[[str], None],
) -> None:
    """Emit cluster-wide Rancher token and member activity sections."""
    out("")
    out("=" * 72)
    out("  CLUSTER-WIDE: Recent token activity")
    out("=" * 72)

    tokens = investigate_rancher_tokens(client)
    if tokens:
        out(f"\n  {'USER':<30} {'TOKEN':<20} {'LAST USED':<25} PROVIDER")
        for token in tokens[:30]:
            user = token["user_name"] or token["user_id"]
            out(
                f"  {user:<30} {token['name']:<20} "
                f"{token['last_used']:<25} {token['auth_provider']}"
            )
    else:
        out("  Could not fetch token info.")

    out("")
    out("=" * 72)
    out(f"  CLUSTER MEMBERS ({cluster_id})")
    out("=" * 72)

    members = investigate_rancher_cluster_members(client, cluster_id)
    if members:
        out(f"\n  {'USER/GROUP':<40} {'ROLE':<25} CREATED")
        for member in members:
            who = member["user_name"] or member["group"] or "(unknown)"
            out(f"  {who:<40} {member['role']:<25} {member['created']}")


def _report_investigation_hints(out: Callable[[str], None]) -> None:
    """Emit static investigation hints section."""
    out("")
    out("=" * 72)
    out("  INVESTIGATION HINTS")
    out("=" * 72)
    out("""
  1. K8s events are short-lived (~1h). If the deletion happened earlier,
     events will be gone. Check Rancher server logs directly:
       - SSH to Rancher host, check /var/log/auditlog/ (if audit enabled)
       - Or: kubectl -n cattle-system logs deploy/rancher | grep DELETE

  2. OVH MKS: OVH may store k8s API audit logs in their control panel.
     Check: OVH Manager -> Public Cloud -> Managed Kubernetes -> Logs

  3. Rancher token table above shows who was active recently. Cross-reference
     with the project access list to narrow suspects.

  4. If pods were deleted (not scaled down), look for ReplicaSets with
     recent creation timestamps — controllers recreate pods after deletion.

  5. If deployments show replicas=0, someone scaled them down explicitly
     (kubectl scale / Rancher UI). This is different from pod deletion.

  6. For the @befon case (visa-platform-shared): namespace deletion removes
     ALL resources. If ns was recreated (age ~28h), everything in it is gone.
     Check Rancher project assignment to see if ns was re-attached.
""")


def _report_namespaces(
    namespaces: list[str],
    *,
    client: RancherClient | None,
    out: Callable[[str], None],
) -> None:
    """Emit per-namespace investigation sections."""
    for namespace in namespaces:
        _report_namespace_details(namespace, client=client, out=out)


def generate_report(
    namespaces: list[str],
    client: RancherClient | None,
    cluster_id: str,
    output_dir: Path,
) -> Path:
    """Run full investigation and produce a report."""
    report_path = output_dir / (
        f"deletion_investigation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    output_dir.mkdir(exist_ok=True)

    # Capture all output to both stdout and file
    lines: list[str] = []

    def out(text: str = "") -> None:
        print(text)
        lines.append(text)

    out(f"DELETION INVESTIGATION REPORT — {datetime.now().isoformat()}")
    out(f"Namespaces: {', '.join(namespaces)}")

    _report_namespaces(namespaces, client=client, out=out)

    if client:
        _report_cluster_wide_activity(client=client, cluster_id=cluster_id, out=out)
    _report_investigation_hints(out)

    # Save report
    report_path.write_text("\n".join(lines), encoding="utf-8")
    out(f"\nReport saved: {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_config() -> tuple[str, str | None, str | None, str | None]:
    """Load Rancher config from .env."""
    load_dotenv(Path(".env"), override=False)

    rancher_url = os.getenv("RANCHER_URL", "")
    rancher_token = os.getenv("RANCHER_TOKEN")
    rancher_ak = os.getenv("RANCHER_AK")
    rancher_sk = os.getenv("RANCHER_SK")
    return rancher_url, rancher_token, rancher_ak, rancher_sk


def detect_cluster_id(client: RancherClient) -> str:
    """Auto-detect cluster ID (first non-local cluster)."""
    data = client.try_get("/v3/clusters")
    if not data:
        return ""
    for c in data.get("data", []):
        if c.get("id") != "local":
            return cast(str, c["id"])
    return ""


def execute_deletion_investigation(
    namespaces_raw: str,
    output_dir: str = "reports",
    *,
    skip_rancher: bool = False,
) -> Path:
    """Execute deletion investigation use-case and return report path."""
    namespaces = [ns.strip() for ns in namespaces_raw.split(",") if ns.strip()]
    if not namespaces:
        raise ValueError("No namespaces provided")

    client: RancherClient | None = None
    cluster_id = ""

    if not skip_rancher:
        rancher_url, rancher_token, rancher_ak, rancher_sk = load_config()
        if rancher_url and (rancher_token or (rancher_ak and rancher_sk)):
            client = RancherClient(rancher_url, rancher_token, rancher_ak, rancher_sk)
            cluster_id = detect_cluster_id(client)
            print(f"Rancher: {rancher_url} | Cluster: {cluster_id}")
        else:
            print("WARN: Rancher not configured, running kubectl-only mode")

    return generate_report(namespaces, client, cluster_id, Path(output_dir))


def execute(
    namespaces_raw: str,
    output_dir: str = "reports",
    *,
    skip_rancher: bool = False,
) -> Path:
    """Backward-compatible alias for execute_deletion_investigation."""
    return execute_deletion_investigation(
        namespaces_raw,
        output_dir=output_dir,
        skip_rancher=skip_rancher,
    )
