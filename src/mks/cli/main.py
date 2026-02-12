"""CLI entrypoint for mks audit tooling."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

import typer
from rich.console import Console

from mks.application import (
    execute_dashboard_summary,
    execute_deletion_investigation,
    execute_pod_density_summary,
    execute_rancher_project_overview,
    execute_rancher_users_export,
    execute_rbac_audit,
    execute_resource_audit,
    execute_usage_efficiency_audit,
    execute_workload_efficiency_audit,
)

app = typer.Typer(
    name="mks",
    help="OVH MKS audit toolkit",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


def _resolve_version() -> str:
    """Return installed package version or local fallback."""
    try:
        return package_version("mks-audit")
    except PackageNotFoundError:
        return "0.1.0"


@app.callback()
def callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
    ),
) -> None:
    """Handle global CLI options."""
    if version:
        console.print(f"mks-audit {_resolve_version()}")
        raise typer.Exit(code=0)
    if ctx.invoked_subcommand is None:
        raise typer.Exit(code=0)


def _handle_error(exc: Exception) -> None:
    """Convert domain exceptions to CLI exit codes."""
    if isinstance(exc, ValueError):
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if isinstance(exc, RuntimeError):
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    raise exc


@app.command("resource-audit")
def audit_command(
    extended: bool = typer.Option(
        False,
        "--extended",
        help="Include pod-density, namespace-efficiency and scheduling reports.",
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
) -> None:
    """Snapshot cluster resources.

    Prints rich preview by default; use `--report/-r` to persist artifacts.
    """
    try:
        mode = "extended" if extended else "current"
        run = execute_resource_audit(mode=mode, reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("workload-efficiency")
def workload_command(
    include_system: bool = typer.Option(
        False,
        "--include-system",
        help="Include kube-/rancher-/cattle- namespaces in analysis.",
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
) -> None:
    """Compare requested vs actual usage by workload.

    Prints rich preview by default; use `--report/-r` to persist artifacts.
    """
    try:
        run = execute_workload_efficiency_audit(
            include_system=include_system,
            reports_root=report,
        )
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("rancher-project-overview")
def rancher_command(
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
) -> None:
    """Analyze namespace -> Rancher project mapping."""
    try:
        run = execute_rancher_project_overview(reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("rancher-users-export")
def rancher_users_command(
    namespaces: str = typer.Option(
        ...,
        "--namespaces",
        "-n",
        help="Comma-separated namespaces to resolve against Rancher projects/users.",
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
    cache_dir: str = typer.Option(
        "cache/rancher_users",
        "--cache-dir",
        help="Disk cache directory for Rancher project/user lookups.",
    ),
    cache_ttl_seconds: int = typer.Option(
        3600,
        "--cache-ttl-seconds",
        help="Cache TTL for Rancher lookups (seconds).",
    ),
) -> None:
    """Export Rancher project users for selected namespaces.

    Prints rich preview by default; use `--report/-r` to persist artifacts.
    """
    try:
        run = execute_rancher_users_export(
            namespaces,
            reports_root=report,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
            console.print(f"[green]Manifest:[/green] {run.manifest_path}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("rbac-audit")
def rbac_command(
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
) -> None:
    """Analyze RBAC bindings and project-like namespace grouping."""
    try:
        run = execute_rbac_audit(reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("dashboard-summary")
def dashboard_command(
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. If omitted, prints stdout only."
        ),
    ),
) -> None:
    """Print current-state summary from historical audit files."""
    try:
        run = execute_dashboard_summary(reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("pod-density-summary")
def pods_command(
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. If omitted, prints stdout only."
        ),
    ),
) -> None:
    """Print per-node pod density summary."""
    try:
        run = execute_pod_density_summary(reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("usage-efficiency")
def real_usage_command(
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
) -> None:
    """Compare real pod usage vs requests/limits."""
    try:
        run = execute_usage_efficiency_audit(reports_root=report)
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


@app.command("deletion-investigation")
def investigate_command(
    namespaces: str = typer.Option(
        ...,
        "--namespaces",
        "-n",
        help="Comma-separated namespaces to investigate.",
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        "-r",
        help=(
            "Persist report files under this directory. "
            "If omitted, prints stdout preview only."
        ),
    ),
    skip_rancher: bool = typer.Option(
        False,
        "--skip-rancher",
        help="Disable Rancher API checks; use kubectl-only investigation.",
    ),
) -> None:
    """Investigate possible deletions.

    Correlates k8s events/deployments/replicasets and optional Rancher activity.
    """
    try:
        run = execute_deletion_investigation(
            namespaces,
            reports_root=report,
            skip_rancher=skip_rancher,
        )
        if run is not None:
            console.print(f"[green]Run:[/green] {run.output_dir}")
            console.print(f"[green]Manifest:[/green] {run.manifest_path}")
    except (ValueError, RuntimeError) as exc:  # pragma: no cover
        _handle_error(exc)


def main() -> None:
    """Project entrypoint for `mks` script."""
    app()


# Backward-compatible aliases during migration window.
app.command("audit", hidden=True)(audit_command)
app.command("workload", hidden=True)(workload_command)
app.command("rancher", hidden=True)(rancher_command)
app.command("rancher-users", hidden=True)(rancher_users_command)
app.command("rbac", hidden=True)(rbac_command)
app.command("dashboard", hidden=True)(dashboard_command)
app.command("pods", hidden=True)(pods_command)
app.command("real-usage", hidden=True)(real_usage_command)
app.command("investigate", hidden=True)(investigate_command)


if __name__ == "__main__":
    main()
