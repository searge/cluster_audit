"""Shared kubectl execution helpers."""

import json
import shlex
import subprocess
from typing import Any, cast


class KubectlError(RuntimeError):
    """Raised when kubectl command execution fails."""


def _run_kubectl(
    command: str, *, append_json_output: bool
) -> subprocess.CompletedProcess[str]:
    args = ["kubectl", *shlex.split(command)]
    if append_json_output:
        args.extend(["-o", "json"])
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise KubectlError(f"kubectl command failed: {stderr}") from exc


def kubectl_json(command: str, *, append_json_output: bool = True) -> dict[str, Any]:
    """Execute kubectl command and parse JSON output."""
    result = _run_kubectl(command, append_json_output=append_json_output)
    try:
        return cast(dict[str, Any], json.loads(result.stdout) if result.stdout else {})
    except json.JSONDecodeError as exc:
        raise KubectlError(f"kubectl returned invalid JSON: {exc}") from exc


def kubectl_text(command: str) -> str:
    """Execute kubectl command and return text output."""
    result = _run_kubectl(command, append_json_output=False)
    return result.stdout


def kubectl_json_or_empty(command: str) -> dict[str, Any]:
    """Execute kubectl JSON command and return empty payload on failure."""
    try:
        return kubectl_json(command)
    except KubectlError as exc:
        print(f"❌ Error running kubectl: {exc}")
        return {}
