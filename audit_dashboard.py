#!/usr/bin/env python3
"""
Kubernetes Audit Dashboard
Analyzes accumulated audit data and generates actionable insights
Follows functional paradigm with dataclasses for data structures
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AuditConfig:
    """Configuration for audit dashboard analysis"""
    data_dir: Path
    cpu_waste_threshold_m: int = 100
    cpu_efficiency_low_threshold: int = 50
    idle_cpu_request_threshold_m: int = 50
    significant_waste_threshold_m: int = 500
    very_wasteful_threshold: int = 20
    wasteful_threshold: int = 50
    moderate_threshold: int = 80
    default_keep_days: int = 14
    default_top_count: int = 10
    trend_analysis_days: int = 7


@dataclass(frozen=True)
class TrendsSummary:
    """Summary of trends analysis"""
    issue_change: int
    pod_change: int
    cpu_change: float
    trend_icon: str


@dataclass(frozen=True)
class EfficiencyDistribution:
    """Distribution of CPU efficiency across pods"""
    idle: int
    very_wasteful: int
    wasteful: int
    moderate: int
    efficient: int
    total: int


@dataclass(frozen=True)
class CurrentState:
    """Current state metrics"""
    total_pods: int
    total_containers: int
    containers_with_issues: int
    issue_rate: str
    cpu_limits_ratio: str
    memory_limits_ratio: str
    critical_issues: int
    high_issues: int


def load_trends_data(config: AuditConfig) -> pd.DataFrame:
    """Load trends data from CSV file"""
    trends_file = config.data_dir / "trends.csv"
    if not trends_file.exists():
        return pd.DataFrame()

    df = pd.read_csv(trends_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df.sort_values('timestamp')


def load_latest_audit_files(config: AuditConfig) -> dict[str, pd.DataFrame]:
    """Load latest audit files by type"""
    files = {}
    patterns = {
        'pods': 'pods_detail_*.csv',
        'nodes': 'nodes_utilization_*.csv',
        'namespaces': 'namespaces_summary_*.csv'
    }

    for key, pattern in patterns.items():
        csv_files = list(config.data_dir.glob(pattern))
        if csv_files:
            latest_file = max(csv_files, key=lambda f: f.stat().st_mtime)
            files[key] = pd.read_csv(latest_file)
            files[key]['timestamp'] = pd.to_datetime(files[key]['timestamp'])

    return files


def get_latest_usage_file(config: AuditConfig, latest_usage_file: str | None = None) -> str | None:
    """Get the latest usage analysis file path"""
    if latest_usage_file:
        return latest_usage_file

    usage_files = list(config.data_dir.glob("real_usage_analysis_*.csv"))
    if not usage_files:
        return None
    return str(max(usage_files, key=lambda f: f.stat().st_mtime))


def load_usage_data(config: AuditConfig, latest_usage_file: str | None = None) -> pd.DataFrame | None:
    """Load usage analysis data"""
    usage_file_path = get_latest_usage_file(config, latest_usage_file)
    if not usage_file_path:
        return None

    try:
        return pd.read_csv(usage_file_path)
    except Exception as e:
        print(f"⚠️  Could not load usage data: {e}")
        return None


def extract_current_state(trends: pd.DataFrame) -> CurrentState:
    """Extract current state metrics from trends data"""
    current = trends.iloc[-1]
    return CurrentState(
        total_pods=current['total_pods'],
        total_containers=current['total_containers'],
        containers_with_issues=current['containers_with_issues'],
        issue_rate=current['issue_rate'],
        cpu_limits_ratio=current['cpu_limits_ratio'],
        memory_limits_ratio=current['memory_limits_ratio'],
        critical_issues=current['critical_issues'],
        high_issues=current['high_issues']
    )


def calculate_trends_summary(trends: pd.DataFrame, config: AuditConfig) -> TrendsSummary | None:
    """Calculate trends summary for the specified period"""
    if len(trends) < config.trend_analysis_days:
        return None

    recent = trends.tail(config.trend_analysis_days)

    issue_start = recent.iloc[0]['containers_with_issues']
    issue_end = recent.iloc[-1]['containers_with_issues']
    issue_change = issue_end - issue_start
    trend_icon = "📈" if issue_change > 0 else "📉" if issue_change < 0 else "➡️"

    pod_start = recent.iloc[0]['total_pods']
    pod_end = recent.iloc[-1]['total_pods']
    pod_change = pod_end - pod_start

    cpu_start = float(recent.iloc[0]['cpu_limits_ratio'].rstrip('%'))
    cpu_end = float(recent.iloc[-1]['cpu_limits_ratio'].rstrip('%'))
    cpu_change = cpu_end - cpu_start

    return TrendsSummary(
        issue_change=issue_change,
        pod_change=pod_change,
        cpu_change=cpu_change,
        trend_icon=trend_icon
    )


def find_cpu_wasters(df: pd.DataFrame, config: AuditConfig) -> pd.DataFrame:
    """Find top CPU wasters from usage data"""
    return df[
        (df['cpu_request_waste_m'] > config.cpu_waste_threshold_m) &
        (df['cpu_efficiency_pct'] < config.cpu_efficiency_low_threshold) &
        (df['cpu_request_m'] > 0)
    ].nlargest(config.default_top_count, 'cpu_request_waste_m')


def find_idle_pods(df: pd.DataFrame, config: AuditConfig) -> pd.DataFrame:
    """Find completely idle pods"""
    return df[
        (df['cpu_usage_m'] == 0) &
        (df['cpu_request_m'] > config.idle_cpu_request_threshold_m)
    ].nlargest(config.default_top_count, 'cpu_request_m')


def calculate_efficiency_distribution(df: pd.DataFrame, config: AuditConfig) -> EfficiencyDistribution | None:
    """Calculate CPU efficiency distribution"""
    active_df = df[df['cpu_request_m'] > 0]
    if active_df.empty:
        return None

    idle = len(active_df[active_df['cpu_efficiency_pct'] == 0])
    very_wasteful = len(active_df[active_df['cpu_efficiency_pct'] < config.very_wasteful_threshold])
    wasteful = len(active_df[
        (active_df['cpu_efficiency_pct'] >= config.very_wasteful_threshold) &
        (active_df['cpu_efficiency_pct'] < config.wasteful_threshold)
    ])
    moderate = len(active_df[
        (active_df['cpu_efficiency_pct'] >= config.wasteful_threshold) &
        (active_df['cpu_efficiency_pct'] < config.moderate_threshold)
    ])
    efficient = len(active_df[active_df['cpu_efficiency_pct'] >= config.moderate_threshold])

    return EfficiencyDistribution(
        idle=idle,
        very_wasteful=very_wasteful,
        wasteful=wasteful,
        moderate=moderate,
        efficient=efficient,
        total=len(active_df)
    )


def calculate_namespace_waste(df: pd.DataFrame, config: AuditConfig) -> pd.DataFrame:
    """Calculate waste by namespace"""
    ns_waste = df.groupby('namespace').agg({
        'cpu_request_waste_m': 'sum',
        'memory_request_waste_mb': 'sum',
        'cpu_request_m': 'sum',
        'cpu_usage_m': 'sum'
    }).round(1)

    ns_waste['efficiency_pct'] = (
        ns_waste['cpu_usage_m'] / ns_waste['cpu_request_m'] * 100
    ).fillna(0).round(1)

    return ns_waste[ns_waste['cpu_request_waste_m'] > config.significant_waste_threshold_m].nlargest(
        config.default_top_count, 'cpu_request_waste_m'
    )


def print_header() -> None:
    """Print dashboard header"""
    print("📊 Kubernetes Audit Dashboard")
    print("=" * 50)


def print_current_state(state: CurrentState, trends: pd.DataFrame) -> None:
    """Print current state information"""
    print(f"📅 Data period: {trends['timestamp'].min()} to {trends['timestamp'].max()}")
    print(f"📈 Total audits: {len(trends)}")
    print()

    print("🔍 Current State:")
    print(f"  • Pods: {state.total_pods}")
    print(f"  • Containers: {state.total_containers}")
    print(f"  • Issues: {state.containers_with_issues} ({state.issue_rate})")
    print(f"  • CPU overcommit: {state.cpu_limits_ratio}")
    print(f"  • Memory overcommit: {state.memory_limits_ratio}")
    print()


def print_trends_analysis(trends_summary: TrendsSummary) -> None:
    """Print trends analysis"""
    print("📈 Trends Analysis (Last 7 days):")
    print(f"  • Issues trend: {trends_summary.trend_icon} {trends_summary.issue_change:+d} containers")
    print(f"  • Pod growth: {trends_summary.pod_change:+d} pods")
    print(f"  • CPU overcommit: {trends_summary.cpu_change:+.1f}% change")
    print()


def print_top_problems(df: pd.DataFrame, config: AuditConfig) -> None:
    """Print most problematic namespaces"""
    print("🚨 Top 10 Problem Namespaces:")

    problematic = df[df['issues_count'] > 0].nlargest(
        config.default_top_count, ['issues_count', 'cpu_limits_m']
    )

    if problematic.empty:
        print("  🎉 No issues found!")
        return

    print(f"{'Namespace':<30} {'Issues':<8} {'Priority':<10} {'CPU(m)':<10} {'Health':<8}")
    print("-" * 75)

    for _, row in problematic.iterrows():
        print(f"{row['namespace']:<30} {row['issues_count']:<8} {row['priority']:<10} "
              f"{row['cpu_limits_m']:<10} {row['health_score']:<8}")
    print()


def print_cpu_wasters(wasters: pd.DataFrame) -> None:
    """Print top CPU wasters"""
    if not wasters.empty:
        print("🗑️ TOP CPU WASTERS:")
        for _, row in wasters.iterrows():
            print(f"  {row['pod_key']}: {row['cpu_efficiency_pct']:.1f}% efficient, "
                  f"wasting {row['cpu_request_waste_m']:.0f}m")
        print()


def print_idle_pods(idle_pods: pd.DataFrame) -> None:
    """Print completely idle pods"""
    if not idle_pods.empty:
        print("😴 COMPLETELY IDLE PODS:")
        for _, row in idle_pods.iterrows():
            print(f"  {row['pod_key']}: 0m used, {row['cpu_request_m']:.0f}m requested")
        print()


def print_efficiency_distribution(distribution: EfficiencyDistribution) -> None:
    """Print CPU efficiency distribution"""
    print("📊 CPU EFFICIENCY DISTRIBUTION:")
    print(f"  • Idle (0%): {distribution.idle} pods ({distribution.idle/distribution.total*100:.1f}%)")
    print(f"  • Very Wasteful (<20%): {distribution.very_wasteful} pods "
          f"({distribution.very_wasteful/distribution.total*100:.1f}%)")
    print(f"  • Wasteful (20-50%): {distribution.wasteful} pods "
          f"({distribution.wasteful/distribution.total*100:.1f}%)")
    print(f"  • Moderate (50-80%): {distribution.moderate} pods "
          f"({distribution.moderate/distribution.total*100:.1f}%)")
    print(f"  • Efficient (>80%): {distribution.efficient} pods "
          f"({distribution.efficient/distribution.total*100:.1f}%)")
    print()


def print_namespace_waste(significant_waste: pd.DataFrame) -> None:
    """Print namespace waste analysis"""
    if not significant_waste.empty:
        print("🏢 NAMESPACE WASTE ANALYSIS:")
        print(f"{'Namespace':<30} {'CPU Waste(m)':<12} {'Efficiency':<12} {'Memory Waste(MB)':<15}")
        print("-" * 75)
        for ns, row in significant_waste.iterrows():
            print(f"{ns:<30} {row['cpu_request_waste_m']:<12} {row['efficiency_pct']:.1f}%{'':<8} "
                  f"{row['memory_request_waste_mb']:<15}")
        print()


def print_resource_hogs(df: pd.DataFrame, config: AuditConfig) -> None:
    """Print biggest resource consumers"""
    print("🐘 Top 10 Resource Consumers:")

    top_consumers = df.nlargest(config.default_top_count, 'cpu_limits_m')

    print(f"{'Namespace':<30} {'CPU Limits(m)':<15} {'Memory(MB)':<12} {'Pods':<6}")
    print("-" * 70)

    for _, row in top_consumers.iterrows():
        print(f"{row['namespace']:<30} {row['cpu_limits_m']:<15} "
              f"{row['memory_limits_mb']:<12} {row['pod_count']:<6}")
    print()


def print_nodes_analysis(df: pd.DataFrame) -> None:
    """Print node utilization analysis"""
    print("🖥️  Node Utilization:")

    node_summary = df.groupby('node_type').agg({
        'pod_count': 'sum',
        'cpu_requests_m': 'sum',
        'cpu_limits_m': 'sum',
        'issues_count': 'sum'
    }).reset_index()

    print(f"{'Node Type':<20} {'Pods':<6} {'CPU Req(m)':<12} {'CPU Lim(m)':<12} {'Issues':<8}")
    print("-" * 65)

    for _, row in node_summary.iterrows():
        print(f"{row['node_type']:<20} {row['pod_count']:<6} {row['cpu_requests_m']:<12} "
              f"{row['cpu_limits_m']:<12} {row['issues_count']:<8}")
    print()


def print_monitoring_summary(state: CurrentState, trends: pd.DataFrame, config: AuditConfig) -> None:
    """Print monitoring summary without recommendations"""
    print("📊 MONITORING SUMMARY:")
    print()

    print("📈 Current Metrics:")
    print(f"  • Critical issues: {state.critical_issues} containers")
    print(f"  • High priority issues: {state.high_issues} containers")
    print(f"  • CPU overcommit: {state.cpu_limits_ratio}")
    print(f"  • Memory overcommit: {state.memory_limits_ratio}")
    print()

    if len(trends) >= config.trend_analysis_days:
        recent = trends.tail(config.trend_analysis_days)
        issue_change = recent.iloc[-1]['containers_with_issues'] - recent.iloc[0]['containers_with_issues']
        trend_icon = "📈" if issue_change > 0 else "📉" if issue_change < 0 else "➡️"
        print(f"📊 7-Day Trend: {trend_icon} {issue_change:+d} containers with issues")
        print()

    print("💾 Generated Reports:")
    print(f"  • Trends: {config.data_dir}/trends.csv")
    for pattern in ["*_summary_*.csv", "*_utilization_*.csv", "*_detail_*.csv"]:
        files = list(config.data_dir.glob(pattern))
        if files:
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            print(f"  • Latest {pattern.split('_')[1]}: {latest_file.name}")
    print()


def cleanup_old_files(config: AuditConfig) -> int:
    """Clean up old audit files"""
    now = datetime.now()
    cutoff = now - timedelta(days=config.default_keep_days)
    deleted_count = 0

    for file_path in config.data_dir.glob("*_2*.csv"):
        try:
            timestamp_str = file_path.stem.split('_')[-2] + '_' + file_path.stem.split('_')[-1]
            file_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")

            if file_time < cutoff:
                file_path.unlink()
                deleted_count += 1
        except (ValueError, IndexError):
            continue

    if deleted_count > 0:
        print(f"🗑️  Cleaned up {deleted_count} old files (older than {config.default_keep_days} days)")

    return deleted_count


def generate_summary_report(config: AuditConfig) -> None:
    """Generate comprehensive summary report"""
    print_header()

    trends = load_trends_data(config)
    latest = load_latest_audit_files(config)

    if trends.empty:
        print("❌ No trends data found. Run audit first.")
        return

    current_state = extract_current_state(trends)
    print_current_state(current_state, trends)

    # Trends analysis
    trends_summary = calculate_trends_summary(trends, config)
    if trends_summary:
        print_trends_analysis(trends_summary)

    # Top problems
    if 'namespaces' in latest:
        print_top_problems(latest['namespaces'], config)

    # Usage analysis
    usage_df = load_usage_data(config)
    if usage_df is not None:
        # CPU Wasters Analysis
        cpu_wasters = find_cpu_wasters(usage_df, config)
        print_cpu_wasters(cpu_wasters)

        # Idle pods analysis
        idle_pods = find_idle_pods(usage_df, config)
        print_idle_pods(idle_pods)

        # Efficiency distribution
        efficiency_dist = calculate_efficiency_distribution(usage_df, config)
        if efficiency_dist:
            print_efficiency_distribution(efficiency_dist)

        # Namespace waste analysis
        namespace_waste = calculate_namespace_waste(usage_df, config)
        print_namespace_waste(namespace_waste)

    # Resource hogs
    if 'namespaces' in latest:
        print_resource_hogs(latest['namespaces'], config)

    # Node analysis
    if 'nodes' in latest:
        print_nodes_analysis(latest['nodes'])

    # Monitoring summary
    print_monitoring_summary(current_state, trends, config)


def main() -> None:
    """Main entry point"""
    config = AuditConfig(data_dir=Path("reports"))

    # Generate summary
    generate_summary_report(config)

    # Cleanup old files
    cleanup_old_files(config)


if __name__ == "__main__":
    main()
