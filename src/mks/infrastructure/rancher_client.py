"""Async Rancher backend client."""

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

import httpx


class RancherApiError(RuntimeError):
    """Rancher API request failed."""


@dataclass(frozen=True)
class RancherAuth:
    """Authentication material for Rancher API."""

    token: str | None = None
    ak: str | None = None
    sk: str | None = None


@dataclass(frozen=True)
class RancherClientConfig:
    """Runtime tuning options for Rancher API calls."""

    timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.3


class RancherClient:
    """Rancher API client with Bearer and Basic auth support."""

    def __init__(
        self,
        base_url: str,
        auth: RancherAuth,
        config: RancherClientConfig | None = None,
    ) -> None:
        """Create Rancher API client.

        Parameters
        ----------
        base_url : str
            Rancher base URL.
        auth : RancherAuth
            Auth credentials (token and/or access key pair).
        config : RancherClientConfig | None
            Runtime tuning options.
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.config = config or RancherClientConfig()
        self._client: httpx.AsyncClient | None = None
        self._default_headers = {"Accept": "application/json"}

    async def __aenter__(self) -> "RancherClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.config.timeout_seconds,
            )
        return self._client

    def _auth_header(self, *, force_basic: bool = False) -> str | None:
        if not force_basic and self.auth.token:
            return f"Bearer {self.auth.token}"
        if self.auth.ak and self.auth.sk:
            cred = base64.b64encode(f"{self.auth.ak}:{self.auth.sk}".encode()).decode(
                "utf-8"
            )
            return f"Basic {cred}"
        return None

    async def _get_with_retries(
        self,
        path: str,
        params: dict[str, str] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Perform GET with retry/backoff.

        Parameters
        ----------
        path : str
            API path.
        params : dict[str, str] | None
            Query parameters.
        headers : dict[str, str]
            Request headers.

        Returns
        -------
        httpx.Response
            Final response object.
        """
        client = await self._ensure_client()
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await client.get(path, params=params, headers=headers)
                if response.status_code >= 500 and attempt < self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_base_delay_seconds * (2**attempt)
                    )
                    continue
                return response
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_base_delay_seconds * (2**attempt)
                    )
                    continue
                break
        raise RancherApiError(f"Rancher API request failed: {last_exc}") from last_exc

    async def get(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Request Rancher API and return JSON object.

        Parameters
        ----------
        path : str
            API path.
        params : dict[str, str] | None
            Query parameters.

        Returns
        -------
        dict[str, Any]
            Parsed JSON response.
        """
        headers = dict(self._default_headers)
        auth_header = self._auth_header()
        if auth_header:
            headers["Authorization"] = auth_header

        response = await self._get_with_retries(path, params, headers)
        if (
            response.status_code == 401
            and self.auth.token
            and self.auth.ak
            and self.auth.sk
        ):
            retry_headers = dict(self._default_headers)
            retry_headers["Authorization"] = self._auth_header(force_basic=True) or ""
            response = await self._get_with_retries(path, params, retry_headers)

        if response.status_code >= 400:
            body = response.text[:200]
            raise RancherApiError(
                f"Rancher API error {response.status_code} for {path}: {body}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise RancherApiError(
                f"Invalid JSON in Rancher response for {path}"
            ) from exc
        if not isinstance(data, dict):
            raise RancherApiError(f"Unexpected response shape for {path}")
        return data

    async def try_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Request Rancher API and return None on error.

        Parameters
        ----------
        path : str
            API path.
        params : dict[str, str] | None
            Query parameters.

        Returns
        -------
        dict[str, Any] | None
            Parsed JSON or None when request fails.
        """
        try:
            return await self.get(path, params=params)
        except RancherApiError:
            return None

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """Load Rancher user by identifier."""
        return await self.get(f"/v3/users/{user_id}")
