#!/usr/bin/env python3
"""
Simple Rancher Namespace Analyzer
Analyzes Rancher project mapping from namespace annotations only
"""

import json
import traceback
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mks.domain.namespace_policy import is_system_namespace
from mks.domain.pod_reporting import count_pods_by_namespace, health_status_for_failed
from mks.domain.rancher_namespace import (
    RancherNamespaceProject,
    extract_rancher_namespace_project,
)
from mks.infrastructure.kubectl_client import kubectl_json_or_empty

EXTRA_SYSTEM_NAMESPACES = frozenset({"cattle-system", "rancher-operator-system"})


@dataclass
class NamespaceProject:
    """Namespace to project mapping"""

    namespace: str
    project: RancherNamespaceProject

    @property
    def project_id(self) -> str:
        """Return Rancher project identifier."""
        return self.project.project_id

    @property
    def project_name(self) -> str:
        """Return Rancher project display name."""
        return self.project.project_name

    @property
    def display_name(self) -> str:
        """Return namespace display name."""
        return self.project.display_name

    @property
    def description(self) -> str:
        """Return namespace description."""
        return self.project.description

    @property
    def created(self) -> str:
        """Return namespace creation timestamp."""
        return self.project.created


class SimpleRancherAnalyzer:
    """Analyze namespace-to-project mapping using Kubernetes annotations."""

    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        return kubectl_json_or_empty(command)

    def analyze_namespaces(self) -> dict[str, list[NamespaceProject]]:
        """Analyze namespaces and extract Rancher project information"""
        print("🔍 Analyzing namespace annotations for Rancher projects...")

        namespaces = self.run_kubectl("get namespaces")
        if not namespaces:
            return {}

        projects = defaultdict(list)
        for ns in namespaces.get("items", []):
            name = ns["metadata"]["name"]

            # Skip system namespaces
            if is_system_namespace(name, extra_namespaces=EXTRA_SYSTEM_NAMESPACES):
                continue

            project_meta = extract_rancher_namespace_project(ns)

            ns_project = NamespaceProject(
                namespace=name,
                project=project_meta,
            )

            # Group by project
            if project_meta.project_id:
                projects[project_meta.project_id].append(ns_project)
            else:
                # Namespaces without project - group by name
                projects[f"standalone-{name}"].append(ns_project)

        return dict(projects)

    def get_pod_counts_by_namespace(self) -> dict[str, dict[str, int]]:
        """Get pod counts per namespace"""
        print("📊 Getting pod counts per namespace...")

        pods = self.run_kubectl("get pods -A")
        if not pods:
            return {}
        return count_pods_by_namespace(pods)

    def try_get_rancher_users(self) -> dict[str, Any]:
        """Try to get Rancher users if CRDs are accessible"""
        print("🔍 Attempting to get Rancher users...")

        users_info = {}

        # Try different approaches
        commands_to_try = [
            "get users.management.cattle.io",
            "get projectroletemplatebindings.management.cattle.io -A",
            "get clusterroletemplatebindings.management.cattle.io",
        ]

        for cmd in commands_to_try:
            try:
                print(f"   Trying: kubectl {cmd}")
                data = self.run_kubectl(cmd)

                if data and "items" in data:
                    users_info[cmd.split()[1]] = data["items"]
                    print(f"   ✅ Success: Found {len(data['items'])} items")
                else:
                    print("   ❌ No data returned")

            except (RuntimeError, KeyError, TypeError, ValueError) as e:
                print(f"   ❌ Failed: {e}")
                continue

        return users_info

    def generate_reports(
        self,
        projects: dict[str, list[NamespaceProject]],
        pod_stats: dict[str, dict[str, int]],
        rancher_data: dict[str, Any],
    ) -> None:
        """Generate CSV reports"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project_summary, namespace_details = self._build_report_rows(
            projects, pod_stats
        )

        if project_summary:
            df_projects = pd.DataFrame(project_summary)
            df_projects = df_projects.sort_values("total_pods", ascending=False)
            project_file = self.data_dir / f"rancher_projects_simple_{timestamp}.csv"
            df_projects.to_csv(project_file, index=False)
            print(f"📊 Project summary saved: {project_file}")

        # Save namespace details
        if namespace_details:
            df_namespaces = pd.DataFrame(namespace_details)
            df_namespaces = df_namespaces.sort_values(
                ["project_name", "total_pods"], ascending=[True, False]
            )
            ns_file = self.data_dir / f"rancher_namespaces_simple_{timestamp}.csv"
            df_namespaces.to_csv(ns_file, index=False)
            print(f"📋 Namespace details saved: {ns_file}")

        # Save any Rancher data we managed to get
        if rancher_data:
            rancher_file = self.data_dir / f"rancher_additional_data_{timestamp}.json"
            with open(rancher_file, "w", encoding="utf-8") as f:
                json.dump(rancher_data, f, indent=2, default=str)
            print(f"🔧 Additional Rancher data saved: {rancher_file}")

    @staticmethod
    def _build_report_rows(
        projects: dict[str, list[NamespaceProject]],
        pod_stats: dict[str, dict[str, int]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Build project summary rows and namespace detail rows."""
        project_summary: list[dict[str, Any]] = []
        namespace_details: list[dict[str, Any]] = []
        for project_id, namespaces in projects.items():
            total_pods = sum(
                pod_stats.get(ns.namespace, {}).get("total", 0) for ns in namespaces
            )
            running_pods = sum(
                pod_stats.get(ns.namespace, {}).get("running", 0) for ns in namespaces
            )
            failed_pods = sum(
                pod_stats.get(ns.namespace, {}).get("failed", 0) for ns in namespaces
            )
            project_name = namespaces[0].project_name if namespaces else project_id
            project_summary.append(
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "namespace_count": len(namespaces),
                    "namespaces": ", ".join([ns.namespace for ns in namespaces]),
                    "total_pods": total_pods,
                    "running_pods": running_pods,
                    "failed_pods": failed_pods,
                    "health_status": health_status_for_failed(failed_pods),
                }
            )
            for ns in namespaces:
                stats = pod_stats.get(ns.namespace, {})
                namespace_details.append(
                    {
                        "namespace": ns.namespace,
                        "project_id": project_id,
                        "project_name": project_name,
                        "display_name": ns.display_name,
                        "description": ns.description,
                        "created": ns.created,
                        "total_pods": stats.get("total", 0),
                        "running_pods": stats.get("running", 0),
                        "failed_pods": stats.get("failed", 0),
                        "pending_pods": stats.get("pending", 0),
                    }
                )
        return project_summary, namespace_details

    def print_summary(
        self,
        projects: dict[str, list[NamespaceProject]],
        pod_stats: dict[str, dict[str, int]],
    ) -> None:
        """Print analysis summary"""
        print("\n" + "=" * 70)
        print("🚀 RANCHER PROJECT ANALYSIS (Simple Mode)")
        print("=" * 70)

        total_namespaces = sum(len(namespaces) for namespaces in projects.values())
        total_pods = sum(stats.get("total", 0) for stats in pod_stats.values())

        print("📊 Overview:")
        print(f"   • Projects: {len(projects)}")
        print(f"   • Namespaces: {total_namespaces}")
        print(f"   • Total Pods: {total_pods}")

        projects_by_size = self._build_project_size_rows(projects, pod_stats)

        print("\n📁 Projects (sorted by pod count):")
        self._print_project_breakdown(projects_by_size, pod_stats)

        if len(projects_by_size) > 15:
            remaining = len(projects_by_size) - 15
            remaining_pods = sum(x[2] for x in projects_by_size[15:])
            print(f"\n   ... and {remaining} more projects ({remaining_pods} pods)")

        print("\n💡 Next steps:")
        print("   • Check generated CSV files for detailed data")
        print("   • To get user information, you may need:")
        print("     - Rancher API access (token/password)")
        print("     - Higher Kubernetes permissions")
        print("     - Direct access to Rancher UI")

    @staticmethod
    def _build_project_size_rows(
        projects: dict[str, list[NamespaceProject]],
        pod_stats: dict[str, dict[str, int]],
    ) -> list[tuple[str, list[NamespaceProject], int]]:
        """Return project rows sorted by descending pod count."""
        rows: list[tuple[str, list[NamespaceProject], int]] = []
        for project_id, namespaces in projects.items():
            total_project_pods = sum(
                pod_stats.get(ns.namespace, {}).get("total", 0) for ns in namespaces
            )
            rows.append((project_id, namespaces, total_project_pods))
        rows.sort(key=lambda item: item[2], reverse=True)
        return rows

    @staticmethod
    def _print_project_breakdown(
        projects_by_size: list[tuple[str, list[NamespaceProject], int]],
        pod_stats: dict[str, dict[str, int]],
    ) -> None:
        """Print top projects and namespace-level pod breakdown."""
        for project_id, namespaces, pod_count in projects_by_size[:15]:
            project_name = namespaces[0].project_name if namespaces else project_id
            ns_names = [ns.namespace for ns in namespaces]

            print(f"\n   🔹 {project_name}")
            print(f"     ID: {project_id}")
            print(f"     Pods: {pod_count}")
            print(f"     Namespaces ({len(ns_names)}): {', '.join(ns_names)}")
            if len(namespaces) > 1:
                for ns in namespaces:
                    ns_pods = pod_stats.get(ns.namespace, {}).get("total", 0)
                    if ns_pods > 0:
                        print(f"       └─ {ns.namespace}: {ns_pods} pods")

    def run_analysis(self) -> None:
        """Run complete analysis"""
        print("🚀 Starting Simple Rancher Analysis")
        print("=" * 50)

        try:
            # Analyze namespaces
            projects = self.analyze_namespaces()

            if not projects:
                print(
                    "❌ No Rancher project information found in namespace annotations"
                )
                print("   This cluster might not be managed by Rancher")
                return

            # Get pod statistics
            pod_stats = self.get_pod_counts_by_namespace()

            # Try to get additional Rancher data
            rancher_data = self.try_get_rancher_users()

            # Generate reports
            self.generate_reports(projects, pod_stats, rancher_data)

            # Print summary
            self.print_summary(projects, pod_stats)

        except (RuntimeError, ValueError, KeyError) as e:
            print(f"❌ Analysis failed: {e}")
            traceback.print_exc()


def execute_rancher_project_overview(data_dir: str = "reports") -> None:
    """Execute Rancher project overview use-case."""
    analyzer = SimpleRancherAnalyzer(data_dir=data_dir)
    analyzer.run_analysis()


def execute() -> None:
    """Backward-compatible alias for execute_rancher_project_overview."""
    execute_rancher_project_overview()


if __name__ == "__main__":
    execute_rancher_project_overview()
