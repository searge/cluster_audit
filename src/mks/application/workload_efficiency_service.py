#!/usr/bin/env python3
"""
Kubernetes Workload Resource Efficiency Table Generator
Generates a table showing resource usage efficiency per workload
(Deployment/StatefulSet/DaemonSet)
"""

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from mks.domain.namespace_policy import is_system_namespace
from mks.domain.quantity_parser import parse_cpu, parse_memory
from mks.infrastructure.kubectl_client import KubectlError, kubectl_json, kubectl_text

EXTRA_SYSTEM_NAMESPACES = frozenset({"cattle-system", "rancher-system"})
_BYTES_IN_MB = 1024 * 1024


class WorkloadMetrics(NamedTuple):
    """Resource metrics for a workload"""

    namespace: str
    workload: str
    workload_type: str
    cpu_req: int  # millicores
    cpu_lim: int  # millicores
    cpu_use: int  # millicores
    cpu_eff: float  # percentage
    cpu_waste: int  # millicores
    mem_req: int  # MB
    mem_lim: int  # MB
    mem_use: int  # MB
    mem_eff: float  # percentage
    mem_waste: int  # MB


class WorkloadEfficiencyAnalyzer:
    """Analyzes Kubernetes workload resource efficiency"""

    def __init__(self, data_dir: str = "reports", include_system: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now()
        self.include_system = include_system

        # Kind to short name mapping
        self.kind_short_names = {
            "Deployment": "deploy",
            "StatefulSet": "sts",
            "DaemonSet": "ds",
            "Job": "job",
            "CronJob": "cronjob",
            "ReplicaSet": "rs",
            "Pod": "pod",
        }

    def is_system_namespace(self, namespace: str) -> bool:
        """Check if namespace should be excluded from analysis"""
        return is_system_namespace(
            namespace,
            include_system=self.include_system,
            extra_namespaces=EXTRA_SYSTEM_NAMESPACES,
        )

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            return kubectl_json(command)
        except KubectlError as e:
            raise RuntimeError(f"Error running kubectl: {e}") from e

    def run_kubectl_top(self, command: str) -> list[str]:
        """Execute kubectl top command and return output lines"""
        try:
            output = kubectl_text(command)
            return output.strip().split("\n")
        except KubectlError as e:
            print(
                "⚠️  Warning: kubectl top command failed "
                f"(metrics-server may not be available): {e}"
            )
            return []

    def get_pod_specs(self) -> dict[str, Any]:
        """Get resource specs for all pods"""
        print("🔍 Fetching pod resource specifications...")
        return self.run_kubectl("get pods --all-namespaces")

    def get_pod_usage(self) -> dict[str, dict[str, int]]:
        """Get real resource usage for all pods"""
        print("📊 Fetching pod resource usage...")
        lines = self.run_kubectl_top("top pods --all-namespaces --no-headers")

        usage_data = {}
        for line in lines:
            if not line.strip():
                continue

            parts = line.split()
            if len(parts) < 4:
                continue

            namespace = parts[0]
            pod_name = parts[1]
            cpu_usage = parse_cpu(parts[2])
            memory_usage = int(parse_memory(parts[3]) / _BYTES_IN_MB)

            pod_key = f"{namespace}/{pod_name}"
            usage_data[pod_key] = {"cpu": cpu_usage, "memory": memory_usage}

        return usage_data

    def get_workload_from_owner_ref(self, pod: dict[str, Any]) -> tuple[str, str]:
        """Extract workload name and type from ownerReferences"""
        owner_refs = pod["metadata"].get("ownerReferences", [])

        if not owner_refs:
            # No owner, treat as standalone pod
            return pod["metadata"]["name"], "pod"

        # Get the first owner reference (usually the direct parent)
        owner = owner_refs[0]
        owner_kind = owner["kind"]
        owner_name = owner["name"]

        # If it's a ReplicaSet, look for the Deployment that owns it
        if owner_kind == "ReplicaSet":
            try:
                rs_data = self.run_kubectl(
                    f"get replicaset {owner_name} -n {pod['metadata']['namespace']}"
                )
                rs_owner_refs = rs_data["metadata"].get("ownerReferences", [])
                if rs_owner_refs and rs_owner_refs[0]["kind"] == "Deployment":
                    deployment_name = rs_owner_refs[0].get("name")
                    if deployment_name:
                        return deployment_name, "deploy"
            except (RuntimeError, KeyError, TypeError):
                pass

        # Return the short name for the kind
        short_name = self.kind_short_names.get(owner_kind) or owner_kind.lower()
        return owner_name, short_name

    def _collect_workload_groups(
        self, pods_data: dict[str, Any], usage_data: dict[str, dict[str, int]]
    ) -> defaultdict[str, list[dict[str, Any]]]:
        """Group runnable non-system pods by workload key."""
        workload_pods: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for pod in pods_data.get("items", []):
            namespace = pod["metadata"]["namespace"]
            if self.is_system_namespace(namespace):
                continue
            if pod["status"]["phase"] != "Running":
                continue

            pod_name = pod["metadata"]["name"]
            pod_key = f"{namespace}/{pod_name}"
            workload_name, workload_type = self.get_workload_from_owner_ref(pod)
            workload_key = f"{namespace}/{workload_name}/{workload_type}"
            workload_pods[workload_key].append(
                {"pod": pod, "usage": usage_data.get(pod_key, {"cpu": 0, "memory": 0})}
            )
        return workload_pods

    def _build_workload_metric(
        self,
        workload_key: str,
        pods: list[dict[str, Any]],
    ) -> WorkloadMetrics:
        """Aggregate pod-level resources into one workload-level metric."""
        namespace, workload_name, workload_type = workload_key.split("/", 2)
        totals = self._aggregate_workload_totals(pods)

        cpu_eff = (
            (totals["cpu_use"] / totals["cpu_req"] * 100)
            if totals["cpu_req"] > 0
            else 0
        )
        mem_eff = (
            (totals["mem_use"] / totals["mem_req"] * 100)
            if totals["mem_req"] > 0
            else 0
        )
        cpu_waste = int(max(0, totals["cpu_req"] - totals["cpu_use"]))
        mem_waste = int(max(0, totals["mem_req"] - totals["mem_use"]))
        return WorkloadMetrics(
            namespace=namespace,
            workload=workload_name,
            workload_type=workload_type,
            cpu_req=int(totals["cpu_req"]),
            cpu_lim=int(totals["cpu_lim"]),
            cpu_use=int(totals["cpu_use"]),
            cpu_eff=cpu_eff,
            cpu_waste=cpu_waste,
            mem_req=int(totals["mem_req"]),
            mem_lim=int(totals["mem_lim"]),
            mem_use=int(totals["mem_use"]),
            mem_eff=mem_eff,
            mem_waste=mem_waste,
        )

    def _aggregate_workload_totals(
        self,
        pods: list[dict[str, Any]],
    ) -> dict[str, float]:
        """Accumulate requests, limits, and usage across pods in one workload."""
        totals: dict[str, float] = {
            "cpu_req": 0,
            "cpu_lim": 0,
            "cpu_use": 0,
            "mem_req": 0,
            "mem_lim": 0,
            "mem_use": 0,
        }
        for pod_data in pods:
            pod = pod_data["pod"]
            usage = pod_data["usage"]
            for container in pod["spec"]["containers"]:
                resources = container.get("resources", {})
                requests = resources.get("requests", {})
                limits = resources.get("limits", {})
                totals["cpu_req"] += parse_cpu(requests.get("cpu", "0"))
                totals["cpu_lim"] += parse_cpu(limits.get("cpu", "0"))
                totals["mem_req"] += int(
                    parse_memory(requests.get("memory", "0")) / _BYTES_IN_MB
                )
                totals["mem_lim"] += int(
                    parse_memory(limits.get("memory", "0")) / _BYTES_IN_MB
                )
            totals["cpu_use"] += usage["cpu"]
            totals["mem_use"] += usage["memory"]
        return totals

    def analyze_workloads(self) -> list[WorkloadMetrics]:
        """Analyze workload efficiency by aggregating pod data"""
        pods_data = self.get_pod_specs()
        usage_data = self.get_pod_usage()
        print("🔄 Aggregating pods by workload...")
        workload_pods = self._collect_workload_groups(pods_data, usage_data)

        # Calculate metrics per workload
        workload_metrics = [
            self._build_workload_metric(workload_key, pods)
            for workload_key, pods in workload_pods.items()
        ]

        # Sort by highest CPU waste first
        return sorted(workload_metrics, key=lambda x: x.cpu_waste, reverse=True)

    def format_console_table(self, metrics: list[WorkloadMetrics]) -> None:
        """Print formatted table to console"""
        if not metrics:
            print("No workload data found.")
            return

        print("\n" + "=" * 150)
        print("KUBERNETES WORKLOAD RESOURCE EFFICIENCY REPORT")
        print("=" * 150)

        # Table headers
        headers = [
            "Namespace",
            "Workload",
            "Type",
            "CPU Req",
            "CPU Lim",
            "CPU Use",
            "CPU Eff",
            "CPU Waste",
            "Mem Req",
            "Mem Lim",
            "Mem Use",
            "Mem Eff",
            "Mem Waste",
        ]

        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for metric in metrics:
            values = [
                metric.namespace,
                metric.workload,
                metric.workload_type,
                str(metric.cpu_req),
                str(metric.cpu_lim),
                str(metric.cpu_use),
                f"{metric.cpu_eff:.1f}%",
                str(metric.cpu_waste),
                str(metric.mem_req),
                str(metric.mem_lim),
                str(metric.mem_use),
                f"{metric.mem_eff:.1f}%",
                str(metric.mem_waste),
            ]
            for i, val in enumerate(values):
                col_widths[i] = max(col_widths[i], len(str(val)))

        # Print header
        header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        separator_line = "-+-".join("-" * w for w in col_widths)

        print(header_line)
        print(separator_line)

        # Print data rows
        for metric in metrics:
            values = [
                metric.namespace.ljust(col_widths[0]),
                metric.workload.ljust(col_widths[1]),
                metric.workload_type.ljust(col_widths[2]),
                str(metric.cpu_req).rjust(col_widths[3]),
                str(metric.cpu_lim).rjust(col_widths[4]),
                str(metric.cpu_use).rjust(col_widths[5]),
                f"{metric.cpu_eff:.1f}%".rjust(col_widths[6]),
                str(metric.cpu_waste).rjust(col_widths[7]),
                str(metric.mem_req).rjust(col_widths[8]),
                str(metric.mem_lim).rjust(col_widths[9]),
                str(metric.mem_use).rjust(col_widths[10]),
                f"{metric.mem_eff:.1f}%".rjust(col_widths[11]),
                str(metric.mem_waste).rjust(col_widths[12]),
            ]
            print(" | ".join(values))

        print("=" * 150)

    def export_csv(self, metrics: list[WorkloadMetrics]) -> str:
        """Export metrics to CSV file"""
        timestamp_str = self.timestamp.strftime("%Y%m%d_%H%M%S")
        filename = self.data_dir / f"workload_efficiency_{timestamp_str}.csv"

        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "Namespace",
                "Workload",
                "Type",
                "CPU_Req",
                "CPU_Lim",
                "CPU_Use",
                "CPU_Eff",
                "CPU_Waste",
                "Mem_Req",
                "Mem_Lim",
                "Mem_Use",
                "Mem_Eff",
                "Mem_Waste",
            ]

            writer = csv.writer(csvfile)
            writer.writerow(fieldnames)

            for metric in metrics:
                writer.writerow(
                    [
                        metric.namespace,
                        metric.workload,
                        metric.workload_type,
                        metric.cpu_req,
                        metric.cpu_lim,
                        metric.cpu_use,
                        f"{metric.cpu_eff:.1f}%",
                        metric.cpu_waste,
                        metric.mem_req,
                        metric.mem_lim,
                        metric.mem_use,
                        f"{metric.mem_eff:.1f}%",
                        metric.mem_waste,
                    ]
                )

        return str(filename)

    def print_summary(self, metrics: list[WorkloadMetrics]) -> None:
        """Print summary statistics"""
        if not metrics:
            return

        total_cpu_waste = sum(m.cpu_waste for m in metrics)
        total_mem_waste = sum(m.mem_waste for m in metrics)
        total_cpu_req = sum(m.cpu_req for m in metrics)
        total_mem_req = sum(m.mem_req for m in metrics)

        avg_cpu_eff = sum(m.cpu_eff for m in metrics) / len(metrics)
        avg_mem_eff = sum(m.mem_eff for m in metrics) / len(metrics)

        print("\n📊 SUMMARY STATISTICS")
        print(f"   Total Workloads: {len(metrics)}")
        print(
            f"   Total CPU Waste: {total_cpu_waste:,} millicores "
            f"({total_cpu_waste / 1000:.1f} cores)"
        )
        print(
            f"   Total Memory Waste: {total_mem_waste:,} MB "
            f"({total_mem_waste / 1024:.1f} GB)"
        )
        print(f"   Average CPU Efficiency: {avg_cpu_eff:.1f}%")
        print(f"   Average Memory Efficiency: {avg_mem_eff:.1f}%")

        if total_cpu_req > 0:
            overall_cpu_waste_pct = (total_cpu_waste / total_cpu_req) * 100
            print(f"   Overall CPU Waste Rate: {overall_cpu_waste_pct:.1f}%")

        if total_mem_req > 0:
            overall_mem_waste_pct = (total_mem_waste / total_mem_req) * 100
            print(f"   Overall Memory Waste Rate: {overall_mem_waste_pct:.1f}%")

    def run_analysis(self) -> None:
        """Run complete workload efficiency analysis"""
        print("🚀 Starting Kubernetes Workload Efficiency Analysis")
        print(f"📅 Timestamp: {self.timestamp}")
        print(f"🔧 Include System Namespaces: {self.include_system}")

        try:
            # Analyze workloads
            metrics = self.analyze_workloads()

            # Display results
            self.format_console_table(metrics)

            # Export to CSV
            csv_file = self.export_csv(metrics)
            print(f"\n💾 CSV exported to: {csv_file}")

            # Print summary
            self.print_summary(metrics)

            print(f"\n✅ Analysis completed! Found {len(metrics)} workloads.")

        except KeyboardInterrupt:
            raise RuntimeError("Analysis interrupted by user") from None
        except Exception as e:
            raise RuntimeError(f"Error during analysis: {e}") from e


def execute_workload_efficiency_audit(
    *, include_system: bool = False, data_dir: str = "reports"
) -> None:
    """Execute workload efficiency audit use-case."""
    analyzer = WorkloadEfficiencyAnalyzer(
        data_dir=data_dir, include_system=include_system
    )
    analyzer.run_analysis()


def execute(*, include_system: bool = False, data_dir: str = "reports") -> None:
    """Backward-compatible alias for execute_workload_efficiency_audit."""
    execute_workload_efficiency_audit(
        include_system=include_system,
        data_dir=data_dir,
    )
