# %%
# @title Imports
import os
from contextlib import suppress
from datetime import datetime

import gspread
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml
from google.auth import default
from gspread_dataframe import set_with_dataframe
from IPython.display import display
from kubernetes import client, config

try:
    from google.colab import auth, userdata
except ImportError:
    auth = None
    userdata = None


# %%
# @title Load kubeconfig
def load_kubeconfig_text():
    """Load kubeconfig from Colab Secrets or local env for non-Colab runs."""
    if userdata is not None:
        try:
            return userdata.get("KUBECONFIG")
        except Exception:
            pass

    raw_config = os.environ.get("KUBECONFIG", "").strip()
    if not raw_config:
        print(
            "Warning: set Colab Secret 'KUBECONFIG' or export "
            "KUBECONFIG=~/.kube/config before running this notebook."
        )
        return None

    kubeconfig_path = os.path.expanduser(raw_config)
    if os.path.exists(kubeconfig_path):
        with open(kubeconfig_path, encoding="utf-8") as handle:
            return handle.read()

    return raw_config


def get_kubernetes_client():
    """
    Initializes a Kubernetes API client using a kubeconfig stored in
    Colab Secrets. Returns the CoreV1Api instance or None if failed.
    """
    raw_config = load_kubeconfig_text()
    if not raw_config:
        return None

    try:
        config_dict = yaml.safe_load(raw_config)

        # Initialize configuration from dictionary
        loader = config.kube_config.KubeConfigLoader(config_dict)
        k8s_config = client.Configuration()
        loader.load_and_set(k8s_config)

        # Create API client with the loaded configuration
        api_client = client.ApiClient(configuration=k8s_config)
        v1 = client.CoreV1Api(api_client)

        cluster_name = config_dict.get("current-context", "unknown")
        print(f"Connected to Cluster Context: {cluster_name}")

        return v1

    except yaml.YAMLError:
        print("Error: Failed to parse KUBECONFIG. Ensure it is a valid YAML.")
    except Exception as e:
        print(f"Kubernetes initialization failed: {e}")

    return None


# Initialize the client for further use in the notebook
v1_client = get_kubernetes_client()

# %%
# @title Data Loading

SYSTEM_NAMESPACES = {
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "rancher-operator-system",
    "fleet-default",
    "monitoring",
    "ingress-nginx",
    "longhorn-system",
    "cert-manager",
    "argocd",
    "kyverno",
    "minio",
    "gitlab-runners",
}
SYSTEM_NAMESPACE_PREFIXES = ("cattle-",)
COLORS = px.colors.qualitative.Vivid
COLOR_USED = COLORS[2]
COLOR_WASTED = COLORS[9]
SPREADSHEET_ID = "13JavgeYNFHSKqmtIyBDZvOBpWCgiwOevW9MFNp_OzsE"
WORKSHEET_DATE_FORMAT = "%Y-%m-%d"

RESOURCE_COLUMNS = [
    "namespace",
    "pod",
    "container",
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mb",
    "memory_limit_mb",
]
USAGE_COLUMNS = ["namespace", "pod", "container", "cpu_usage_m", "memory_usage_mb"]
ISSUE_COLUMNS = [
    "namespace",
    "pod",
    "container",
    "issue",
    "severity",
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mb",
    "memory_limit_mb",
]


def parse_cpu(cpu_str):
    if not cpu_str or cpu_str == "0":
        return 0.0
    cpu_str = str(cpu_str).lower()
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])
    if cpu_str.endswith("u"):
        return float(cpu_str[:-1]) / 1000
    if cpu_str.endswith("n"):
        return float(cpu_str[:-1]) / 1000000
    return float(cpu_str) * 1000


