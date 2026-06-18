"""OVH API client (billing + managed Kubernetes).

Thin wrapper over the optional ``ovh`` library. All OVH HTTP IO lives here so
the application layer stays free of side effects. Install with the ``ovh`` extra.

The OVH billing API has no batch endpoint: every line is a separate ``GET``.
Two things keep this fast:

* **disk cache** — issued bills and their detail lines are immutable, so once
  fetched they are served from ``diskcache`` forever (re-runs are near-instant);
* **thread pool** — the ``ovh`` library is synchronous, so per-line GETs are
  fanned out across worker threads instead of run sequentially.

The managed-Kubernetes (MKS) endpoints expose control-plane truth that kubectl
cannot see: cluster version/region, node-pool billing mode and autoscaling.
"""

import importlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import TracebackType
from typing import Any, NamedTuple, Self

from mks.config import OvhConfig

DEFAULT_MAX_WORKERS = 8

# Known MKS project id; overridden by OvhConfig.project_id.
DEFAULT_MKS_PROJECT_ID = "2a7f293154554621aed100e791b0a98c"


class OvhApiError(RuntimeError):
    """OVH API request failed or credentials are incomplete."""


@dataclass(frozen=True)
class OvhIdentity:
    """Account identity returned by ``GET /me``."""

    nichandle: str
    currency: str


@dataclass(frozen=True)
class BillLine:
    """One billing detail line, normalized for the application layer."""

    bill_id: str
    issue_date: str
    domain: str
    description: str
    value: float


class KubeCluster(NamedTuple):
    """MKS cluster summary (``GET /cloud/project/{id}/kube/{kubeId}``)."""

    id: str
    name: str
    region: str
    version: str
    status: str
    plan: str
    is_up_to_date: bool
    control_plane_is_up_to_date: bool
    next_upgrade_versions: tuple[str, ...]
    created_at: str
    updated_at: str


class NodePool(NamedTuple):
    """MKS node pool (``GET /cloud/project/{id}/kube/{kubeId}/nodepool``)."""

    id: str
    name: str
    flavor: str
    status: str
    autoscale: bool
    monthly_billed: bool
    desired_nodes: int
    min_nodes: int
    max_nodes: int
    current_nodes: int
    available_nodes: int
    up_to_date_nodes: int


class KubeNode(NamedTuple):
    """One MKS node (``GET /cloud/project/{id}/kube/{kubeId}/node``)."""

    id: str
    name: str
    flavor: str
    status: str
    node_pool_id: str
    version: str
    is_up_to_date: bool


class UsageSnapshot(NamedTuple):
    """Project spend for a usage period (``GET .../usage/current|forecast``)."""

    period_from: str
    period_to: str
    total_price: float


class VolumeInfo(NamedTuple):
    """One block-storage volume (``GET /cloud/project/{id}/volume``)."""

    id: str
    name: str
    region: str
    size_gb: int
    status: str
    attached: bool
    creation_date: str


class FloatingIp(NamedTuple):
    """One floating IP (``GET .../region/{region}/floatingip``)."""

    id: str
    ip: str
    region: str
    status: str
    associated: bool


