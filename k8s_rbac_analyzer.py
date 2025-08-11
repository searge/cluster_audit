#!/usr/bin/env python3
"""
Kubernetes RBAC and Project Analyzer
Analyzes users, service accounts, and access patterns in plain Kubernetes
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class UserAccess:
    """User access information"""

    username: str
    subject_type: str  # User, Group, ServiceAccount
    namespaces: list[str]
    cluster_roles: list[str]
    namespace_roles: dict[str, list[str]]
    service_account_namespace: str | None = None


@dataclass
class ProjectGroup:
    """Logical project grouping based on naming patterns"""

    project_name: str
    namespaces: list[str]
    pattern: str
    users: list[str]
    service_accounts: list[str]


class K8sRBACAnalyzer:
    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # Common system namespaces to exclude
        self.system_namespaces = {
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "default",
            "gke-gmp-system",
            "gke-managed-cim",
            "gke-managed-filestorecsi",
            "gke-managed-parallelstorecsi",
            "gke-managed-system",
            "gke-managed-volumepopulator",
            "gmp-public",
        }

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
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON: {e}")
            sys.exit(1)

    def get_all_namespaces(self) -> dict[str, dict[str, Any]]:
        """Get all namespaces with metadata"""
        print("📋 Getting all namespaces...")

        namespaces = self.run_kubectl("get namespaces")
        if not namespaces:
            return {}

        ns_info = {}
        for ns in namespaces.get("items", []):
            name = ns["metadata"]["name"]

            # Skip system namespaces
            if name in self.system_namespaces or name.startswith(
                ("cattle-", "rancher-")
            ):
                continue

            ns_info[name] = {
                "name": name,
                "labels": ns["metadata"].get("labels", {}),
                "annotations": ns["metadata"].get("annotations", {}),
                "created": ns["metadata"].get("creationTimestamp", ""),
            }

        return ns_info

    def detect_project_patterns(
        self, namespaces: dict[str, dict[str, Any]]
    ) -> dict[str, ProjectGroup]:
        """Detect logical project groupings based on naming patterns"""
        print("🔍 Detecting project patterns from namespace names...")

        projects = {}
        assigned_namespaces = set()

        # Common patterns to detect
        patterns = [
            # Environment-based patterns
            (
                r"^(.+)-(dev|development|staging|stage|prod|production|test|testing)$",
                "env",
            ),
            # Service-based patterns
            (r"^(.+)-(frontend|backend|api|service|app|web|ui)$", "service"),
            # Component-based patterns
            (r"^(.+)-(db|database|redis|cache|queue|worker)$", "component"),
            # Simple prefix patterns
            (r"^([a-zA-Z0-9]+)-(.+)$", "prefix"),
        ]

        for pattern_regex, pattern_type in patterns:
            pattern = re.compile(pattern_regex)

            for ns_name in namespaces:
                if ns_name in assigned_namespaces:
                    continue

                match = pattern.match(ns_name)
                if match:
                    project_name = match.group(1)

                    # Find all namespaces matching this project
                    project_namespaces = []
                    for other_ns in namespaces:
                        if (
                            other_ns.startswith(f"{project_name}-")
                            or other_ns == project_name
                        ):
                            project_namespaces.append(other_ns)
                            assigned_namespaces.add(other_ns)

                    if len(project_namespaces) > 0:
                        projects[project_name] = ProjectGroup(
                            project_name=project_name,
                            namespaces=project_namespaces,
                            pattern=f"{pattern_type}: {pattern_regex}",
                            users=[],
                            service_accounts=[],
                        )

        # Add remaining namespaces as individual projects
        for ns_name in namespaces:
            if ns_name not in assigned_namespaces:
                projects[ns_name] = ProjectGroup(
                    project_name=ns_name,
                    namespaces=[ns_name],
                    pattern="standalone",
                    users=[],
                    service_accounts=[],
                )

        return projects

    def analyze_rbac_bindings(self) -> dict[str, UserAccess]:
        """Analyze RoleBindings and ClusterRoleBindings"""
        print("👥 Analyzing RBAC bindings...")

        users = {}

        # Get ClusterRoleBindings
        cluster_bindings = self.run_kubectl("get clusterrolebindings")
        for binding in cluster_bindings.get("items", []):
            binding_name = binding["metadata"]["name"]
            role_ref = binding.get("roleRef", {})
            role_name = role_ref.get("name", "")

            # Skip system bindings
            if any(
                skip in binding_name.lower()
                for skip in ["system:", "gke-", "kubernetes-"]
            ):
                continue

            for subject in binding.get("subjects", []):
                subject_type = subject.get("kind", "")
                subject_name = subject.get("name", "")
                subject_namespace = subject.get("namespace")

                if subject_type in ["User", "Group", "ServiceAccount"] and subject_name:
                    user_key = f"{subject_type}:{subject_name}"

                    if user_key not in users:
                        users[user_key] = UserAccess(
                            username=subject_name,
                            subject_type=subject_type,
                            namespaces=[],
                            cluster_roles=[],
                            namespace_roles={},
                            service_account_namespace=subject_namespace,
                        )

                    users[user_key].cluster_roles.append(
                        f"{role_name} ({binding_name})"
                    )

        # Get RoleBindings
        role_bindings = self.run_kubectl("get rolebindings -A")
        for binding in role_bindings.get("items", []):
            binding_name = binding["metadata"]["name"]
            binding_namespace = binding["metadata"]["namespace"]
            role_ref = binding.get("roleRef", {})
            role_name = role_ref.get("name", "")

            # Skip system namespaces
            if binding_namespace in self.system_namespaces:
                continue

            for subject in binding.get("subjects", []):
                subject_type = subject.get("kind", "")
                subject_name = subject.get("name", "")
                subject_namespace = subject.get("namespace")

                if subject_type in ["User", "Group", "ServiceAccount"] and subject_name:
                    user_key = f"{subject_type}:{subject_name}"

                    if user_key not in users:
                        users[user_key] = UserAccess(
                            username=subject_name,
                            subject_type=subject_type,
                            namespaces=[],
                            cluster_roles=[],
                            namespace_roles={},
                            service_account_namespace=subject_namespace,
                        )

                    if binding_namespace not in users[user_key].namespaces:
                        users[user_key].namespaces.append(binding_namespace)

                    if binding_namespace not in users[user_key].namespace_roles:
                        users[user_key].namespace_roles[binding_namespace] = []

                    users[user_key].namespace_roles[binding_namespace].append(
                        f"{role_name} ({binding_name})"
                    )

        return users

    def get_pod_statistics(self) -> dict[str, dict[str, int]]:
        """Get pod statistics per namespace"""
        print("📊 Getting pod statistics...")

        pods = self.run_kubectl("get pods -A")
        if not pods:
            return {}

        ns_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"running": 0, "total": 0, "failed": 0, "pending": 0}
        )

        for pod in pods.get("items", []):
            namespace = pod["metadata"]["namespace"]
            phase = pod["status"]["phase"]

            if namespace in self.system_namespaces:
                continue

            ns_stats[namespace]["total"] += 1
            if phase == "Running":
                ns_stats[namespace]["running"] += 1
            elif phase == "Failed":
                ns_stats[namespace]["failed"] += 1
            elif phase == "Pending":
                ns_stats[namespace]["pending"] += 1

        return dict(ns_stats)

    def map_users_to_projects(
        self, projects: dict[str, ProjectGroup], users: dict[str, UserAccess]
    ) -> dict[str, ProjectGroup]:
        """Map users to detected projects"""
        print("🔗 Mapping users to projects...")

        for _user_key, user_access in users.items():
            # Skip obvious service accounts and system users
            if user_access.subject_type == "ServiceAccount" and any(
                sys in user_access.username for sys in ["system", "default", "gke-"]
            ):
                continue

            for _project_name, project in projects.items():
                # Check if user has access to any namespace in this project
                user_project_namespaces = set(user_access.namespaces) & set(
                    project.namespaces
                )

                if user_project_namespaces:
                    if user_access.subject_type == "ServiceAccount":
                        if user_access.username not in project.service_accounts:
                            project.service_accounts.append(user_access.username)
                    else:
                        if user_access.username not in project.users:
                            project.users.append(user_access.username)

        return projects

    def generate_reports(
        self,
        projects: dict[str, ProjectGroup],
        users: dict[str, UserAccess],
        pod_stats: dict[str, dict[str, int]],
    ) -> None:
        """Generate comprehensive reports"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Project summary report
        project_data = []
        for project_name, project in projects.items():
            total_pods = sum(
                pod_stats.get(ns, {}).get("total", 0) for ns in project.namespaces
            )
            running_pods = sum(
                pod_stats.get(ns, {}).get("running", 0) for ns in project.namespaces
            )
            failed_pods = sum(
                pod_stats.get(ns, {}).get("failed", 0) for ns in project.namespaces
            )

            project_data.append(
                {
                    "project_name": project_name,
                    "pattern": project.pattern,
                    "namespace_count": len(project.namespaces),
                    "namespaces": ", ".join(project.namespaces),
                    "user_count": len(project.users),
                    "users": ", ".join(project.users),
                    "service_account_count": len(project.service_accounts),
                    "service_accounts": ", ".join(project.service_accounts),
                    "total_pods": total_pods,
                    "running_pods": running_pods,
                    "failed_pods": failed_pods,
                    "health_status": "🟢 Healthy"
                    if failed_pods == 0
                    else "🟡 Issues"
                    if failed_pods < 5
                    else "🔴 Problems",
                }
            )

        if project_data:
            df_projects = pd.DataFrame(project_data)
            df_projects = df_projects.sort_values("total_pods", ascending=False)
            projects_file = self.data_dir / f"k8s_projects_{timestamp}.csv"
            df_projects.to_csv(projects_file, index=False)
            print(f"📊 Projects report: {projects_file}")

        # User access report
        user_data = []
        for _user_key, user_access in users.items():
            # Skip system users
            if any(
                sys in user_access.username.lower()
                for sys in ["system", "gke-", "kubernetes-"]
            ):
                continue

            user_data.append(
                {
                    "username": user_access.username,
                    "type": user_access.subject_type,
                    "namespace_count": len(user_access.namespaces),
                    "namespaces": ", ".join(user_access.namespaces),
                    "cluster_role_count": len(user_access.cluster_roles),
                    "cluster_roles": ", ".join(user_access.cluster_roles),
                    "namespace_roles": str(user_access.namespace_roles),
                    "service_account_namespace": user_access.service_account_namespace
                    or "",
                }
            )

        if user_data:
            df_users = pd.DataFrame(user_data)
            df_users = df_users.sort_values(
                ["type", "namespace_count"], ascending=[True, False]
            )
            users_file = self.data_dir / f"k8s_user_access_{timestamp}.csv"
            df_users.to_csv(users_file, index=False)
            print(f"👥 User access report: {users_file}")

        # Namespace details report
        namespace_data = []
        for project_name, project in projects.items():
            for ns in project.namespaces:
                stats = pod_stats.get(ns, {})
                namespace_data.append(
                    {
                        "namespace": ns,
                        "project": project_name,
                        "pattern": project.pattern,
                        "total_pods": stats.get("total", 0),
                        "running_pods": stats.get("running", 0),
                        "failed_pods": stats.get("failed", 0),
                        "pending_pods": stats.get("pending", 0),
                        "users": ", ".join(project.users),
                        "service_accounts": ", ".join(project.service_accounts),
                    }
                )

        if namespace_data:
            df_namespaces = pd.DataFrame(namespace_data)
            df_namespaces = df_namespaces.sort_values(
                ["project", "total_pods"], ascending=[True, False]
            )
            ns_file = self.data_dir / f"k8s_namespaces_{timestamp}.csv"
            df_namespaces.to_csv(ns_file, index=False)
            print(f"📋 Namespace details: {ns_file}")

    def print_summary(
        self,
        projects: dict[str, ProjectGroup],
        users: dict[str, UserAccess],
        pod_stats: dict[str, dict[str, int]],
    ) -> None:
        """Print analysis summary"""
        print("\n" + "=" * 70)
        print("🚀 KUBERNETES PROJECT & RBAC ANALYSIS")
        print("=" * 70)

        total_pods = sum(stats.get("total", 0) for stats in pod_stats.values())
        real_users = [u for u in users.values() if u.subject_type == "User"]
        service_accounts = [
            u
            for u in users.values()
            if u.subject_type == "ServiceAccount"
            and not any(
                sys in u.username.lower() for sys in ["system", "default", "gke-"]
            )
        ]

        print("📊 Overview:")
        print(f"   • Detected Projects: {len(projects)}")
        print(
            f"   • Total Namespaces: {sum(len(p.namespaces) for p in projects.values())}"
        )
        print(f"   • Total Pods: {total_pods}")
        print(f"   • Human Users: {len(real_users)}")
        print(f"   • Application Service Accounts: {len(service_accounts)}")

        # Show top projects by pod count
        projects_by_size = sorted(
            projects.items(),
            key=lambda x: sum(
                pod_stats.get(ns, {}).get("total", 0) for ns in x[1].namespaces
            ),
            reverse=True,
        )

        print("\n📁 Top Projects by Pod Count:")
        for project_name, project in projects_by_size[:10]:
            total_project_pods = sum(
                pod_stats.get(ns, {}).get("total", 0) for ns in project.namespaces
            )

            if total_project_pods == 0:
                continue

            print(f"\n   🔹 {project_name} ({total_project_pods} pods)")
            print(f"     Pattern: {project.pattern}")
            print(f"     Namespaces: {', '.join(project.namespaces)}")

            if project.users:
                print(f"     👤 Users: {', '.join(project.users)}")

            if project.service_accounts:
                sa_display = project.service_accounts[:3]  # Show first 3
                if len(project.service_accounts) > 3:
                    sa_display.append(f"... +{len(project.service_accounts) - 3} more")
                print(f"     🤖 Service Accounts: {', '.join(sa_display)}")

        print("\n👥 User Summary:")
        if real_users:
            print("   Human Users found:")
            for user in sorted(
                real_users, key=lambda u: len(u.namespaces), reverse=True
            )[:5]:
                ns_count = len(user.namespaces)
                print(f"   • {user.username}: access to {ns_count} namespace(s)")
        else:
            print("   • No human users found with namespace access")
            print("   • This might be a cluster using:")
            print("     - Service accounts only")
            print("     - External identity providers")
            print("     - Google IAM (for GKE)")

        print("\n💡 Next Steps:")
        print("   • Check generated CSV files for detailed analysis")
        print("   • Review RBAC bindings for security compliance")
        print("   • Consider implementing namespace-based project structure")

    def run_analysis(self) -> None:
        """Run complete analysis"""
        print("🚀 Starting Kubernetes RBAC and Project Analysis")
        print("=" * 60)

        try:
            # Get basic data
            namespaces = self.get_all_namespaces()
            if not namespaces:
                print("❌ No non-system namespaces found")
                return

            # Detect project patterns
            projects = self.detect_project_patterns(namespaces)

            # Analyze RBAC
            users = self.analyze_rbac_bindings()

            # Get pod statistics
            pod_stats = self.get_pod_statistics()

            # Map users to projects
            projects = self.map_users_to_projects(projects, users)

            # Generate reports
            self.generate_reports(projects, users, pod_stats)

            # Print summary
            self.print_summary(projects, users, pod_stats)

        except Exception as e:
            print(f"❌ Analysis failed: {e}")
            import traceback

            traceback.print_exc()


def main() -> None:
    analyzer = K8sRBACAnalyzer()
    analyzer.run_analysis()


if __name__ == "__main__":
    main()
