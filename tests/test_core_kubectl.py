"""Tests for kubectl helper functions."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from mks.core.kubectl import KubectlError, kubectl_json, kubectl_text


def test_kubectl_json_success() -> None:
    completed = subprocess.CompletedProcess(
        args=["kubectl", "get", "pods", "-o", "json"],
        returncode=0,
        stdout=json.dumps({"items": []}),
        stderr="",
    )
    with patch("mks.core.kubectl.subprocess.run", return_value=completed):
        assert kubectl_json("get pods") == {"items": []}


def test_kubectl_text_success() -> None:
    completed = subprocess.CompletedProcess(
        args=["kubectl", "get", "pods"],
        returncode=0,
        stdout="ok\n",
        stderr="",
    )
    with patch("mks.core.kubectl.subprocess.run", return_value=completed):
        assert kubectl_text("get pods") == "ok\n"


def test_kubectl_json_invalid_json() -> None:
    completed = subprocess.CompletedProcess(
        args=["kubectl", "get", "pods", "-o", "json"],
        returncode=0,
        stdout="{invalid}",
        stderr="",
    )
    with (
        patch("mks.core.kubectl.subprocess.run", return_value=completed),
        pytest.raises(KubectlError),
    ):
        kubectl_json("get pods")


def test_kubectl_command_failure() -> None:
    with (
        patch(
            "mks.core.kubectl.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1,
                ["kubectl", "get", "pods"],
                stderr="cluster unavailable",
            ),
        ),
        pytest.raises(KubectlError),
    ):
        kubectl_json("get pods")
