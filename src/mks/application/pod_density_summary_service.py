#!/usr/bin/env python3
"""Quick pod density check"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from mks.infrastructure.kubectl_client import KubectlError, kubectl_json


@dataclass
class NodeStats:
    """Statistics for pods on a single node"""

    running: int = 0
    failed: int = 0
    pending: int = 0
    succeeded: int = 0

    @property
    def total(self) -> int:
        """Total number of pods on this node"""
        return self.running + self.failed + self.pending + self.succeeded


def run_kubectl(cmd: str) -> dict[str, Any]:
    """Run kubectl command and return parsed JSON output"""
    try:
        return kubectl_json(cmd)
    except KubectlError as e:
        raise RuntimeError(f"Error running kubectl: {e}") from e


def execute_pod_density_summary() -> None:
    """Analyze pod distribution across nodes and display statistics"""
    pods = run_kubectl("get pods -A")

    node_stats: dict[str, NodeStats] = defaultdict(NodeStats)

    for pod in pods["items"]:
        node = pod["spec"].get("nodeName", "Unscheduled")
        if node == "Unscheduled":
            continue

        phase = pod["status"]["phase"].lower()
        if hasattr(node_stats[node], phase):
            setattr(node_stats[node], phase, getattr(node_stats[node], phase) + 1)

    print(f"{'NODE_NAME':<45} {'RUNNING':<8} {'TOTAL':<8} {'FAILED':<8} {'PENDING':<8}")
    print("-" * 80)

    for node, stats in sorted(node_stats.items()):
        print(
            f"{node:<45} {stats.running:<8} {stats.total:<8} "
            f"{stats.failed:<8} {stats.pending:<8}"
        )


def execute() -> None:
    """Backward-compatible alias for execute_pod_density_summary."""
    execute_pod_density_summary()


if __name__ == "__main__":
    execute_pod_density_summary()
