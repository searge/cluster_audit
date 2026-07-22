"""Pure logic for the hygiene report: findings joined with namespace owners.

This is the outbound contract for notification tooling (e.g. a Swarm
connector): one JSONL line per finding, already enriched with the Rancher
project owners responsible for the namespace. Sources are pluggable; v1 reads
Kyverno PolicyReports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, NamedTuple


@dataclass(frozen=True)
class Owner:
    """One person responsible for a namespace (from Rancher bindings)."""

    name: str
    email: str
    role: str


class Finding(NamedTuple):
    """One policy violation attributed to a namespaced resource."""

    source: str  # e.g. "kyverno"
    namespace: str
    kind: str
    name: str
    policy: str
    rule: str
    severity: str
    message: str


@dataclass(frozen=True)
class OwnedFinding:
    """A finding with the owners who should receive it."""

    finding: Finding
    owners: tuple[Owner, ...] = field(default_factory=tuple)

    def to_record(self) -> dict[str, Any]:
        """Flatten to a JSONL-ready dict."""
        record = self.finding._asdict()
        record["owners"] = [asdict(o) for o in self.owners]
        return record


def parse_policyreports(
    payload: dict[str, Any], *, policy: str | None = None
) -> list[Finding]:
    """Extract failed results from a PolicyReport list payload.

    ``payload`` is ``kubectl get policyreports -A -o json``. Only entries with
    ``result == "fail"`` become findings; ``policy`` narrows to one policy.
    """
    findings: list[Finding] = []
    for item in payload.get("items", []):
        namespace = str(item.get("metadata", {}).get("namespace", ""))
        scope = item.get("scope") or {}
        kind = str(scope.get("kind", ""))
        name = str(scope.get("name", ""))
        for result in item.get("results", []):
            if result.get("result") != "fail":
                continue
            if policy and result.get("policy") != policy:
                continue
            findings.append(
                Finding(
                    source="kyverno",
                    namespace=namespace,
                    kind=kind,
                    name=name,
                    policy=str(result.get("policy", "")),
                    rule=str(result.get("rule", "")),
                    severity=str(result.get("severity", "")),
                    message=str(result.get("message", "")).strip(),
                )
            )
    return findings


def owners_from_rows(rows: list[dict[str, Any]]) -> dict[str, list[Owner]]:
    """Build namespace -> owners from Rancher users-export rows.

    Keeps only user subjects with an email; deduplicates by email per
    namespace, preserving first-seen order.
    """
    by_ns: dict[str, list[Owner]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row.get("subject_type") != "user":
            continue
        email = str(row.get("user_email", "")).strip()
        if not email:
            continue
        namespace = str(row.get("namespace", ""))
        key = (namespace, email.lower())
        if key in seen:
            continue
        seen.add(key)
        by_ns.setdefault(namespace, []).append(
            Owner(
                name=str(row.get("user_name") or row.get("subject_name") or ""),
                email=email,
                role=str(row.get("role_template_id", "")),
            )
        )
    return by_ns


def join_owners(
    findings: list[Finding], owners_by_ns: dict[str, list[Owner]]
) -> list[OwnedFinding]:
    """Attach owners to each finding by namespace (empty tuple when unknown)."""
    return [
        OwnedFinding(finding=f, owners=tuple(owners_by_ns.get(f.namespace, ())))
        for f in findings
    ]


__all__ = [
    "Finding",
    "OwnedFinding",
    "Owner",
    "join_owners",
    "owners_from_rows",
    "parse_policyreports",
]