def parse_memory(mem_str):
    if not mem_str or mem_str == "0":
        return 0.0
    mem_str = str(mem_str)
    binary_map = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
    decimal_map = {"k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}
    for suffix, factor in binary_map.items():
        if mem_str.endswith(suffix):
            return (float(mem_str[:-2]) * factor) / (1024**2)
    for suffix, factor in decimal_map.items():
        if mem_str.endswith(suffix):
            return (float(mem_str[:-1]) * factor) / (1024**2)
    return float(mem_str) / (1024**2)


def normalize_namespace(ns):
    if hasattr(ns, "name"):
        return str(ns.name).strip().lower()
    return str(ns).strip().lower()


def is_system_namespace(ns):
    namespace = normalize_namespace(ns)
    return namespace in SYSTEM_NAMESPACES or namespace.startswith(
        SYSTEM_NAMESPACE_PREFIXES
    )


def filter_system_namespaces(df):
    if df.empty or "namespace" not in df.columns:
        return df.copy()

    cleaned = df.copy()
    cleaned["namespace"] = cleaned["namespace"].map(normalize_namespace)
    return cleaned[~cleaned["namespace"].map(is_system_namespace)].reset_index(
        drop=True
    )


def add_efficiency_columns(df):
    if df.empty:
        return df.copy()

    enriched = df.copy()
    cpu_efficiency = (
        enriched["cpu_usage_m"]
        .div(enriched["cpu_request_m"].where(enriched["cpu_request_m"] != 0))
        .mul(100)
    )
    memory_efficiency = (
        enriched["memory_usage_mb"]
        .div(enriched["memory_request_mb"].where(enriched["memory_request_mb"] != 0))
        .mul(100)
    )
    enriched["cpu_efficiency_pct"] = pd.Series(cpu_efficiency, dtype="Float64").fillna(
        0.0
    )
    enriched["memory_efficiency_pct"] = pd.Series(
        memory_efficiency, dtype="Float64"
    ).fillna(0.0)
    return enriched


def detect_container_issues(row):
    issues = []

    if row["cpu_request_m"] == 0 and row["cpu_limit_m"] == 0:
        issues.append("NO_CPU_RESOURCES")
    elif row["cpu_request_m"] == 0:
        issues.append("NO_CPU_REQUEST")
    elif row["cpu_limit_m"] == 0:
        issues.append("NO_CPU_LIMIT")

    if row["memory_request_mb"] == 0 and row["memory_limit_mb"] == 0:
        issues.append("NO_MEMORY_RESOURCES")
    elif row["memory_request_mb"] == 0:
        issues.append("NO_MEMORY_REQUEST")
    elif row["memory_limit_mb"] == 0:
        issues.append("NO_MEMORY_LIMIT")

    if row["cpu_request_m"] > 0 and row["cpu_limit_m"] > 0:
        cpu_ratio = row["cpu_limit_m"] / row["cpu_request_m"]
        if cpu_ratio > 10:
            issues.append(f"HIGH_CPU_RATIO_{cpu_ratio:.1f}x")

    if row["memory_request_mb"] > 0 and row["memory_limit_mb"] > 0:
        memory_ratio = row["memory_limit_mb"] / row["memory_request_mb"]
        if memory_ratio > 5:
            issues.append(f"HIGH_MEMORY_RATIO_{memory_ratio:.1f}x")

    return issues


def issue_severity(issue):
    if "NO_" in issue and "RESOURCES" in issue:
        return "CRITICAL"
    if "HIGH_" in issue and "RATIO" in issue:
        return "HIGH"
    if "NO_" in issue:
        return "MEDIUM"
    return "LOW"


def build_issues_df(df):
    if df.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)

    issue_rows = []
    for row in df[
        [
            "namespace",
            "pod",
            "container",
            "cpu_request_m",
            "cpu_limit_m",
            "memory_request_mb",
            "memory_limit_mb",
        ]
    ].itertuples(index=False):
        row_dict = row._asdict()
        for issue in detect_container_issues(row_dict):
            issue_rows.append(
                {
                    **row_dict,
                    "issue": issue,
                    "severity": issue_severity(issue),
                }
            )

    return pd.DataFrame(issue_rows, columns=ISSUE_COLUMNS)


def build_namespace_summary_df(df):
    if df.empty:
        return pd.DataFrame(
            columns=[
                "namespace",
                "pod_count",
                "cpu_requests_m",
                "memory_requests_mb",
                "critical_issues",
                "high_issues",
                "medium_issues",
            ]
        )

    namespaces_df = (
        df.groupby("namespace")
        .agg(
            pod_count=("pod", "nunique"),
            cpu_requests_m=("cpu_request_m", "sum"),
            memory_requests_mb=("memory_request_mb", "sum"),
        )
        .reset_index()
    )
    namespaces_df["critical_issues"] = 0
    namespaces_df["high_issues"] = 0
    namespaces_df["medium_issues"] = 0
    return namespaces_df


