# mks-audit — Agent Guide

Single source of truth for AI agents working on this codebase.
Read this file fully before making any changes.

---

## What This Project Is

`mks-audit` is a Kubernetes cluster audit toolkit for OVH MKS environments.
It collects resource metrics, RBAC data, and Rancher project mappings — then
generates CSV reports, Markdown summaries, and trend data.

The CLI entrypoint is `mks` (defined in `src/mks/cli/main.py`).
All operations are driven through `Taskfile.yaml`. Do not run Python scripts directly.

---

## Runtime

- **Python:** 3.13+ (forward-compatible with 3.14)
- **Package manager:** `uv` — use `uv run` for all Python execution
- **Task runner:** `task` — see `Taskfile.yaml` for all available tasks
- **External dependency:** `kubectl` configured with cluster access
- **Optional:** metrics-server in cluster (for `usage-efficiency` and `workload-efficiency`)

---

## Layer Architecture

Dependency direction is **inward only**. Never violate this.

```
cli → application → domain
cli → config
application → infrastructure
infrastructure → domain (shared types only)
```

### `mks.cli`

CLI commands, help text, option parsing. Maps exceptions to exit codes.
Loads config **once** via `load_config()` and passes it down.

### `mks.application`

Use-cases and orchestration. Each capability has two files:

- `*_use_case.py` — command-facing, handles run/stdout modes, calls service
- `*_service.py` — implementation as module-level functions, no class wrapping

### `mks.domain`

Pure functions and frozen dataclasses. No side effects. No IO.
No imports from `application`, `infrastructure`, or `cli`.

### `mks.infrastructure`

All external IO. Only place allowed to call `subprocess`, `httpx`, filesystem.

- `kubectl_client.py` — all kubectl calls go here
- `rancher_client.py` — async Rancher API client with retry/backoff

### Compatibility shims

`mks.audit`, `mks.backends`, `mks.core` — thin re-export shims for stable imports.
Do not add new logic there. Active implementation lives in `application/domain/infrastructure`.

---

## Hard Rules

These were each violated at least once. They are non-negotiable.

1. **No `subprocess` outside `mks.infrastructure`.**
   Every kubectl call goes through `kubectl_client`. No exceptions.

2. **No `load_dotenv()` or `os.getenv()` outside `mks.config`.**
   Config is loaded once, passed as a frozen dataclass.

3. **No `shell=True`.**
   Use `shlex.split()` or argument lists. `shell=True` is a command injection vector.

4. **All dataclasses are `frozen=True`.**
   To update a value: `dataclasses.replace(original, field=new_value)`.

5. **No duplicate infrastructure clients.**
   One `RancherClient`. One kubectl adapter.

6. **No class wrapping of shared functions.**
   Do not write `self.parse_cpu()` when `parse_cpu()` from domain is available.

7. **No `Any` return types where structure is known.**
   Raw kubectl JSON is acceptable as `dict[str, Any]`. New domain types are not.

---

## Configuration

Config is loaded once at CLI startup and passed down as frozen dataclasses.

```python
# cli/main.py
config = load_config()  # reads .env + environment

# passes only the relevant slice to the use-case
execute_rancher_users_export(namespaces, rancher_config=config.rancher, ...)
```

**Environment variables** (via `.env` or shell):

| Variable | Used by |
|---|---|
| `KUBECONFIG` | kubectl adapter (path to kubeconfig) |
| `RANCHER_URL` | Rancher client base URL |
| `RANCHER_TOKEN` | Rancher Bearer auth |
| `RANCHER_AK` / `RANCHER_SK` | Rancher Basic auth (alternative) |
| `OVH_ENDPOINT` / `OVH_APPLICATION_KEY` / `OVH_APPLICATION_SECRET` / `OVH_CONSUMER_KEY` | OVH API (optional backend) |
| `OVH_PROJECT_ID` / `OVH_KUBE_ID` | OVH cluster targeting |
| `LDP_WEBSOCKET_URL` / `LDP_TOKEN` | Audit log backend (optional) |

Services must never call `load_dotenv()` or `os.getenv()` directly.

---

## CLI Commands

All commands support `--report/-r <dir>` to persist artifacts.
Without `--report`, output is rendered to stdout via `rich` and no files are kept.

| Command | What it does |
|---|---|
| `mks resource-audit` | Snapshot resources: pods, nodes, namespaces. `--extended` adds density/scheduling reports. |
| `mks workload-efficiency` | Requested vs actual usage per workload (requires metrics-server). |
| `mks usage-efficiency` | Pod-level CPU/memory efficiency and waste analysis. |
| `mks dashboard-summary` | Aggregated summary from historical audit files. |
| `mks pod-density-summary` | Per-node pod count vs capacity. |
| `mks rbac-audit` | ClusterRoleBindings + RoleBindings analysis, project grouping. |
| `mks rancher-project-overview` | Namespace → Rancher project mapping from annotations. |
| `mks rancher-users-export --namespaces ns1,ns2` | Resolve project users via Rancher API with disk cache. |
| `mks deletion-investigation --namespaces ns1,ns2` | Correlate k8s events, deployments, Rancher tokens. |

**Deprecated aliases** (hidden, still work): `audit`, `workload`, `rancher`, `rbac`, `dashboard`, `pods`, `real-usage`, `investigate`.

---

## Capabilities Map

