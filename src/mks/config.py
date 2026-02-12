"""Application configuration and environment loading."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AuditConfig:
    """Top-level config controlling enabled backends."""

    kubeconfig: Path | None = None
    ovh_endpoint: str | None = None
    ovh_app_key: str | None = None
    ovh_app_secret: str | None = None
    ovh_consumer_key: str | None = None
    ovh_project_id: str | None = None
    ovh_kube_id: str | None = None
    rancher_url: str | None = None
    rancher_token: str | None = None
    rancher_ak: str | None = None
    rancher_sk: str | None = None
    ldp_websocket_url: str | None = None
    ldp_token: str | None = None

    @property
    def has_ovh(self) -> bool:
        return bool(
            self.ovh_app_key
            and self.ovh_app_secret
            and self.ovh_consumer_key
            and self.ovh_project_id
        )

    @property
    def has_rancher(self) -> bool:
        return bool(self.rancher_url and (self.rancher_token or self.rancher_ak))

    @property
    def has_audit_logs(self) -> bool:
        return bool(self.ldp_websocket_url)


def load_config(env_path: Path = Path(".env")) -> AuditConfig:
    """Load config from environment and optional .env file."""
    load_dotenv(env_path, override=False)
    kubeconfig_raw = os.getenv("KUBECONFIG")
    return AuditConfig(
        kubeconfig=Path(kubeconfig_raw) if kubeconfig_raw else None,
        ovh_endpoint=os.getenv("OVH_ENDPOINT"),
        ovh_app_key=os.getenv("OVH_APPLICATION_KEY"),
        ovh_app_secret=os.getenv("OVH_APPLICATION_SECRET"),
        ovh_consumer_key=os.getenv("OVH_CONSUMER_KEY"),
        ovh_project_id=os.getenv("OVH_PROJECT_ID"),
        ovh_kube_id=os.getenv("OVH_KUBE_ID"),
        rancher_url=os.getenv("RANCHER_URL"),
        rancher_token=os.getenv("RANCHER_TOKEN"),
        rancher_ak=os.getenv("RANCHER_AK"),
        rancher_sk=os.getenv("RANCHER_SK"),
        ldp_websocket_url=os.getenv("LDP_WEBSOCKET_URL"),
        ldp_token=os.getenv("LDP_TOKEN"),
    )