def apply_issue_summary(namespaces_df, issues_df):
    if namespaces_df.empty:
        return namespaces_df.copy()

    summary = namespaces_df.copy()
    if issues_df.empty:
        return summary

    severity_counts = (
        issues_df[issues_df["severity"].isin(["CRITICAL", "HIGH", "MEDIUM"])]
        .groupby(["namespace", "severity"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["CRITICAL", "HIGH", "MEDIUM"], fill_value=0)
        .rename(
            columns={
                "CRITICAL": "critical_issues",
                "HIGH": "high_issues",
                "MEDIUM": "medium_issues",
            }
        )
        .reset_index()
    )
    summary = summary.drop(
        columns=["critical_issues", "high_issues", "medium_issues"]
    ).merge(severity_counts, on="namespace", how="left")
    for column in ["critical_issues", "high_issues", "medium_issues"]:
        summary[column] = (
            pd.to_numeric(summary[column], errors="coerce").fillna(0).astype(int)
        )
    return summary


def build_namespace_waste_df(df, request_col, usage_col, waste_col, threshold):
    if df.empty:
        return pd.DataFrame(
            columns=["namespace", request_col, usage_col, waste_col, "waste_pct"]
        )

    grouped = df.groupby("namespace")[[request_col, waste_col]].sum().reset_index()
    grouped[usage_col] = grouped[request_col] - grouped[waste_col]
    waste_pct = grouped[waste_col] / grouped[request_col].replace(0, pd.NA) * 100
    grouped["waste_pct"] = pd.Series(waste_pct, dtype="Float64").fillna(0.0).round(1)
    return grouped[grouped[waste_col] >= threshold].sort_values(
        waste_col, ascending=True
    )


def prepare_analysis_frames(df):
    usage_df = add_efficiency_columns(filter_system_namespaces(df))
    namespaces_df = build_namespace_summary_df(usage_df)

    total_waste = (
        float(usage_df["cpu_request_waste_m"].sum()) if not usage_df.empty else 0.0
    )
    cpu_waste = build_namespace_waste_df(
        usage_df,
        request_col="cpu_request_m",
        usage_col="cpu_usage_m",
        waste_col="cpu_request_waste_m",
        threshold=100,
    )
    mem_waste = build_namespace_waste_df(
        usage_df,
        request_col="memory_request_mb",
        usage_col="memory_usage_mb",
        waste_col="memory_request_waste_mb",
        threshold=100,
    )

    return {
        "usage_df": usage_df,
        "namespaces_df": namespaces_df,
        "cpu_waste": cpu_waste,
        "mem_waste": mem_waste,
        "total_waste": total_waste,
    }


def build_resource_requests_df(v1):
    if not v1:
        return pd.DataFrame(columns=RESOURCE_COLUMNS)

    rows = []
    for pod in v1.list_pod_for_all_namespaces().items:
        namespace = normalize_namespace(pod.metadata.namespace)
        if is_system_namespace(namespace):
            continue

        for container in pod.spec.containers:
            requests = container.resources.requests or {}
            limits = container.resources.limits or {}
            rows.append(
                {
                    "namespace": namespace,
                    "pod": pod.metadata.name,
                    "container": container.name,
                    "cpu_request_m": parse_cpu(requests.get("cpu", "0")),
                    "cpu_limit_m": parse_cpu(limits.get("cpu", "0")),
                    "memory_request_mb": parse_memory(requests.get("memory", "0")),
                    "memory_limit_mb": parse_memory(limits.get("memory", "0")),
                }
            )

    return pd.DataFrame(rows, columns=RESOURCE_COLUMNS)


def build_usage_df(v1):
    if not v1:
        return pd.DataFrame(columns=USAGE_COLUMNS)

    rows = []
    try:
        custom_api = client.CustomObjectsApi(v1.api_client)
        metrics = custom_api.list_cluster_custom_object(
            "metrics.k8s.io", "v1beta1", "pods"
        )

        for item in metrics.get("items", []):
            namespace = normalize_namespace(item["metadata"]["namespace"])
            if is_system_namespace(namespace):
                continue

            for container in item.get("containers", []):
                usage = container.get("usage", {})
                rows.append(
                    {
                        "namespace": namespace,
                        "pod": item["metadata"]["name"],
                        "container": container["name"],
                        "cpu_usage_m": parse_cpu(usage.get("cpu", "0")),
                        "memory_usage_mb": parse_memory(usage.get("memory", "0")),
                    }
                )
    except Exception:
        return pd.DataFrame(columns=USAGE_COLUMNS)

    return pd.DataFrame(rows, columns=USAGE_COLUMNS)


def finalize_cluster_df(df):
    if df.empty:
        return df

    df = df.copy()
    df = filter_system_namespaces(df)
    df.fillna(0, inplace=True)
    df["cpu_request_waste_m"] = (df["cpu_request_m"] - df["cpu_usage_m"]).clip(lower=0)
    df["memory_request_waste_mb"] = (
        df["memory_request_mb"] - df["memory_usage_mb"]
    ).clip(lower=0)
    return df


def fetch_cluster_data(v1):
    resource_df = build_resource_requests_df(v1)
    usage_df = build_usage_df(v1)

    df = pd.merge(
        resource_df, usage_df, on=["namespace", "pod", "container"], how="left"
    )
    return finalize_cluster_df(df)


df = (
    fetch_cluster_data(v1_client)
    .sort_values("cpu_request_waste_m", ascending=False)
    .reset_index(drop=True)
)

analysis_frames = prepare_analysis_frames(df)
usage_df = analysis_frames["usage_df"]
namespaces_df = analysis_frames["namespaces_df"]
cpu_waste = analysis_frames["cpu_waste"]
mem_waste = analysis_frames["mem_waste"]
total_waste = analysis_frames["total_waste"]

df.head(3)

# %%
# @title Issue Analysis
issues_df = build_issues_df(df)
namespaces_df = apply_issue_summary(namespaces_df, issues_df)
issues_df.head(3)

# %%
# @title Efficiency Breakdown


def summarize_efficiency_breakdown(usage_df, total_waste):
    if usage_df.empty:
        print("No workload efficiency data available.")
        return

    efficiency = usage_df["cpu_efficiency_pct"]
    total = len(usage_df)
    categories = [
        ("Idle (<10%)", efficiency < 10),
        ("Severely over-sized", (efficiency >= 10) & (efficiency < 30)),
        ("Over-sized (30-70%)", (efficiency >= 30) & (efficiency < 70)),
    ]

    print("Over-provisioned workloads breakdown:\n")
    for label, mask in categories:
        count = int(mask.sum())
        print(f"  {label:<24} {count:4d} pods ({count / total * 100:5.1f}%)")

    wasteful = int((efficiency < 70).sum())
    print(
        f"\n→ Right-sizing these {wasteful} workloads would save "
        f"~{total_waste / 1000:.0f}k millicores"
    )


summarize_efficiency_breakdown(usage_df, total_waste)


def build_namespace_efficiency_df(namespaces_df, usage_df, efficiency_col):
    if namespaces_df.empty:
        return namespaces_df.copy()

    efficiency_by_namespace = (
        usage_df.groupby("namespace")[efficiency_col].mean().reset_index()
        if not usage_df.empty
        else pd.DataFrame(columns=["namespace", efficiency_col])
    )
    namespace_df = namespaces_df.merge(
        efficiency_by_namespace,
        on="namespace",
        how="left",
    )
    namespace_df[efficiency_col] = pd.to_numeric(
        namespace_df[efficiency_col],
        errors="coerce",
    ).fillna(0.0)
    namespace_df["total_issues"] = (
        namespace_df["critical_issues"]
        + namespace_df["high_issues"]
        + namespace_df["medium_issues"]
    )
    return namespace_df


def render_namespace_efficiency_map(df, x_col, y_col, color_col, title, labels):
    if df.empty:
        print(f"No data available for {title}.")
        return

    plot_df = df.copy()
    plot_df["bubble_size"] = plot_df["total_issues"].clip(lower=1)

    fig = px.scatter(
        plot_df,
        x=x_col,
        y=y_col,
        size="bubble_size",
        color=color_col,
        hover_name="namespace",
        hover_data={"total_issues": True, "bubble_size": False},
        color_continuous_scale="Viridis",
        title=title,
        labels=labels,
    )
    fig.update_layout(height=600)
    fig.show()


# @title CPU Efficiency Health Map

ns_cpu = build_namespace_efficiency_df(
    namespaces_df,
    usage_df,
    "cpu_efficiency_pct",
)
render_namespace_efficiency_map(
    ns_cpu,
    x_col="pod_count",
    y_col="cpu_requests_m",
    color_col="cpu_efficiency_pct",
    title="CPU: Pod Count × Request × Issues",
    labels={
        "pod_count": "Pod Count",
        "cpu_requests_m": "CPU Requested (m)",
        "cpu_efficiency_pct": "Efficiency %",
    },
)

# @title Memory Efficiency Health Map

ns_mem = build_namespace_efficiency_df(
    namespaces_df,
    usage_df,
    "memory_efficiency_pct",
)
render_namespace_efficiency_map(
    ns_mem,
    x_col="pod_count",
    y_col="memory_requests_mb",
    color_col="memory_efficiency_pct",
    title="Memory: Pod Count × Request × Issues",
    labels={
        "pod_count": "Pod Count",
        "memory_requests_mb": "Memory Requested (MB)",
        "memory_efficiency_pct": "Efficiency %",
    },
)


# @title Chart Helpers
def render_namespace_waste_chart(df, usage_col, waste_col, title, xaxis_title, unit):
    if df.empty:
        print(f"No data available for {title}.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=df["namespace"],
            x=-df[usage_col],
            orientation="h",
            name="Used",
            marker_color=COLOR_USED,
            hovertemplate=f"<b>%{{y}}</b><br>Used: %{{x:.0f}}{unit}<extra></extra>",
            showlegend=True,
        )
    )
    fig.add_trace(
        go.Bar(
            y=df["namespace"],
            x=df[waste_col],
            orientation="h",
            name="Wasted",
            marker_color=COLOR_WASTED,
            hovertemplate=f"<b>%{{y}}</b><br>Wasted: %{{x:.0f}}{unit}<extra></extra>",
            showlegend=True,
        )
    )
    fig.update_layout(
        barmode="relative",
        title=title,
        xaxis_title=xaxis_title,
        yaxis_title="",
        height=max(400, len(df) * 20),
        hovermode="closest",
        plot_bgcolor="#f9fafb",
        paper_bgcolor="white",
        xaxis={"zeroline": True, "zerolinewidth": 2, "zerolinecolor": "#ccc"},
        margin={"l": 150},
    )
    fig.show()