class OvhClient:
    """Read-only OVH client (billing + MKS) with disk cache and threaded fan-out."""

    def __init__(
        self,
        config: OvhConfig,
        *,
        cache_dir: str | None = None,
        cache_ttl_seconds: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """Create a client from OVH credentials.

        Parameters
        ----------
        config:
            OVH credentials slice.
        cache_dir:
            Directory for the immutable-response cache. ``None`` disables caching.
        cache_ttl_seconds:
            Optional expiry for cached entries; ``None`` means never expire.
        max_workers:
            Thread-pool size for fanning out per-line detail GETs.

        Raises
        ------
        OvhApiError
            If credentials are incomplete or the ``ovh`` library is missing.
        """
        missing = [
            name
            for name, value in (
                ("OVH_ENDPOINT", config.endpoint),
                ("OVH_APPLICATION_KEY", config.app_key),
                ("OVH_APPLICATION_SECRET", config.app_secret),
                ("OVH_CONSUMER_KEY", config.consumer_key),
            )
            if not value
        ]
        if missing:
            raise OvhApiError(f"missing OVH credentials: {', '.join(missing)}")

        try:
            ovh = importlib.import_module("ovh")
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise OvhApiError(
                "the 'ovh' library is required; install with the 'ovh' extra"
            ) from exc

        self._client: Any = ovh.Client(
            endpoint=config.endpoint,
            application_key=config.app_key,
            application_secret=config.app_secret,
            consumer_key=config.consumer_key,
        )
        cache_cls = importlib.import_module("diskcache").Cache
        self._cache: Any = cache_cls(cache_dir) if cache_dir else None
        self._cache_ttl = cache_ttl_seconds
        self._max_workers = max(1, max_workers)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the disk cache handle."""
        if self._cache is not None:
            self._cache.close()

    def _get(self, path: str, **params: Any) -> Any:
        try:
            return self._client.get(path, **params)
        except Exception as exc:  # noqa: BLE001 - surface any API error uniformly
            raise OvhApiError(
                f"GET {path} failed: {type(exc).__name__}: {exc}"
            ) from exc

    def _get_immutable(self, path: str) -> Any:
        """``GET`` a path whose response never changes, via the disk cache."""
        if self._cache is not None:
            cached = self._cache.get(path)
            if cached is not None:
                return cached
        value = self._get(path)
        if self._cache is not None:
            self._cache.set(path, value, expire=self._cache_ttl)
        return value

    def get_identity(self) -> OvhIdentity:
        """Return account handle and currency code (``GET /me``)."""
        me = self._get("/me")
        currency = (me.get("currency") or {}).get("code", "?")
        return OvhIdentity(nichandle=me.get("nichandle", "?"), currency=currency)

    def list_projects(self) -> list[str]:
        """Return cloud project ids on the account (``GET /cloud/project``)."""
        return list(self._get("/cloud/project"))

    def get_project_description(self, project_id: str) -> str:
        """Return a project's description (``GET /cloud/project/{id}``)."""
        return str(self._get(f"/cloud/project/{project_id}").get("description", "?"))

    def list_bills(self, date_from: str, date_to: str) -> list[str]:
        """Return bill ids issued within the window (``GET /me/bill``)."""
        return list(
            self._get("/me/bill", **{"date.from": date_from, "date.to": date_to})
        )

    def _fetch_bill_meta(self, bill_id: str) -> tuple[str, str, list[str]]:
        """Return ``(bill_id, issue_date, detail_ids)`` for one bill."""
        issue_date = str(self._get_immutable(f"/me/bill/{bill_id}")["date"])
        detail_ids = list(self._get_immutable(f"/me/bill/{bill_id}/details"))
        return bill_id, issue_date, detail_ids

    def _fetch_line(self, bill_id: str, issue_date: str, detail_id: str) -> BillLine:
        """Fetch and normalize one billing detail line."""
        detail = self._get_immutable(f"/me/bill/{bill_id}/details/{detail_id}")
        return BillLine(
            bill_id=bill_id,
            issue_date=issue_date,
            domain=str(detail.get("domain", "")),
            description=str(detail["description"]),
            value=float(detail["totalPrice"]["value"]),
        )

    def fetch_bill_lines(self, bill_ids: list[str]) -> list[BillLine]:
        """Fetch every detail line across ``bill_ids`` concurrently."""
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            metas = list(pool.map(self._fetch_bill_meta, bill_ids))
            jobs = [
                (bill_id, issue_date, detail_id)
                for bill_id, issue_date, detail_ids in metas
                for detail_id in detail_ids
            ]
            return list(pool.map(lambda job: self._fetch_line(*job), jobs))

    def list_kube_ids(self, project_id: str) -> list[str]:
        """Return managed-Kubernetes cluster ids for a project."""
        return list(self._get(f"/cloud/project/{project_id}/kube"))

    def get_kube(self, project_id: str, kube_id: str) -> KubeCluster:
        """Return the cluster summary for one MKS cluster."""
        raw = self._get(f"/cloud/project/{project_id}/kube/{kube_id}")
        return KubeCluster(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            region=str(raw.get("region", "")),
            version=str(raw.get("version", "")),
            status=str(raw.get("status", "")),
            plan=str(raw.get("plan", "")),
            is_up_to_date=bool(raw.get("isUpToDate", False)),
            control_plane_is_up_to_date=bool(raw.get("controlPlaneIsUpToDate", False)),
            next_upgrade_versions=tuple(raw.get("nextUpgradeVersions") or ()),
            created_at=str(raw.get("createdAt", "")),
            updated_at=str(raw.get("updatedAt", "")),
        )

    def list_nodepools(self, project_id: str, kube_id: str) -> list[NodePool]:
        """Return node pools for one MKS cluster."""
        raw = self._get(f"/cloud/project/{project_id}/kube/{kube_id}/nodepool")
        return [
            NodePool(
                id=str(item["id"]),
                name=str(item.get("name", "")),
                flavor=str(item.get("flavor", "")),
                status=str(item.get("status", "")),
                autoscale=bool(item.get("autoscale", False)),
                monthly_billed=bool(item.get("monthlyBilled", False)),
                desired_nodes=int(item.get("desiredNodes", 0)),
                min_nodes=int(item.get("minNodes", 0)),
                max_nodes=int(item.get("maxNodes", 0)),
                current_nodes=int(item.get("currentNodes", 0)),
                available_nodes=int(item.get("availableNodes", 0)),
                up_to_date_nodes=int(item.get("upToDateNodes", 0)),
            )
            for item in raw
        ]

    def list_nodes(self, project_id: str, kube_id: str) -> list[KubeNode]:
        """Return individual nodes for one MKS cluster."""
        raw = self._get(f"/cloud/project/{project_id}/kube/{kube_id}/node")
        return [
            KubeNode(
                id=str(item["id"]),
                name=str(item.get("name", "")),
                flavor=str(item.get("flavor", "")),
                status=str(item.get("status", "")),
                node_pool_id=str(item.get("nodePoolId", "")),
                version=str(item.get("version", "")),
                is_up_to_date=bool(item.get("isUpToDate", False)),
            )
            for item in raw
        ]

    def _usage(self, project_id: str, period: str) -> UsageSnapshot:
        raw = self._get(f"/cloud/project/{project_id}/usage/{period}")
        window = raw.get("period") or {}
        total = raw.get("totalPrice") or {}
        return UsageSnapshot(
            period_from=str(window.get("from", "")),
            period_to=str(window.get("to", "")),
            total_price=float(total.get("value", 0.0)),
        )

    def get_usage_current(self, project_id: str) -> UsageSnapshot:
        """Return month-to-date project spend."""
        return self._usage(project_id, "current")

    def get_usage_forecast(self, project_id: str) -> UsageSnapshot:
        """Return projected end-of-month project spend."""
        return self._usage(project_id, "forecast")

    def list_regions(self, project_id: str) -> list[str]:
        """Return active region names for a project."""
        return list(self._get(f"/cloud/project/{project_id}/region"))

    def list_volumes(self, project_id: str) -> list[VolumeInfo]:
        """Return all block-storage volumes for a project."""
        raw = self._get(f"/cloud/project/{project_id}/volume")
        return [
            VolumeInfo(
                id=str(item["id"]),
                name=str(item.get("name", "")),
                region=str(item.get("region", "")),
                size_gb=int(item.get("size", 0)),
                status=str(item.get("status", "")),
                attached=bool(item.get("attachedTo")),
                creation_date=str(item.get("creationDate", "")),
            )
            for item in raw
        ]

    def list_floating_ips(self, project_id: str, region: str) -> list[FloatingIp]:
        """Return floating IPs in a region."""
        raw = self._get(f"/cloud/project/{project_id}/region/{region}/floatingip")
        return [
            FloatingIp(
                id=str(item["id"]),
                ip=str(item.get("ip", "")),
                region=region,
                status=str(item.get("status", "")),
                associated=bool(item.get("associatedEntity")),
            )
            for item in raw
        ]


__all__ = [
    "DEFAULT_MKS_PROJECT_ID",
    "BillLine",
    "FloatingIp",
    "KubeCluster",
    "KubeNode",
    "NodePool",
    "OvhApiError",
    "OvhClient",
    "OvhIdentity",
    "UsageSnapshot",
    "VolumeInfo",
]
