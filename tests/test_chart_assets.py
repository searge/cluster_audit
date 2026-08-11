"""The chart's copies of shared assets must not drift from their sources.

Helm can only read files inside the chart directory, so the dashboard exists
twice: grafana/dashboards/ feeds the local docker-compose Grafana, and
k8s/platform-capacity/dashboards/ feeds the deployed ConfigMap. The pair
already drifted once — the deployed copy evolved in ovh-cluster while the
"source of truth" here sat stale — which is exactly the failure this test
turns into a red build instead of a surprise on the next deploy.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_chart_dashboard_matches_grafana_source() -> None:
    """One dashboard, two consumers, byte-identical."""
    source = REPO / "grafana" / "dashboards" / "capacity.json"
    chart_copy = REPO / "k8s" / "platform-capacity" / "dashboards" / "capacity.json"

    assert chart_copy.read_bytes() == source.read_bytes(), (
        "k8s/platform-capacity/dashboards/capacity.json differs from "
        "grafana/dashboards/capacity.json - copy the edited one over the other"
    )
