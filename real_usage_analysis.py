#!/usr/bin/env python3
"""
Real Kubernetes Resource Usage Analysis
Compares actual usage vs requests/limits to find realistic values
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


class RealUsageAnalyzer:
    def __init__(self, data_dir: str = "reports"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now()

    def run_kubectl(self, command: str) -> dict[str, Any]:
        """Execute kubectl command and return JSON output"""
        try:
            cmd = f"kubectl {command} -o json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"❌ Error running kubectl: {e}")
            return {}

    def parse_cpu(self, cpu_str: str) -> float:
        """Parse CPU string to millicores"""
        if not cpu_str or cpu_str == '0':
            return 0.0

        cpu_str = str(cpu_str).lower().strip()
        if cpu_str.endswith('m'):
            return float(cpu_str[:-1])
        if cpu_str.endswith('u'):
            return float(cpu_str[:-1]) / 1000
        if cpu_str.endswith('n'):
            return float(cpu_str[:-1]) / 1000000
        return float(cpu_str) * 1000

    def parse_memory(self, memory_str: str) -> int:
        """Parse memory string to bytes"""
        if not memory_str or memory_str == '0':
            return 0

        memory_str = str(memory_str).upper().strip()
        multipliers = {
            'KI': 1024, 'K': 1000,
            'MI': 1024**2, 'M': 1000**2,
            'GI': 1024**3, 'G': 1000**3,
            'TI': 1024**4, 'T': 1000**4
        }

        for suffix, multiplier in multipliers.items():
            if memory_str.endswith(suffix):
                return int(float(memory_str[:-len(suffix)]) * multiplier)

        return int(memory_str) if memory_str.isdigit() else 0

    def get_pod_specs(self) -> dict[str, dict[str, Any]]:
        """Get resource specs for all running pods"""
        print("📋 Getting pod resource specifications...")

        pods_data = self.run_kubectl("get pods -A --field-selector=status.phase=Running")
        pod_specs = {}

        for pod in pods_data.get('items', []):
            pod_key = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"

            containers = []
            for container in pod['spec']['containers']:
                resources = container.get('resources', {})
                requests = resources.get('requests', {})
                limits = resources.get('limits', {})

                containers.append({
                    'name': container['name'],
                    'cpu_request': self.parse_cpu(requests.get('cpu', '0')),
                    'cpu_limit': self.parse_cpu(limits.get('cpu', '0')),
                    'memory_request': self.parse_memory(requests.get('memory', '0Ki')),
                    'memory_limit': self.parse_memory(limits.get('memory', '0Ki'))
                })

            pod_specs[pod_key] = {
                'namespace': pod['metadata']['namespace'],
                'node': pod['spec'].get('nodeName', 'Unknown'),
                'containers': containers
            }

        return pod_specs

    def get_real_usage(self) -> dict[str, dict[str, float]]:
        """Get actual resource usage from kubectl top"""
        print("📊 Getting real resource usage...")

        # Get pod metrics
        try:
            result = subprocess.run(
                "kubectl top pods -A --no-headers",
                shell=True, capture_output=True, text=True, check=True
            )

            real_usage = {}
            for line in result.stdout.strip().split('\n'):
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
                        'cpu_usage': self.parse_cpu(cpu_usage),
                        'memory_usage': self.parse_memory(memory_usage)
                    }

            return real_usage

        except subprocess.CalledProcessError as e:
            print(f"⚠️  Could not get metrics: {e}")
            print("   Make sure metrics-server is running in the cluster")
            return {}

    def analyze_usage_patterns(self, pod_specs: dict[str, dict[str, Any]], real_usage: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
        """Analyze usage patterns and waste"""
        print("🔍 Analyzing usage patterns...")

        analysis_data = []

        for pod_key, spec in pod_specs.items():
            usage = real_usage.get(pod_key, {'cpu_usage': 0, 'memory_usage': 0})

            # Calculate totals for the pod
            total_cpu_request = sum(c['cpu_request'] for c in spec['containers'])
            total_cpu_limit = sum(c['cpu_limit'] for c in spec['containers'])
            total_memory_request = sum(c['memory_request'] for c in spec['containers'])
            total_memory_limit = sum(c['memory_limit'] for c in spec['containers'])

            cpu_usage = usage['cpu_usage']
            memory_usage = usage['memory_usage']

            # Calculate waste and efficiency
            cpu_request_waste = total_cpu_request - cpu_usage if total_cpu_request > 0 else 0
            cpu_limit_waste = total_cpu_limit - cpu_usage if total_cpu_limit > 0 else 0
            memory_request_waste = total_memory_request - memory_usage if total_memory_request > 0 else 0
            memory_limit_waste = total_memory_limit - memory_usage if total_memory_limit > 0 else 0

            # Efficiency percentages
            cpu_efficiency = (cpu_usage / total_cpu_request * 100) if total_cpu_request > 0 else 0
            memory_efficiency = (memory_usage / total_memory_request * 100) if total_memory_request > 0 else 0

            # Determine categories
            category = "Unknown"
            if cpu_usage == 0:
                category = "Idle"
            elif cpu_efficiency > 80:
                category = "Efficient"
            elif cpu_efficiency > 50:
                category = "Moderate"
            elif cpu_efficiency > 20:
                category = "Wasteful"
            else:
                category = "Very Wasteful"

            analysis_data.append({
                'pod_key': pod_key,
                'namespace': spec['namespace'],
                'node': spec['node'],
                'container_count': len(spec['containers']),
                'cpu_usage_m': round(cpu_usage, 1),
                'cpu_request_m': round(total_cpu_request, 1),
                'cpu_limit_m': round(total_cpu_limit, 1),
                'memory_usage_mb': round(memory_usage / 1024 / 1024, 1),
                'memory_request_mb': round(total_memory_request / 1024 / 1024, 1),
                'memory_limit_mb': round(total_memory_limit / 1024 / 1024, 1),
                'cpu_request_waste_m': round(cpu_request_waste, 1),
                'cpu_limit_waste_m': round(cpu_limit_waste, 1),
                'memory_request_waste_mb': round(memory_request_waste / 1024 / 1024, 1),
                'memory_limit_waste_mb': round(memory_limit_waste / 1024 / 1024, 1),
                'cpu_efficiency_pct': round(cpu_efficiency, 1),
                'memory_efficiency_pct': round(memory_efficiency, 1),
                'category': category,
                'has_limits': total_cpu_limit > 0 and total_memory_limit > 0,
                'overprovisioned': cpu_efficiency < 20 and total_cpu_request > 100  # Less than 20% efficient and requesting >100m
            })

        return analysis_data

    def generate_recommendations(self, analysis_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate resource recommendations based on real usage"""
        print("💡 Generating resource recommendations...")

        df = pd.DataFrame(analysis_data)

        if df.empty:
            return {}

        # Overall statistics
        stats = {
            'total_pods': len(df),
            'total_cpu_requested': df['cpu_request_m'].sum(),
            'total_cpu_used': df['cpu_usage_m'].sum(),
            'total_memory_requested': df['memory_request_mb'].sum(),
            'total_memory_used': df['memory_usage_mb'].sum(),
            'cpu_waste_total': df['cpu_request_waste_m'].sum(),
            'memory_waste_total': df['memory_request_waste_mb'].sum(),
            'avg_cpu_efficiency': df[df['cpu_request_m'] > 0]['cpu_efficiency_pct'].mean(),
            'avg_memory_efficiency': df[df['memory_request_mb'] > 0]['memory_efficiency_pct'].mean()
        }

        # Category breakdown
        category_stats = df['category'].value_counts().to_dict()

        # Namespace analysis
        ns_stats = df.groupby('namespace').agg({
            'cpu_usage_m': 'sum',
            'cpu_request_m': 'sum',
            'cpu_request_waste_m': 'sum',
            'memory_usage_mb': 'sum',
            'memory_request_mb': 'sum',
            'memory_request_waste_mb': 'sum'
        }).round(1)

        # Find realistic defaults based on usage patterns
        active_pods = df[df['cpu_usage_m'] > 0]
        if not active_pods.empty:
            # Calculate percentiles of actual usage
            active_pods['cpu_usage_m'].quantile(0.5)
            cpu_p75 = active_pods['cpu_usage_m'].quantile(0.75)
            cpu_p90 = active_pods['cpu_usage_m'].quantile(0.9)

            active_pods['memory_usage_mb'].quantile(0.5)
            memory_p75 = active_pods['memory_usage_mb'].quantile(0.75)
            memory_p90 = active_pods['memory_usage_mb'].quantile(0.9)

            recommended_defaults = {
                'cpu_request': f"{max(50, int(cpu_p75))}m",  # P75 but minimum 50m
                'cpu_limit': f"{max(100, int(cpu_p90 * 2))}m",  # P90 * 2 for bursts
                'memory_request': f"{max(64, int(memory_p75))}Mi",
                'memory_limit': f"{max(128, int(memory_p90 * 1.5))}Mi"
            }
        else:
            recommended_defaults = {
                'cpu_request': "50m",
                'cpu_limit': "100m",
                'memory_request': "64Mi",
                'memory_limit': "128Mi"
            }

        return {
            'stats': stats,
            'category_breakdown': category_stats,
            'namespace_stats': ns_stats,
            'recommended_defaults': recommended_defaults,
            'top_wasters': df.nlargest(10, 'cpu_request_waste_m')[['pod_key', 'cpu_usage_m', 'cpu_request_m', 'cpu_request_waste_m', 'category']].to_dict('records'),
            'most_efficient': df[df['cpu_efficiency_pct'] > 0].nlargest(10, 'cpu_efficiency_pct')[['pod_key', 'cpu_usage_m', 'cpu_request_m', 'cpu_efficiency_pct']].to_dict('records')
        }

    def save_analysis(self, analysis_data: list[dict[str, Any]], recommendations: dict[str, Any]) -> None:
        """Save analysis results"""
        timestamp_str = self.timestamp.strftime("%Y%m%d_%H%M%S")

        # Save detailed data
        df = pd.DataFrame(analysis_data)
        csv_file = self.data_dir / f"real_usage_analysis_{timestamp_str}.csv"
        df.to_csv(csv_file, index=False)

        # Save recommendations
        rec_file = self.data_dir / f"usage_recommendations_{timestamp_str}.md"

        with open(rec_file, 'w') as f:
            f.write("# Real Resource Usage Analysis\n")
            f.write(f"**Generated**: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # Overall stats
            stats = recommendations.get('stats', {})
            f.write("## 📊 Cluster Resource Efficiency\n\n")
            f.write("| Metric | Value |\n")
            f.write("|--------|-------|\n")
            f.write(f"| **Total Pods Analyzed** | {stats.get('total_pods', 0)} |\n")
            f.write(f"| **CPU Requested** | {stats.get('total_cpu_requested', 0):.1f}m |\n")
            f.write(f"| **CPU Actually Used** | {stats.get('total_cpu_used', 0):.1f}m |\n")
            f.write(f"| **CPU Waste** | {stats.get('cpu_waste_total', 0):.1f}m ({(stats.get('cpu_waste_total', 0) / max(stats.get('total_cpu_requested', 1), 1) * 100):.1f}%) |\n")
            f.write(f"| **Avg CPU Efficiency** | {stats.get('avg_cpu_efficiency', 0):.1f}% |\n")
            f.write(f"| **Memory Requested** | {stats.get('total_memory_requested', 0):.1f}MB |\n")
            f.write(f"| **Memory Actually Used** | {stats.get('total_memory_used', 0):.1f}MB |\n")
            f.write(f"| **Memory Waste** | {stats.get('memory_waste_total', 0):.1f}MB ({(stats.get('memory_waste_total', 0) / max(stats.get('total_memory_requested', 1), 1) * 100):.1f}%) |\n")
            f.write(f"| **Avg Memory Efficiency** | {stats.get('avg_memory_efficiency', 0):.1f}% |\n\n")

            # Category breakdown
            f.write("## 📈 Pod Categories\n\n")
            f.writelines(f"- **{category}**: {count} pods\n" for category, count in recommendations.get('category_breakdown', {}).items())
            f.write("\n")

            # Recommended defaults
            defaults = recommendations.get('recommended_defaults', {})
            f.write("## 💡 Recommended Default Limits\n\n")
            f.write("Based on real usage patterns (P75 for requests, P90*2 for limits):\n\n")
            f.write("```yaml\n")
            f.write("limits:\n")
            f.write("- default:\n")
            f.write(f"    cpu: \"{defaults.get('cpu_limit', '100m')}\"\n")
            f.write(f"    memory: \"{defaults.get('memory_limit', '128Mi')}\"\n")
            f.write("  defaultRequest:\n")
            f.write(f"    cpu: \"{defaults.get('cpu_request', '50m')}\"\n")
            f.write(f"    memory: \"{defaults.get('memory_request', '64Mi')}\"\n")
            f.write("  type: Container\n")
            f.write("```\n\n")

            # Top wasters
            f.write("## 🗑️ Top CPU Wasters\n\n")
            for waster in recommendations.get('top_wasters', [])[:5]:
                waste_pct = (waster['cpu_request_waste_m'] / max(waster['cpu_request_m'], 1)) * 100
                f.write(f"- **{waster['pod_key']}**: Using {waster['cpu_usage_m']}m, requesting {waster['cpu_request_m']}m (wasting {waste_pct:.0f}%)\n")
            f.write("\n")

            # Most efficient
            f.write("## ✅ Most Efficient Pods\n\n")
            f.writelines(f"- **{efficient['pod_key']}**: {efficient['cpu_efficiency_pct']:.1f}% efficiency ({efficient['cpu_usage_m']}m used of {efficient['cpu_request_m']}m requested)\n" for efficient in recommendations.get('most_efficient', [])[:5])

        print("📊 Analysis saved:")
        print(f"  → {csv_file}")
        print(f"  → {rec_file}")

    def print_summary(self, recommendations: dict[str, Any]) -> None:
        """Print summary to console"""
        print("\n" + "="*60)
        print("📊 REAL USAGE ANALYSIS SUMMARY")
        print("="*60)

        stats = recommendations.get('stats', {})

        print("\n🎯 Cluster Efficiency:")
        print(f"  • CPU: {stats.get('avg_cpu_efficiency', 0):.1f}% efficient")
        print(f"  • Memory: {stats.get('avg_memory_efficiency', 0):.1f}% efficient")
        print(f"  • CPU waste: {stats.get('cpu_waste_total', 0):.1f}m ({(stats.get('cpu_waste_total', 0) / max(stats.get('total_cpu_requested', 1), 1) * 100):.1f}%)")

        print("\n💡 Recommended Defaults (based on real usage):")
        defaults = recommendations.get('recommended_defaults', {})
        print(f"  • CPU request: {defaults.get('cpu_request', '50m')}")
        print(f"  • CPU limit: {defaults.get('cpu_limit', '100m')}")
        print(f"  • Memory request: {defaults.get('memory_request', '64Mi')}")
        print(f"  • Memory limit: {defaults.get('memory_limit', '128Mi')}")

        print("\n📈 Pod Categories:")
        for category, count in recommendations.get('category_breakdown', {}).items():
            print(f"  • {category}: {count} pods")

        print("\n🚨 Action Items:")
        if stats.get('avg_cpu_efficiency', 0) < 30:
            print("  • URGENT: Very low CPU efficiency - massive over-provisioning!")
        if len(recommendations.get('top_wasters', [])) > 0:
            print("  • Review top wasters and reduce their requests")

        print("\n💾 Detailed analysis saved in reports/ directory")

    def run_analysis(self) -> None:
        """Run complete real usage analysis"""
        print("🚀 Starting Real Resource Usage Analysis")
        print("="*50)

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

def main() -> None:
    analyzer = RealUsageAnalyzer()
    analyzer.run_analysis()

if __name__ == "__main__":
    main()
