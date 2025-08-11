#!/usr/bin/env python3
"""Quick pod density check"""

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


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
        result = subprocess.run(
            f"kubectl {cmd}", shell=True, capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running kubectl: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing kubectl output: {e}")
        sys.exit(1)


def main() -> None:
    """Analyze pod distribution across nodes and display statistics"""
    pods = run_kubectl("get pods -A -o json")

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


if __name__ == "__main__":
    main()
