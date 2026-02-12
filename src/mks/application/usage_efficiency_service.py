#!/usr/bin/env python3
"""
Real Kubernetes Resource Usage Analysis
Compares actual usage vs requests/limits to find realistic values
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mks.domain.quantity_parser import parse_cpu, parse_memory
from mks.infrastructure.kubectl_client import (
    KubectlError,
    kubectl_json_or_empty,
    kubectl_text,
)


class RealUsageAnalyzer:
    """Analyze live pod usage versus requested resources and report waste."""

    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now()

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        return kubectl_json_or_empty(command)

    @staticmethod
    def _calculate_pod_totals(spec: dict[str, Any]) -> tuple[float, float, int, int]:
        """Return total requests and limits for a pod."""
        total_cpu_request = sum(c["cpu_request"] for c in spec["containers"])
        total_cpu_limit = sum(c["cpu_limit"] for c in spec["containers"])
        total_memory_request = sum(c["memory_request"] for c in spec["containers"])
        total_memory_limit = sum(c["memory_limit"] for c in spec["containers"])
        return (
            total_cpu_request,
            total_cpu_limit,
            total_memory_request,
            total_memory_limit,
        )

    @staticmethod
    def _categorize_cpu_efficiency(cpu_usage: float, cpu_efficiency: float) -> str:
        """Map CPU efficiency values to a human-readable category."""
        if cpu_usage == 0:
            return "Idle"
        if cpu_efficiency > 80:
            return "Efficient"
        if cpu_efficiency > 50:
            return "Moderate"
        if cpu_efficiency > 20:
            return "Wasteful"
        return "Very Wasteful"

    @staticmethod
    def _build_analysis_row(
        pod_key: str,
        spec: dict[str, Any],
        usage: dict[str, float],
        totals: tuple[float, float, int, int],
        category: str,
    ) -> dict[str, Any]:
        """Build a normalized analysis row for a single pod."""
        metrics = RealUsageAnalyzer._calculate_waste_and_efficiency(usage, totals)
        (
            total_cpu_request,
            total_cpu_limit,
            total_memory_request,
            total_memory_limit,
        ) = totals
        return {
            "pod_key": pod_key,
            "namespace": spec["namespace"],
            "node": spec["node"],
            "container_count": len(spec["containers"]),
            "cpu_usage_m": round(usage["cpu_usage"], 1),
            "cpu_request_m": round(total_cpu_request, 1),
            "cpu_limit_m": round(total_cpu_limit, 1),
            "memory_usage_mb": round(usage["memory_usage"] / 1024 / 1024, 1),
            "memory_request_mb": round(total_memory_request / 1024 / 1024, 1),
            "memory_limit_mb": round(total_memory_limit / 1024 / 1024, 1),
            "cpu_request_waste_m": round(metrics["cpu_request_waste"], 1),
            "cpu_limit_waste_m": round(metrics["cpu_limit_waste"], 1),
            "memory_request_waste_mb": round(
                metrics["memory_request_waste"] / 1024 / 1024, 1
            ),
            "memory_limit_waste_mb": round(
                metrics["memory_limit_waste"] / 1024 / 1024, 1
            ),
            "cpu_efficiency_pct": round(metrics["cpu_efficiency"], 1),
            "memory_efficiency_pct": round(metrics["memory_efficiency"], 1),
            "category": category,
            "has_limits": total_cpu_limit > 0 and total_memory_limit > 0,
            "overprovisioned": metrics["cpu_efficiency"] < 20
            and total_cpu_request > 100,
        }

    @staticmethod
    def _calculate_waste_and_efficiency(
        usage: dict[str, float],
        totals: tuple[float, float, int, int],
    ) -> dict[str, float]:
        """Return waste and efficiency values for CPU and memory dimensions."""
        cpu_usage = usage["cpu_usage"]
        memory_usage = usage["memory_usage"]
        total_cpu_request, total_cpu_limit, total_memory_request, total_memory_limit = (
            totals
        )
        cpu_request_waste = (
            total_cpu_request - cpu_usage if total_cpu_request > 0 else 0
        )
        cpu_limit_waste = total_cpu_limit - cpu_usage if total_cpu_limit > 0 else 0
        memory_request_waste = (
            total_memory_request - memory_usage if total_memory_request > 0 else 0
        )
        memory_limit_waste = (
            total_memory_limit - memory_usage if total_memory_limit > 0 else 0
        )
        cpu_efficiency = (
            (cpu_usage / total_cpu_request * 100) if total_cpu_request > 0 else 0
        )
        memory_efficiency = (
            (memory_usage / total_memory_request * 100)
            if total_memory_request > 0
            else 0
        )
        return {
            "cpu_request_waste": cpu_request_waste,
            "cpu_limit_waste": cpu_limit_waste,
            "memory_request_waste": memory_request_waste,
            "memory_limit_waste": memory_limit_waste,
            "cpu_efficiency": cpu_efficiency,
            "memory_efficiency": memory_efficiency,
        }

    @staticmethod
    def _build_stats(df: pd.DataFrame) -> dict[str, float]:
        """Compute aggregate cluster statistics from analysis dataframe."""
        cpu_request_values = pd.to_numeric(df["cpu_request_m"]).to_numpy(dtype=float)
        cpu_used_values = pd.to_numeric(df["cpu_usage_m"]).to_numpy(dtype=float)
        memory_request_values = pd.to_numeric(df["memory_request_mb"]).to_numpy(
            dtype=float
        )
        memory_used_values = pd.to_numeric(df["memory_usage_mb"]).to_numpy(dtype=float)
        cpu_waste_values = pd.to_numeric(df["cpu_request_waste_m"]).to_numpy(
            dtype=float
        )
        memory_waste_values = pd.to_numeric(df["memory_request_waste_mb"]).to_numpy(
            dtype=float
        )
        cpu_efficiency_values = pd.to_numeric(
            df.loc[df["cpu_request_m"] > 0, "cpu_efficiency_pct"]
        ).to_numpy(dtype=float)
        memory_efficiency_values = pd.to_numeric(
            df.loc[df["memory_request_mb"] > 0, "memory_efficiency_pct"]
        ).to_numpy(dtype=float)
        return {
            "total_pods": float(len(df)),
            "total_cpu_requested": float(cpu_request_values.sum()),
            "total_cpu_used": float(cpu_used_values.sum()),
            "total_memory_requested": float(memory_request_values.sum()),
            "total_memory_used": float(memory_used_values.sum()),
            "cpu_waste_total": float(cpu_waste_values.sum()),
            "memory_waste_total": float(memory_waste_values.sum()),
            "avg_cpu_efficiency": (
                float(cpu_efficiency_values.mean())
                if cpu_efficiency_values.size > 0
                else 0.0
            ),
            "avg_memory_efficiency": (
                float(memory_efficiency_values.mean())
                if memory_efficiency_values.size > 0
                else 0.0
            ),
        }

    @staticmethod
    def _recommended_defaults(df: pd.DataFrame) -> dict[str, str]:
        """Suggest default requests/limits based on active workload percentiles."""
        active_pods = df[df["cpu_usage_m"] > 0]
        if active_pods.empty:
            return {
                "cpu_request": "50m",
                "cpu_limit": "100m",
                "memory_request": "64Mi",
                "memory_limit": "128Mi",
            }

        cpu_p75 = active_pods["cpu_usage_m"].quantile(0.75)
        cpu_p90 = active_pods["cpu_usage_m"].quantile(0.9)
        memory_p75 = active_pods["memory_usage_mb"].quantile(0.75)
        memory_p90 = active_pods["memory_usage_mb"].quantile(0.9)
        return {
            "cpu_request": f"{max(50, int(cpu_p75))}m",
            "cpu_limit": f"{max(100, int(cpu_p90 * 2))}m",
            "memory_request": f"{max(64, int(memory_p75))}Mi",
            "memory_limit": f"{max(128, int(memory_p90 * 1.5))}Mi",
        }

    def get_pod_specs(self) -> dict[str, dict[str, Any]]:
        """Get resource specs for all running pods"""
        print("📋 Getting pod resource specifications...")

        pods_data = self.run_kubectl(
            "get pods -A --field-selector=status.phase=Running"
        )
        pod_specs = {}

        for pod in pods_data.get("items", []):
            pod_key = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"

            containers = []
            for container in pod["spec"]["containers"]:
                resources = container.get("resources", {})
                requests = resources.get("requests", {})
                limits = resources.get("limits", {})

                containers.append(
                    {
                        "name": container["name"],
                        "cpu_request": float(parse_cpu(requests.get("cpu", "0"))),
                        "cpu_limit": float(parse_cpu(limits.get("cpu", "0"))),
                        "memory_request": parse_memory(requests.get("memory", "0Ki")),
                        "memory_limit": parse_memory(limits.get("memory", "0Ki")),
                    }
                )

            pod_specs[pod_key] = {
                "namespace": pod["metadata"]["namespace"],
                "node": pod["spec"].get("nodeName", "Unknown"),
                "containers": containers,
            }

        return pod_specs

    def get_real_usage(self) -> dict[str, dict[str, float]]:
        """Get actual resource usage from kubectl top"""
        print("📊 Getting real resource usage...")

        # Get pod metrics
        try:
            output = kubectl_text("top pods -A --no-headers")

            real_usage = {}
            for line in output.strip().split("\n"):
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 4:
                    namespace = parts[0]
                    pod_name = parts[1]
                    cpu_usage = parts[2]
                    memory_usage = parts[3]

                    pod_key = f"{namespace}/{pod_name}"
                    real_usage[pod_key] = {
                        "cpu_usage": float(parse_cpu(cpu_usage)),
                        "memory_usage": parse_memory(memory_usage),
                    }

            return real_usage

        except KubectlError as e:
            print(f"⚠️  Could not get metrics: {e}")
            print("   Make sure metrics-server is running in the cluster")
            return {}

    def analyze_usage_patterns(
        self,
        pod_specs: dict[str, dict[str, Any]],
        real_usage: dict[str, dict[str, float]],
    ) -> list[dict[str, Any]]:
        """Analyze usage patterns and waste"""
        print("🔍 Analyzing usage patterns...")

        analysis_data = []

        for pod_key, spec in pod_specs.items():
            usage = real_usage.get(pod_key, {"cpu_usage": 0, "memory_usage": 0})
            cpu_usage = usage["cpu_usage"]
            (
                total_cpu_request,
                total_cpu_limit,
                total_memory_request,
                total_memory_limit,
            ) = self._calculate_pod_totals(spec)
            cpu_efficiency = (
                (cpu_usage / total_cpu_request * 100) if total_cpu_request > 0 else 0
            )
            category = self._categorize_cpu_efficiency(cpu_usage, cpu_efficiency)
            analysis_data.append(
                self._build_analysis_row(
                    pod_key=pod_key,
                    spec=spec,
                    usage=usage,
                    totals=(
                        total_cpu_request,
                        total_cpu_limit,
                        total_memory_request,
                        total_memory_limit,
                    ),
                    category=category,
                )
            )

        return analysis_data

    def generate_recommendations(
        self, analysis_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Generate resource recommendations based on real usage"""
        print("💡 Generating resource recommendations...")

        df = pd.DataFrame(analysis_data)

        if df.empty:
            return {}

        stats = self._build_stats(df)

        # Category breakdown
        category_stats = df["category"].value_counts().to_dict()

        # Namespace analysis
        ns_stats = (
            df.groupby("namespace")
            .agg(
                {
                    "cpu_usage_m": "sum",
                    "cpu_request_m": "sum",
                    "cpu_request_waste_m": "sum",
                    "memory_usage_mb": "sum",
                    "memory_request_mb": "sum",
                    "memory_request_waste_mb": "sum",
                }
            )
            .round(1)
        )

        recommended_defaults = self._recommended_defaults(df)

        return {
            "stats": stats,
            "category_breakdown": category_stats,
            "namespace_stats": ns_stats,
            "recommended_defaults": recommended_defaults,
            "top_wasters": df.nlargest(10, "cpu_request_waste_m")[
                [
                    "pod_key",
                    "cpu_usage_m",
                    "cpu_request_m",
                    "cpu_request_waste_m",
                    "category",
                ]
            ].to_dict("records"),
            "most_efficient": df[df["cpu_efficiency_pct"] > 0]
            .nlargest(10, "cpu_efficiency_pct")[
                ["pod_key", "cpu_usage_m", "cpu_request_m", "cpu_efficiency_pct"]
            ]
            .to_dict("records"),
        }

    def save_analysis(
        self, analysis_data: list[dict[str, Any]], recommendations: dict[str, Any]
    ) -> None:
        """Save analysis results"""
        timestamp_str = self.timestamp.strftime("%Y%m%d_%H%M%S")

        # Save detailed data
        df = pd.DataFrame(analysis_data)
        csv_file = self.data_dir / f"real_usage_analysis_{timestamp_str}.csv"
        df.to_csv(csv_file, index=False)

        # Save recommendations
        rec_file = self.data_dir / f"usage_recommendations_{timestamp_str}.md"

        with open(rec_file, "w", encoding="utf-8") as f:
            f.write("# Real Resource Usage Analysis\n")
            f.write(
                f"**Generated**: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )

            # Overall stats
            stats = recommendations.get("stats", {})
            cpu_waste_pct = (
                stats.get("cpu_waste_total", 0)
                / max(stats.get("total_cpu_requested", 1), 1)
                * 100
            )
            memory_waste_pct = (
                stats.get("memory_waste_total", 0)
                / max(stats.get("total_memory_requested", 1), 1)
                * 100
            )
            f.write("## 📊 Cluster Resource Efficiency\n\n")
            f.write("| Metric | Value |\n")
            f.write("|--------|-------|\n")
            f.write(f"| **Total Pods Analyzed** | {stats.get('total_pods', 0)} |\n")
            f.write(
                f"| **CPU Requested** | {stats.get('total_cpu_requested', 0):.1f}m |\n"
            )
            f.write(
                f"| **CPU Actually Used** | {stats.get('total_cpu_used', 0):.1f}m |\n"
            )
            f.write(
                f"| **CPU Waste** | {stats.get('cpu_waste_total', 0):.1f}m "
                f"({cpu_waste_pct:.1f}%) |\n"
            )
            f.write(
                f"| **Avg CPU Efficiency** | "
                f"{stats.get('avg_cpu_efficiency', 0):.1f}% |\n"
            )
            f.write(
                f"| **Memory Requested** | "
                f"{stats.get('total_memory_requested', 0):.1f}MB |\n"
            )
            f.write(
                f"| **Memory Actually Used** | "
                f"{stats.get('total_memory_used', 0):.1f}MB |\n"
            )
            f.write(
                f"| **Memory Waste** | {stats.get('memory_waste_total', 0):.1f}MB "
                f"({memory_waste_pct:.1f}%) |\n"
            )
            f.write(
                f"| **Avg Memory Efficiency** | "
                f"{stats.get('avg_memory_efficiency', 0):.1f}% |\n\n"
            )

            # Category breakdown
            f.write("## 📈 Pod Categories\n\n")
            f.writelines(
                f"- **{category}**: {count} pods\n"
                for category, count in recommendations.get(
                    "category_breakdown", {}
                ).items()
            )
            f.write("\n")

            # Recommended defaults
            defaults = recommendations.get("recommended_defaults", {})
            f.write("## 💡 Recommended Default Limits\n\n")
            f.write(
                "Based on real usage patterns (P75 for requests, P90*2 for limits):\n\n"
            )
            f.write("```yaml\n")
            f.write("limits:\n")
            f.write("- default:\n")
            f.write(f'    cpu: "{defaults.get("cpu_limit", "100m")}"\n')
            f.write(f'    memory: "{defaults.get("memory_limit", "128Mi")}"\n')
            f.write("  defaultRequest:\n")
            f.write(f'    cpu: "{defaults.get("cpu_request", "50m")}"\n')
            f.write(f'    memory: "{defaults.get("memory_request", "64Mi")}"\n')
            f.write("  type: Container\n")
            f.write("```\n\n")

            # Top wasters
            f.write("## 🗑️ Top CPU Wasters\n\n")
            for waster in recommendations.get("top_wasters", [])[:5]:
                waste_pct = (
                    waster["cpu_request_waste_m"] / max(waster["cpu_request_m"], 1)
                ) * 100
                f.write(
                    f"- **{waster['pod_key']}**: Using {waster['cpu_usage_m']}m, "
                    f"requesting {waster['cpu_request_m']}m "
                    f"(wasting {waste_pct:.0f}%)\n"
                )
            f.write("\n")

            # Most efficient
            f.write("## ✅ Most Efficient Pods\n\n")
            f.writelines(
                f"- **{efficient['pod_key']}**: "
                f"{efficient['cpu_efficiency_pct']:.1f}% efficiency "
                f"({efficient['cpu_usage_m']}m used of "
                f"{efficient['cpu_request_m']}m requested)\n"
                for efficient in recommendations.get("most_efficient", [])[:5]
            )

        print("📊 Analysis saved:")
        print(f"  → {csv_file}")
        print(f"  → {rec_file}")

    def print_summary(self, recommendations: dict[str, Any]) -> None:
        """Print summary to console"""
        print("\n" + "=" * 60)
        print("📊 REAL USAGE ANALYSIS SUMMARY")
        print("=" * 60)

        stats = recommendations.get("stats", {})
        cpu_waste_pct = (
            stats.get("cpu_waste_total", 0)
            / max(stats.get("total_cpu_requested", 1), 1)
            * 100
        )

        print("\n🎯 Cluster Efficiency:")
        print(f"  • CPU: {stats.get('avg_cpu_efficiency', 0):.1f}% efficient")
        print(f"  • Memory: {stats.get('avg_memory_efficiency', 0):.1f}% efficient")
        print(
            f"  • CPU waste: {stats.get('cpu_waste_total', 0):.1f}m "
            f"({cpu_waste_pct:.1f}%)"
        )

        print("\n💡 Recommended Defaults (based on real usage):")
        defaults = recommendations.get("recommended_defaults", {})
        print(f"  • CPU request: {defaults.get('cpu_request', '50m')}")
        print(f"  • CPU limit: {defaults.get('cpu_limit', '100m')}")
        print(f"  • Memory request: {defaults.get('memory_request', '64Mi')}")
        print(f"  • Memory limit: {defaults.get('memory_limit', '128Mi')}")

        print("\n📈 Pod Categories:")
        for category, count in recommendations.get("category_breakdown", {}).items():
            print(f"  • {category}: {count} pods")

        print("\n🚨 Action Items:")
        if stats.get("avg_cpu_efficiency", 0) < 30:
            print("  • URGENT: Very low CPU efficiency - massive over-provisioning!")
        if len(recommendations.get("top_wasters", [])) > 0:
            print("  • Review top wasters and reduce their requests")

        print("\n💾 Detailed analysis saved in reports/ directory")

    def run_analysis(self) -> None:
        """Run complete real usage analysis"""
        print("🚀 Starting Real Resource Usage Analysis")
        print("=" * 50)

        # Get pod specifications
        pod_specs = self.get_pod_specs()

        if not pod_specs:
            print("❌ No pod data found")
            return

        # Get real usage metrics
        real_usage = self.get_real_usage()

        if not real_usage:
            print("⚠️  No metrics data - analysis will be limited")

        # Analyze patterns
        analysis_data = self.analyze_usage_patterns(pod_specs, real_usage)

        # Generate recommendations
        recommendations = self.generate_recommendations(analysis_data)

        # Save results
        self.save_analysis(analysis_data, recommendations)

        # Print summary
        self.print_summary(recommendations)


def execute_usage_efficiency_audit(data_dir: str = "reports") -> None:
    """Execute usage efficiency audit use-case."""
    analyzer = RealUsageAnalyzer(data_dir=data_dir)
    analyzer.run_analysis()


def execute() -> None:
    """Backward-compatible alias for execute_usage_efficiency_audit."""
    execute_usage_efficiency_audit()


if __name__ == "__main__":
    execute_usage_efficiency_audit()
