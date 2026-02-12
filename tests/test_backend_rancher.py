"""Tests for Rancher backend client."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mks.backends.rancher import RancherApiError, RancherClient


def _response(status: int, payload: dict[str, object]) -> httpx.Response:
    request = httpx.Request("GET", "https://rancher.example.com/v3/projects")
    return httpx.Response(status, json=payload, request=request)


def test_rancher_get_success() -> None:
    client = RancherClient("https://rancher.example.com", token="token")
    with patch.object(
        client,
        "_get_with_retries",
        new=AsyncMock(return_value=_response(200, {"data": [{"id": "p-1"}]})),
    ):
        assert asyncio.run(client.get("/v3/projects")) == {"data": [{"id": "p-1"}]}


def test_rancher_get_retries_with_basic_auth() -> None:
    client = RancherClient(
        "https://rancher.example.com",
        token="token",
        ak="ak",
        sk="sk",
    )
    with patch.object(
        client,
        "_get_with_retries",
        new=AsyncMock(
            side_effect=[
                _response(401, {"message": "unauthorized"}),
                _response(200, {"id": "u-1"}),
            ]
        ),
    ):
        assert asyncio.run(client.get("/v3/users/u-1")) == {"id": "u-1"}


def test_rancher_get_error() -> None:
    client = RancherClient("https://rancher.example.com", token="token")
    with (
        patch.object(
            client,
            "_get_with_retries",
            new=AsyncMock(return_value=_response(500, {"message": "internal"})),
        ),
        pytest.raises(RancherApiError),
    ):
        asyncio.run(client.get("/v3/projects"))