| Capability | Use-case | Service | Key output |
|---|---|---|---|
| resource-audit | `resource_audit_use_case.py` | `resource_audit_service.py` | `pods_detail_*.csv`, `nodes_utilization_*.csv`, `namespaces_summary_*.csv`, `trends.csv`, `recommendations_*.md` |
| workload-efficiency | `workload_efficiency_use_case.py` | `workload_efficiency_service.py` | `workload_efficiency_*.csv` |
| usage-efficiency | `usage_efficiency_use_case.py` | `usage_efficiency_service.py` | `real_usage_analysis_*.csv`, `usage_recommendations_*.md` |
| dashboard-summary | `dashboard_summary_use_case.py` | `dashboard_summary_service.py` | stdout summary + optional run dir |
| pod-density-summary | `pod_density_summary_use_case.py` | `pod_density_summary_service.py` | stdout table |
| rbac-audit | `rbac_audit_use_case.py` | `rbac_audit_service.py` | `k8s_projects_*.csv`, `k8s_user_access_*.csv`, `k8s_namespaces_*.csv` |
| rancher-project-overview | `rancher_project_overview_use_case.py` | `rancher_project_overview_service.py` | `rancher_projects_simple_*.csv`, `rancher_namespaces_simple_*.csv` |
| rancher-users-export | `rancher_users_export_use_case.py` | `rancher_users_export_service.py` | `rancher_project_users_*.csv` |
| deletion-investigation | `deletion_investigation_use_case.py` | `deletion_investigation_service.py` | `deletion_investigation_*.txt` |

---

## Run Artifact Contract

Persisted runs (`--report/-r`) write to a standard directory layout:

```
reports/<capability>/<YYYYMMDD_HHMMSS>/
  summary.md          ← human-readable key findings
  manifest.json       ← machine-readable metadata (ensure_ascii=False)
  <capability artifacts>
```

`RunResult` is returned by all use-cases in persisted mode.
`None` is returned in stdout-only mode.

Shared helpers in `mks.application.use_case_utils`:

- `render_stdout_only()` — runs and renders to stdout, no files
- `render_stdout_with_tempdir()` — runs in temp dir, renders artifacts to stdout, cleans up
- `finalize_success_run()` — writes `summary.md` + `manifest.json`, returns `RunResult`

---

## Domain Models

All frozen. All in `mks.application._resource_audit_models` or `mks.domain`.

Key types:

- `ContainerResources` — cpu/memory request/limit/issues per container
- `PodInfo` — pod with tuple of containers
- `NodeInfo` — node capacity + allocatable
- `AuditSnapshot` — point-in-time cluster state (nodes + pods + stats)
- `PodDensityInfo` — NamedTuple for node-level pod density metrics
- `WorkloadMetrics` — NamedTuple for workload-level aggregated efficiency
- `SchedulingIssue` — NamedTuple for pending/failed/over-capacity pods
- `RunResult` / `RunContext` — run lifecycle types in `run_writer.py`

---

## Error Model

| Layer | Raises |
|---|---|
| `infrastructure` | `KubectlError`, `RancherApiError` |
| `application` | `RuntimeError`, `ValueError` |
| `cli` | Maps to exit codes: `ValueError` → 1, `RuntimeError` → 2, success → 0 |

Never silently swallow errors. If returning a fallback (e.g. `{}`), do it intentionally
and only where the flow is designed to continue (see `kubectl_json_or_empty`).

---

## How to Add a New Capability

1. Create `src/mks/application/<name>_service.py` — module-level functions, no class wrapping
2. Create `src/mks/application/<name>_use_case.py` — `execute_<name>()` with stdout/report modes
3. Export from `src/mks/application/__init__.py`
4. Add CLI command to `src/mks/cli/main.py`
5. Add task to `Taskfile.yaml` if it needs a shortcut

Follow the pattern from any existing `*_use_case.py` / `*_service.py` pair exactly.

---

## Typing Rules

- All public functions have explicit type hints
- No bare `Any` where structure is known
- Use `type` aliases for domain concepts: `type Millicores = int`
- `dict[str, Any]` is acceptable for raw kubectl JSON
- Use `str | None` not `Optional[str]`
- Use built-in generics: `list[str]`, `dict[str, Any]`, `tuple[int, ...]`

---

## Quality Gate

Before finishing any change, run:

```bash
task test -- src/
```

For targeted feedback during development:

```bash
task lint
task typecheck
```

The full gate runs: `ruff format`, `ruff check --fix`, `ty check`, `pylint`, `mypy`, `pyright`.

---

## Dependencies (runtime)

| Package | Purpose |
|---|---|
| `typer` | CLI framework |
| `rich` | Console output, stdout renderer |
| `pandas` | Report generation, analytics |
| `httpx` | Async HTTP for Rancher client |
| `diskcache` | Rancher users export API cache |
| `python-dotenv` | `.env` loading in `config.py` |

Optional: `ovh` (install with `[ovh]` extra) for OVH API backend.

---

## Notebooks

`notebooks/` directory is for exploratory Jupyter analysis of report data.
Notebooks consume output from `reports/` (CSV/JSON files generated by CLI commands).
They are not part of the production code path and do not import from `mks.*` directly.
Run `task audit` or `task workload-efficiency` first to generate data, then open notebooks.

---

## What Agents Should Not Do

- Do not call `subprocess.run()` outside `mks.infrastructure`
- Do not add mutable dataclasses
- Do not add `load_dotenv()` or `os.getenv()` outside `config.py`
- Do not create new classes just to hold `self.data_dir` or pass parameters between methods
- Do not add logic to shim modules in `mks.audit`, `mks.backends`, `mks.core`
- Do not modify `reports/` directory contents — it is runtime output, not source
- Do not run scripts directly — use `task mks -- <command>` or `task <shortcut>`
