"""Tests for the hygiene-report join logic."""

from mks.domain.hygiene import (
    Finding,
    join_owners,
    owners_from_rows,
    parse_policyreports,
)

_POLR_PAYLOAD = {
    "items": [
        {
            "metadata": {"namespace": "click-close"},
            "scope": {"kind": "CronJob", "name": "messenger-consume"},
            "results": [
                {
                    "result": "fail",
                    "policy": "require-cronjob-hygiene",
                    "rule": "high-freq-no-concurrent-runs",
                    "severity": "medium",
                    "message": "concurrencyPolicy must be Forbid ",
                },
                {
                    "result": "pass",
                    "policy": "require-cronjob-hygiene",
                    "rule": "other",
                },
                {
                    "result": "fail",
                    "policy": "add-ttl-jobs",
                    "rule": "ttl",
                    "severity": "low",
                    "message": "ttl missing",
                },
            ],
        },
        {
            "metadata": {"namespace": "demo"},
            "scope": {"kind": "Service", "name": "web"},
            "results": [{"result": "pass", "policy": "x", "rule": "y"}],
        },
    ]
}


def test_parse_policyreports_keeps_only_fails() -> None:
    """Only failed results become findings; messages are stripped."""
    findings = parse_policyreports(_POLR_PAYLOAD)
    assert len(findings) == 2
    assert findings[0].namespace == "click-close"
    assert findings[0].kind == "CronJob"
    assert findings[0].message == "concurrencyPolicy must be Forbid"


def test_parse_policyreports_policy_filter() -> None:
    """The policy filter narrows findings to one policy."""
    findings = parse_policyreports(_POLR_PAYLOAD, policy="require-cronjob-hygiene")
    assert len(findings) == 1
    assert findings[0].rule == "high-freq-no-concurrent-runs"


def test_owners_from_rows_dedupes_and_filters() -> None:
    """Group rows and users without email are dropped; emails dedupe per ns."""
    rows = [
        {
            "namespace": "click-close",
            "subject_type": "user",
            "user_name": "Ada",
            "user_email": "ada@x.eu",
            "role_template_id": "project-owner",
        },
        {
            "namespace": "click-close",
            "subject_type": "user",
            "user_name": "Ada dup",
            "user_email": "ADA@x.eu",
            "role_template_id": "project-member",
        },
        {
            "namespace": "click-close",
            "subject_type": "group",
            "user_email": "grp@x.eu",
        },
        {"namespace": "click-close", "subject_type": "user", "user_email": ""},
    ]
    owners = owners_from_rows(rows)
    assert list(owners) == ["click-close"]
    assert len(owners["click-close"]) == 1
    assert owners["click-close"][0].email == "ada@x.eu"


def test_join_owners_attaches_by_namespace() -> None:
    """Findings get their namespace owners; unknown ns gets empty tuple."""
    findings = [
        Finding("kyverno", "click-close", "CronJob", "c", "p", "r", "medium", "m"),
        Finding("kyverno", "ghost-ns", "CronJob", "g", "p", "r", "medium", "m"),
    ]
    owners = owners_from_rows(
        [
            {
                "namespace": "click-close",
                "subject_type": "user",
                "user_name": "Ada",
                "user_email": "ada@x.eu",
                "role_template_id": "project-owner",
            }
        ]
    )
    owned = join_owners(findings, owners)
    assert owned[0].owners[0].email == "ada@x.eu"
    assert owned[1].owners == ()
    record = owned[0].to_record()
    assert record["namespace"] == "click-close"
    assert record["owners"][0]["role"] == "project-owner"
