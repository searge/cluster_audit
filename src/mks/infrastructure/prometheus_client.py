"""Prometheus HTTP client for capacity planning.

Reads rancher-monitoring (or any Prometheus) over HTTP. Synchronous: capacity
planning issues a handful of instant queries, so no async machinery is needed.
"""

from typing import Any

import httpx


class PrometheusError(RuntimeError):
    """Prometheus query failed or returned an error status."""


class PrometheusClient:
    """Minimal read-only Prometheus client (instant queries)."""

    def __init__(
        self,
        base_url: str,
        *,
        verify_tls: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Create a client for ``base_url`` (e.g. ``http://localhost:9090``)."""
        self._base_url = base_url.rstrip("/")
        self._verify_tls = verify_tls
        self._timeout = timeout_seconds

    def instant(self, query: str) -> list[tuple[dict[str, str], float]]:
        """Run an instant query, returning ``(labels, value)`` per series."""
        try:
            with httpx.Client(verify=self._verify_tls, timeout=self._timeout) as client:
                response = client.get(
                    f"{self._base_url}/api/v1/query", params={"query": query}
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PrometheusError(f"query failed: {type(exc).__name__}: {exc}") from exc
        if payload.get("status") != "success":
            raise PrometheusError(f"query error: {payload.get('error', 'unknown')}")
        results: list[tuple[dict[str, str], float]] = []
        for item in payload.get("data", {}).get("result", []):
            value = item.get("value")
            if value and len(value) == 2:
                results.append((dict(item.get("metric", {})), float(value[1])))
        return results

    def scalar(self, query: str) -> float | None:
        """Run a query expected to yield a single value; ``None`` if empty."""
        results = self.instant(query)
        return results[0][1] if results else None


__all__ = ["PrometheusClient", "PrometheusError"]
