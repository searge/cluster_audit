#!/usr/bin/env python3
"""
Simple Rancher Namespace Analyzer
Analyzes Rancher project mapping from namespace annotations only
"""

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class NamespaceProject:
    """Namespace to project mapping"""

    namespace: str
    project_id: str
    project_name: str
    display_name: str
    description: str
    created: str


class SimpleRancherAnalyzer:
    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            cmd = f"kubectl {command} -o json"
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True
            )
            return json.loads(result.stdout)  # type: ignore[no-any-return]
        except subprocess.CalledProcessError as e:
            print(f"❌ Error running kubectl: {e}")
            return {}
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON: {e}")
            return {}

    def analyze_namespaces(self) -> dict[str, list[NamespaceProject]]:
        """Analyze namespaces and extract Rancher project information"""
        print("🔍 Analyzing namespace annotations for Rancher projects...")

        namespaces = self.run_kubectl("get namespaces")
        if not namespaces:
            return {}

        projects = defaultdict(list)
        system_namespaces = {
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "default",
            "ingress-controller",
            "cattle-system",
            "rancher-operator-system",
        }

        for ns in namespaces.get("items", []):
            name = ns["metadata"]["name"]

            # Skip system namespaces
            if (
                name in system_namespaces
                or name.startswith("cattle-")
                or name.startswith("rancher-")
                or name.startswith("kube-")
            ):
                continue

            annotations = ns["metadata"].get("annotations", {})
            labels = ns["metadata"].get("labels", {})

            # Extract Rancher project information
            project_id = (
                annotations.get("field.cattle.io/projectId")
                or labels.get("field.cattle.io/projectId")
                or ""
            )

            project_name = annotations.get("field.cattle.io/projectName", "")
            display_name = annotations.get("field.cattle.io/displayName", name)
            description = annotations.get("field.cattle.io/description", "")
            created = ns["metadata"].get("creationTimestamp", "")

            ns_project = NamespaceProject(
                namespace=name,
                project_id=project_id,
                project_name=project_name or project_id,
                display_name=display_name,
                description=description,
                created=created,
            )

            # Group by project
            if project_id:
                projects[project_id].append(ns_project)
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

        ns_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"running": 0, "total": 0, "failed": 0, "pending": 0}
        )

        for pod in pods.get("items", []):
            ns = pod["metadata"]["namespace"]
            phase = pod["status"]["phase"]

            ns_stats[ns]["total"] += 1
            if phase == "Running":
                ns_stats[ns]["running"] += 1
            elif phase == "Failed":
                ns_stats[ns]["failed"] += 1
            elif phase == "Pending":
                ns_stats[ns]["pending"] += 1

        return dict(ns_stats)

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

            except Exception as e:
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

        # Project summary
        project_summary = []
        namespace_details = []

        for project_id, namespaces in projects.items():
            # Calculate project totals
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
                    "health_status": "🟢 Healthy"
                    if failed_pods == 0
                    else f"🟡 {failed_pods} Failed"
                    if failed_pods < 5
                    else f"🔴 {failed_pods} Failed",
                }
            )

            # Namespace details
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

        # Save project summary
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
            with open(rancher_file, "w") as f:
                json.dump(rancher_data, f, indent=2, default=str)
            print(f"🔧 Additional Rancher data saved: {rancher_file}")

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

        # Sort projects by pod count
        projects_by_size = []
        for project_id, namespaces in projects.items():
            total_project_pods = sum(
                pod_stats.get(ns.namespace, {}).get("total", 0) for ns in namespaces
            )
            projects_by_size.append((project_id, namespaces, total_project_pods))

        projects_by_size.sort(key=lambda x: x[2], reverse=True)

        print("\n📁 Projects (sorted by pod count):")
        for project_id, namespaces, pod_count in projects_by_size[:15]:  # Top 15
            project_name = namespaces[0].project_name if namespaces else project_id
            ns_names = [ns.namespace for ns in namespaces]

            print(f"\n   🔹 {project_name}")
            print(f"     ID: {project_id}")
            print(f"     Pods: {pod_count}")
            print(f"     Namespaces ({len(ns_names)}): {', '.join(ns_names)}")

            # Show namespace breakdown if multiple
            if len(namespaces) > 1:
                for ns in namespaces:
                    ns_pods = pod_stats.get(ns.namespace, {}).get("total", 0)
                    if ns_pods > 0:
                        print(f"       └─ {ns.namespace}: {ns_pods} pods")

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

        except Exception as e:
            print(f"❌ Analysis failed: {e}")
            import traceback

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