def build_issue_sankey_df(namespaces_df, top_n=12):
    if namespaces_df.empty:
        return pd.DataFrame(columns=["source", "target", "value", "severity"])

    ns_issues = namespaces_df.copy()
    ns_issues["total_issues"] = (
        ns_issues["critical_issues"]
        + ns_issues["high_issues"]
        + ns_issues["medium_issues"]
    )
    top_namespaces = ns_issues.nlargest(top_n, "total_issues")["namespace"].tolist()

    sankey_rows = []
    for _, row in namespaces_df[
        namespaces_df["namespace"].isin(top_namespaces)
    ].iterrows():
        for severity, label, column in [
            ("critical", "Critical", "critical_issues"),
            ("high", "High", "high_issues"),
            ("medium", "Medium", "medium_issues"),
        ]:
            if row[column] > 0:
                sankey_rows.append(
                    {
                        "source": label,
                        "target": row["namespace"],
                        "value": int(row[column]),
                        "severity": severity,
                    }
                )

    return pd.DataFrame(sankey_rows)


def render_issue_sankey(namespaces_df, top_n=12):
    sankey_df = build_issue_sankey_df(namespaces_df, top_n=top_n)
    if sankey_df.empty:
        print("No issue data available for Sankey chart.")
        return

    sources = ["Critical", "High", "Medium"]
    targets = sorted(sankey_df["target"].unique().tolist())
    all_nodes = sources + targets
    source_idx = [all_nodes.index(source) for source in sankey_df["source"]]
    target_idx = [all_nodes.index(target) for target in sankey_df["target"]]

    critical_color = "rgb(237, 100, 90)"
    high_color = "rgb(204, 97, 176)"
    medium_color = "rgb(218, 165, 27)"
    ns_color = "rgb(82, 188, 163)"

    node_colors = {
        "Critical": critical_color,
        "High": high_color,
        "Medium": medium_color,
    }
    node_color_list = [
        node_colors.get(node, ns_color) if node in sources else ns_color
        for node in all_nodes
    ]
    link_color_map = {
        "critical": "rgba(237, 100, 90, 0.6)",
        "high": "rgba(204, 97, 176, 0.6)",
        "medium": "rgba(218, 165, 27, 0.6)",
    }
    link_colors = [
        link_color_map.get(severity, "rgba(200,200,200,0.3)")
        for severity in sankey_df["severity"]
    ]

    fig = go.Figure(
        data=[
            go.Sankey(
                node={
                    "pad": 20,
                    "thickness": 20,
                    "line": {"color": "black", "width": 0.5},
                    "color": node_color_list,
                    "label": all_nodes,
                },
                link={
                    "source": source_idx,
                    "target": target_idx,
                    "value": sankey_df["value"],
                    "color": link_colors,
                },
            )
        ]
    )
    fig.update_layout(
        title="Issues → Affected Namespaces (Top 12)",
        height=700,
        font={"size": 16},
        margin={"r": 250},
    )
    fig.show()


