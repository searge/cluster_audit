#!/usr/bin/env python3
"""
Kubernetes RBAC and Project Analyzer
Analyzes users, service accounts, and access patterns in plain Kubernetes
"""

import re
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mks.domain.namespace_policy import is_system_namespace
from mks.domain.pod_reporting import count_pods_by_namespace, health_status_for_failed
from mks.infrastructure.kubectl_client import KubectlError, kubectl_json


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
    """Analyze RBAC bindings and infer project-level access groupings."""

    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # Common system namespaces to exclude
        self.system_namespaces = {
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "default",
        }

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            return kubectl_json(command)
        except KubectlError as e:
            raise RuntimeError(f"Error running kubectl: {e}") from e

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
            if is_system_namespace(
                name,
                extra_namespaces=frozenset(self.system_namespaces),
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
        return count_pods_by_namespace(
            pods,
            namespace_filter=lambda namespace: namespace not in self.system_namespaces,
        )

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
        project_data = self._build_project_rows(projects, pod_stats)
        user_data = self._build_user_rows(users)
        namespace_data = self._build_namespace_rows(projects, pod_stats)

        if project_data:
            projects_file = self.data_dir / f"k8s_projects_{timestamp}.csv"
            self._write_sorted_csv(
                project_data,
                projects_file,
                sort_by=["total_pods"],
                ascending=[False],
            )
            print(f"📊 Projects report: {projects_file}")

        if user_data:
            users_file = self.data_dir / f"k8s_user_access_{timestamp}.csv"
            self._write_sorted_csv(
                user_data,
                users_file,
                sort_by=["type", "namespace_count"],
                ascending=[True, False],
            )
            print(f"👥 User access report: {users_file}")

        if namespace_data:
            ns_file = self.data_dir / f"k8s_namespaces_{timestamp}.csv"
            self._write_sorted_csv(
                namespace_data,
                ns_file,
                sort_by=["project", "total_pods"],
                ascending=[True, False],
            )
            print(f"📋 Namespace details: {ns_file}")

    @staticmethod
    def _write_sorted_csv(
        rows: list[dict[str, Any]],
        file_path: Path,
        *,
        sort_by: list[str],
        ascending: list[bool],
    ) -> None:
        """Persist rows to CSV using a deterministic sort order."""
        df = pd.DataFrame(rows)
        df = df.sort_values(sort_by, ascending=ascending)
        df.to_csv(file_path, index=False)

    @staticmethod
    def _build_project_rows(
        projects: dict[str, ProjectGroup],
        pod_stats: dict[str, dict[str, int]],
    ) -> list[dict[str, Any]]:
        """Build per-project summary rows."""
        rows: list[dict[str, Any]] = []
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

            rows.append(
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
                    "health_status": health_status_for_failed(failed_pods),
                }
            )
        return rows

    @staticmethod
    def _build_user_rows(users: dict[str, UserAccess]) -> list[dict[str, Any]]:
        """Build per-user access rows for reporting."""
        rows: list[dict[str, Any]] = []
        for _user_key, user_access in users.items():
            # Skip system users
            if any(
                sys in user_access.username.lower()
                for sys in ["system", "gke-", "kubernetes-"]
            ):
                continue

            rows.append(
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
        return rows

    @staticmethod
    def _build_namespace_rows(
        projects: dict[str, ProjectGroup],
        pod_stats: dict[str, dict[str, int]],
    ) -> list[dict[str, Any]]:
        """Build per-namespace project attribution rows."""
        rows: list[dict[str, Any]] = []
        for project_name, project in projects.items():
            for ns in project.namespaces:
                stats = pod_stats.get(ns, {})
                rows.append(
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
        return rows

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
            "   • Total Namespaces: "
            f"{sum(len(p.namespaces) for p in projects.values())}"
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

        except (RuntimeError, ValueError, KeyError, KubectlError) as e:
            print(f"❌ Analysis failed: {e}")
            traceback.print_exc()


def execute_rbac_audit(data_dir: str = "reports") -> None:
    """Execute RBAC audit use-case."""
    analyzer = K8sRBACAnalyzer(data_dir=data_dir)
    analyzer.run_analysis()


def execute() -> None:
    """Backward-compatible alias for execute_rbac_audit."""
    execute_rbac_audit()


if __name__ == "__main__":
    execute_rbac_audit()
