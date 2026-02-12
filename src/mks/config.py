"""Application configuration and environment loading."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class OvhConfig:
    """OVH backend credentials."""

    endpoint: str | None = None
    app_key: str | None = None
    app_secret: str | None = None
    consumer_key: str | None = None
    project_id: str | None = None
    kube_id: str | None = None


@dataclass(frozen=True)
class RancherConfig:
    """Rancher backend credentials."""

    url: str | None = None
    token: str | None = None
    ak: str | None = None
    sk: str | None = None


@dataclass(frozen=True)
class AuditLogConfig:
    """Audit-log backend credentials."""

    websocket_url: str | None = None
    token: str | None = None


@dataclass(frozen=True)
class AuditConfig:
    """Top-level config controlling enabled backends."""

    kubeconfig: Path | None = None
    ovh: OvhConfig = field(default_factory=OvhConfig)
    rancher: RancherConfig = field(default_factory=RancherConfig)
    audit_logs: AuditLogConfig = field(default_factory=AuditLogConfig)

    @property
    def ovh_endpoint(self) -> str | None:
        """Return OVH API endpoint URL."""
        return self.ovh.endpoint

    @property
    def ovh_app_key(self) -> str | None:
        """Return OVH application key."""
        return self.ovh.app_key

    @property
    def ovh_app_secret(self) -> str | None:
        """Return OVH application secret."""
        return self.ovh.app_secret

    @property
    def ovh_consumer_key(self) -> str | None:
        """Return OVH consumer key."""
        return self.ovh.consumer_key

    @property
    def ovh_project_id(self) -> str | None:
        """Return OVH project identifier."""
        return self.ovh.project_id

    @property
    def ovh_kube_id(self) -> str | None:
        """Return OVH managed Kubernetes identifier."""
        return self.ovh.kube_id

    @property
    def rancher_url(self) -> str | None:
        """Return Rancher base URL."""
        return self.rancher.url

    @property
    def rancher_token(self) -> str | None:
        """Return Rancher bearer token."""
        return self.rancher.token

    @property
    def rancher_ak(self) -> str | None:
        """Return Rancher API access key."""
        return self.rancher.ak

    @property
    def rancher_sk(self) -> str | None:
        """Return Rancher API secret key."""
        return self.rancher.sk

    @property
    def ldp_websocket_url(self) -> str | None:
        """Return audit-log websocket endpoint."""
        return self.audit_logs.websocket_url

    @property
    def ldp_token(self) -> str | None:
        """Return audit-log backend token."""
        return self.audit_logs.token

    @property
    def has_ovh(self) -> bool:
        """Return whether OVH credentials are sufficiently configured."""
        return bool(
            self.ovh_app_key
            and self.ovh_app_secret
            and self.ovh_consumer_key
            and self.ovh_project_id
        )

    @property
    def has_rancher(self) -> bool:
        """Return whether Rancher connectivity is configured."""
        return bool(self.rancher_url and (self.rancher_token or self.rancher_ak))

    @property
    def has_audit_logs(self) -> bool:
        """Return whether audit logs backend is configured."""
        return bool(self.ldp_websocket_url)


def load_config(env_path: Path = Path(".env")) -> AuditConfig:
    """Load config from environment and optional .env file."""
    load_dotenv(env_path, override=False)
    kubeconfig_raw = os.getenv("KUBECONFIG")
    return AuditConfig(
        kubeconfig=Path(kubeconfig_raw) if kubeconfig_raw else None,
        ovh=OvhConfig(
            endpoint=os.getenv("OVH_ENDPOINT"),
            app_key=os.getenv("OVH_APPLICATION_KEY"),
            app_secret=os.getenv("OVH_APPLICATION_SECRET"),
            consumer_key=os.getenv("OVH_CONSUMER_KEY"),
            project_id=os.getenv("OVH_PROJECT_ID"),
            kube_id=os.getenv("OVH_KUBE_ID"),
        ),
        rancher=RancherConfig(
            url=os.getenv("RANCHER_URL"),
            token=os.getenv("RANCHER_TOKEN"),
            ak=os.getenv("RANCHER_AK"),
            sk=os.getenv("RANCHER_SK"),
        ),
        audit_logs=AuditLogConfig(
            websocket_url=os.getenv("LDP_WEBSOCKET_URL"),
            token=os.getenv("LDP_TOKEN"),
        ),
    )