def build_top_waste_table(usage_df, top_n=15):
    if usage_df.empty:
        return pd.DataFrame(), 0.0

    agg = (
        usage_df.groupby("namespace")
        .agg(
            {
                "cpu_request_m": "sum",
                "cpu_usage_m": "sum",
                "cpu_request_waste_m": "sum",
                "memory_request_mb": "sum",
                "memory_usage_mb": "sum",
                "memory_request_waste_mb": "sum",
            }
        )
        .reset_index()
    )
    agg["cpu_waste_pct"] = (
        agg["cpu_request_waste_m"] / agg["cpu_request_m"] * 100
    ).round(1)
    agg["mem_waste_pct"] = (
        agg["memory_request_waste_mb"] / agg["memory_request_mb"] * 100
    ).round(1)

    top_waste = (
        agg.sort_values("cpu_request_waste_m", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    display_df = top_waste[
        [
            "namespace",
            "cpu_request_m",
            "cpu_usage_m",
            "cpu_request_waste_m",
            "cpu_waste_pct",
            "memory_request_mb",
            "memory_usage_mb",
            "memory_request_waste_mb",
            "mem_waste_pct",
        ]
    ].copy()
    display_df.columns = [
        "Namespace",
        "CPU Req (m)",
        "CPU Used (m)",
        "CPU Wasted (m)",
        "CPU Waste %",
        "Mem Req (Mi)",
        "Mem Used (Mi)",
        "Mem Wasted (Mi)",
        "Mem Waste %",
    ]
    for column in display_df.columns[1:]:
        display_df[column] = display_df[column].astype(float).round(1)

    cpu_top = float(display_df["CPU Wasted (m)"].head(5).sum())
    return display_df, cpu_top


def build_namespace_contributors_table(namespaces_df, usage_df, top_n=15):
    if namespaces_df.empty or usage_df.empty:
        return pd.DataFrame()

    efficiency_df = (
        usage_df.groupby("namespace")
        .agg(
            {
                "cpu_efficiency_pct": "mean",
                "memory_efficiency_pct": "mean",
                "cpu_request_waste_m": "sum",
                "memory_request_waste_mb": "sum",
            }
        )
        .reset_index()
    )
    contributors_df = namespaces_df.merge(
        efficiency_df,
        on="namespace",
        how="left",
    )
    total_cpu_waste = contributors_df["cpu_request_waste_m"].sum()
    total_memory_waste = contributors_df["memory_request_waste_mb"].sum()
    contributors_df["cpu_waste_share_pct"] = (
        contributors_df["cpu_request_waste_m"] / total_cpu_waste * 100
        if total_cpu_waste
        else 0
    )
    contributors_df["memory_waste_share_pct"] = (
        contributors_df["memory_request_waste_mb"] / total_memory_waste * 100
        if total_memory_waste
        else 0
    )

    display_df = (
        contributors_df.sort_values("cpu_request_waste_m", ascending=False)
        .head(top_n)
        .loc[
            :,
            [
                "namespace",
                "pod_count",
                "cpu_request_waste_m",
                "cpu_waste_share_pct",
                "cpu_efficiency_pct",
                "memory_request_waste_mb",
                "memory_waste_share_pct",
                "memory_efficiency_pct",
                "critical_issues",
                "high_issues",
                "medium_issues",
            ],
        ]
        .copy()
    )
    display_df.columns = [
        "Namespace",
        "Pods",
        "CPU Wasted (m)",
        "CPU Waste Share %",
        "CPU Efficiency %",
        "Mem Wasted (Mi)",
        "Mem Waste Share %",
        "Mem Efficiency %",
        "Critical",
        "High",
        "Medium",
    ]

    for column in [
        "CPU Wasted (m)",
        "CPU Waste Share %",
        "CPU Efficiency %",
        "Mem Wasted (Mi)",
        "Mem Waste Share %",
        "Mem Efficiency %",
    ]:
        display_df[column] = display_df[column].astype(float).round(1)

    return display_df


def build_top_waste_namespaces_export_df(namespaces_df, usage_df, top_n=15):
    contributors_df = build_namespace_contributors_table(
        namespaces_df,
        usage_df,
        top_n=top_n,
    )
    if contributors_df.empty:
        return contributors_df

    export_df = contributors_df.rename(
        columns={
            "Namespace": "namespace",
            "Pods": "pod_count",
            "CPU Wasted (m)": "cpu_request_waste_m",
            "CPU Waste Share %": "cpu_waste_share_pct",
            "CPU Efficiency %": "cpu_efficiency_pct",
            "Mem Wasted (Mi)": "memory_request_waste_mb",
            "Mem Waste Share %": "memory_waste_share_pct",
            "Mem Efficiency %": "memory_efficiency_pct",
            "Critical": "critical_issues",
            "High": "high_issues",
            "Medium": "medium_issues",
        }
    ).copy()
    export_df.insert(
        0,
        "snapshot_date",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return export_df


def build_snapshot_worksheet_name(df, date_format=WORKSHEET_DATE_FORMAT):
    if df.empty or "snapshot_date" not in df.columns:
        return datetime.now().strftime(date_format)

    snapshot_value = str(df["snapshot_date"].iloc[0])
    return pd.to_datetime(snapshot_value).strftime(date_format)


def get_google_sheet(spreadsheet_id):
    with suppress(AttributeError):
        auth.authenticate_user()

    creds, _ = default()
    gc = gspread.authorize(creds)
    return gc.open_by_key(spreadsheet_id)


def write_df_to_worksheet(df, spreadsheet_id, worksheet_name):
    if df.empty:
        print(f"Skipped {worksheet_name}: DataFrame is empty.")
        return

    spreadsheet = get_google_sheet(spreadsheet_id)
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
        worksheet.clear()
    except Exception:
        worksheet = spreadsheet.add_worksheet(
            title=worksheet_name,
            rows=max(len(df) + 10, 100),
            cols=max(len(df.columns) + 10, 20),
        )

    set_with_dataframe(worksheet, df)
    print(f"Wrote {len(df)} rows to {spreadsheet_id}/{worksheet_name}")


# %%
# @title CPU Waste by Namespace (Divergent)
render_namespace_waste_chart(
    cpu_waste,
    usage_col="cpu_usage_m",
    waste_col="cpu_request_waste_m",
    title="CPU Waste by Namespace",
    xaxis_title="Millicores",
    unit="m",
)

# %%
# @title Memory Waste by Namespace (Divergent)
render_namespace_waste_chart(
    mem_waste,
    usage_col="memory_usage_mb",
    waste_col="memory_request_waste_mb",
    title="Memory Waste by Namespace",
    xaxis_title="Mebibytes",
    unit="MB",
)

# %%
# @title
render_issue_sankey(namespaces_df, top_n=12)

# %%
# @title
display_df, cpu_top5 = build_top_waste_table(usage_df, top_n=15)
if display_df.empty:
    print("No waste table data available.")
else:
    display(display_df)
    print(f"→ Top 5 optimization: {cpu_top5:.0f}m CPU")

# %%
# @title Top Waste Contributors by Namespace
contributors_df = build_namespace_contributors_table(
    namespaces_df,
    usage_df,
    top_n=15,
)
if contributors_df.empty:
    print("No namespace contributor data available.")
else:
    display(contributors_df)

# %%
# @title Save Top Waste Namespaces
top_waste_namespaces_df = build_top_waste_namespaces_export_df(
    namespaces_df,
    usage_df,
    top_n=15,
)
if top_waste_namespaces_df.empty:
    print("No top waste namespaces data available.")
else:
    display(top_waste_namespaces_df)
    snapshot_worksheet = build_snapshot_worksheet_name(top_waste_namespaces_df)
    write_df_to_worksheet(
        top_waste_namespaces_df,
        SPREADSHEET_ID,
        snapshot_worksheet,
    )
