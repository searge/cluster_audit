# Code Review — mks-audit

**Date**: 2026-02-12
**Scope**: Full codebase after refactoring from standalone scripts to `src/mks/` package
**Quality gate**: `task test -- src/` passes (pylint 10/10, mypy/pyright/ruff/ty 0 errors)

---

## Executive Summary

The project has been successfully restructured from 8 standalone root-level scripts into a layered Python package (`src/mks/`) with a unified CLI entrypoint. Old scripts are gone. The package layout, CLI design, configuration model, and shared run-artifact contract are well-executed.

Three structural problems remain that prevent the codebase from fully matching its own architecture document:

1. **deletion_investigation_service.py** reimplements both RancherClient and kubectl helpers inline (~150 lines of duplicated infrastructure)
2. **Three services** bypass `mks.infrastructure.kubectl_client` with direct `subprocess.run(shell=True)` calls
3. **Two services** call `load_dotenv()` + `os.getenv()` directly instead of using `mks.config.load_config()`

Total source: 52 files, ~7400 lines. 6 test files cover core/infrastructure/config layers.

---

## What Works Well

### Package Structure

- Clean `src/mks/` layout with explicit layers: `cli/`, `application/`, `domain/`, `infrastructure/`
- Backward-compatible shims in `core/`, `audit/`, `backends/` are thin re-exports (6 lines each)
- No old root-level scripts remain — migration is complete

### CLI (`cli/main.py`)

- Typer-based with `-h/--help` on every command
- Consistent `--report/-r` option across all 9 capabilities
- Hidden backward-compatible aliases for old command names
- Error model maps `ValueError` -> exit 1, `RuntimeError` -> exit 2
- `--version` flag reads from hardcoded string (minor, but could read from package metadata)

### Application Layer

- Consistent `*_use_case.py` / `*_service.py` pattern across all 9 capabilities
- Shared `run_writer.py` gives every capability the same manifest+summary contract
- `use_case_utils.py` eliminates duplication in stdout-mode / finalize-run flows
- `stdout_renderer.py` is sophisticated — auto-selects CSV columns by profile or ranking

### Domain Layer

- `quantity_parser.py`: handles all K8s quantity formats (m, u, n, Ki, Mi, Gi, Ti, decimal)
- `namespace_policy.py`: canonical `SYSTEM_NAMESPACES` frozenset + extensible `is_system_namespace()`
- `pod_reporting.py` and `rancher_namespace.py`: focused, pure helpers

### Infrastructure Layer

- `kubectl_client.py`: proper error wrapping via `KubectlError`, uses `shlex.split()` (not `shell=True`)
- `rancher_client.py`: async httpx client with retry/backoff, Bearer+Basic auth fallback, context manager

### Configuration

- `config.py`: frozen dataclasses (`AuditConfig`, `OvhConfig`, `RancherConfig`, `AuditLogConfig`)
- `has_ovh` / `has_rancher` / `has_audit_logs` properties for backend feature detection
- Single `load_config()` entry point reading `.env`

### Documentation

- `docs/ARCHITECTURE.md` accurately describes current structure and rules
- `docs/CODING_GUIDELINES.md` gives clear layer-aware coding direction

---

## Issues Found

### P0 — Layer Violations (Architectural)

#### 1. Embedded RancherClient in deletion_investigation_service.py

**Location**: `application/deletion_investigation_service.py:35-89`

The service reimplements a complete `RancherClient` using stdlib `urllib` — 55 lines duplicating what exists in `infrastructure/rancher_client.py` (httpx-based async client).

```
application/deletion_investigation_service.py  →  RancherClient (urllib, sync)
infrastructure/rancher_client.py               →  RancherClient (httpx, async)
```

Both implement: `get()`, `try_get()`, Bearer+Basic auth fallback on 401.

**Impact**: If auth logic changes, two places must be updated. Application code owns infrastructure concerns.

**Fix**: Either add a sync convenience wrapper to the infrastructure client, or use `asyncio.run()` in the service. The infrastructure client already has `try_get()`.

#### 2. Direct `subprocess.run(shell=True)` in three services

| File | Lines | What |
|------|-------|------|
| `deletion_investigation_service.py` | 95-117 | `kubectl_json()` and `kubectl_text()` reimplemented |
| `workload_efficiency_service.py` | 75-88 | `run_kubectl_top()` with `shell=True` |
| `usage_efficiency_service.py` | 253-285 | `get_real_usage()` with `shell=True` |

The infrastructure layer provides `kubectl_json()`, `kubectl_text()`, and `kubectl_json_or_empty()` with proper `shlex.split()` and typed `KubectlError`. Three services bypass it.

**Impact**: `shell=True` is a command injection surface. Inconsistent error handling (some return `None`, others raise `RuntimeError`).

**Fix**: Use `infrastructure.kubectl_client` functions. For `kubectl top` (which has no JSON output), add `kubectl_text()` call to the infrastructure layer — it already exists there.

#### 3. Direct `load_dotenv()` + `os.getenv()` in two services

| File | Calls |
|------|-------|
| `deletion_investigation_service.py` | `load_dotenv()` + 4 `os.getenv()` at line 628-639 |
| `rancher_users_export_service.py` | `load_dotenv()` + 4 `os.getenv()` |

The config layer (`mks.config`) already centralizes this. Two services load `.env` and read env vars independently, duplicating config key names and missing the `AuditConfig` model.

**Fix**: Accept `AuditConfig` (or its sub-configs) as a parameter from the use-case layer, where `load_config()` should be called once.

---

### P1 — Design Issues (Structural)

#### 4. Mutable dataclasses for audit models

**Location**: `application/_resource_audit_models.py`

`ContainerResources`, `PodInfo`, `NodeInfo`, `AuditSnapshot` are **mutable** `@dataclass` instead of `@dataclass(frozen=True)`. `PodDensityInfo`, `NamespaceEfficiencyInfo`, `SchedulingIssue` correctly use `NamedTuple` (immutable).

CODING_GUIDELINES says: use `@dataclass(frozen=True)` for named entities.

**Impact**: Low runtime risk (nothing mutates them), but inconsistent with the stated convention and the rest of the codebase (config.py, rancher_namespace.py all use `frozen=True`).

**Fix**: Add `frozen=True` to `ContainerResources`, `PodInfo`, `NodeInfo`, `AuditSnapshot`. Replace `list` fields with `tuple` where applicable.

#### 5. God-class pattern in services

Several services are single classes with 400-700 lines that hold instance state (`self.data_dir`, `self.timestamp`) and wrap shared helpers in instance methods:

| Class | Lines | Wrapper methods |
|-------|-------|-----------------|
| `K8sResourceAuditor` | ~555 | `_run_kubectl()`, `_parse_cpu()`, `_parse_memory()`, `_is_system_namespace()` |
| `WorkloadEfficiencyAnalyzer` | ~455 | `run_kubectl()`, `run_kubectl_top()`, `parse_cpu()`, `parse_memory()`, `is_system_namespace()` |
| `RealUsageAnalyzer` | ~580 | `run_kubectl()`, `parse_cpu()`, `parse_memory()` |
| `K8sRBACAnalyzer` | ~560 | `run_kubectl()` |

Each class wraps the same shared functions as instance methods (`self.parse_cpu()` calls `domain.parse_cpu()`). This adds no value but obscures the actual dependency.

**Impact**: Clutters the API surface. Makes it look like each class has its own parsing logic when they all delegate to the same domain function.

**Fix**: Call domain/infrastructure functions directly. Replace classes with module-level functions where the only state is `data_dir` and `timestamp` (which can be parameters).

#### 6. Hardcoded node type detection

**Location**: `resource_audit_service.py:69-75`

```python
def _get_node_type(self, node_name: str) -> str:
    if "monthly-b2-15" in node_name:
        return "monthly-b2-15"
    if "hourly-d2-8" in node_name:
        return "hourly-d2-8"
    return "unknown"
```

This is hardcoded to specific OVH flavor names. Any cluster change breaks classification.

**Fix**: Extract from node labels (`node.kubernetes.io/instance-type` or `beta.kubernetes.io/instance-type`) which K8s/OVH populates automatically.

---

### P2 — Minor Issues

#### 7. Stale maintenance section in recommendations report

**Location**: `_resource_audit_reports.py:372-383`

```python
"# Weekly audit\n",
"python3 resource_audit.py\n",
```

References the old root-level script name instead of `mks resource-audit`.

#### 8. Unused `requests` dependency

`pyproject.toml` lists `requests>=2.32.4` but no import of `requests` exists in `src/`. The project uses `httpx` (for async Rancher client) and `urllib` (for sync Rancher client). `requests` is dead weight.

#### 9. Heavy data dependencies potentially unused

`matplotlib`, `numpy`, `seaborn` are declared as dependencies but no module in `src/mks/` imports them. If the old `audit_dashboard.py` charting code was removed during migration, these dependencies should go too.

#### 10. Version hardcoded in CLI

**Location**: `cli/main.py:40`

```python
console.print("mks-audit 0.1.0")
```

Should read from `importlib.metadata.version("mks-audit")` to stay in sync with `pyproject.toml`.

#### 11. `ensure_ascii=True` in manifest JSON

**Location**: `run_writer.py:98`

`ensure_ascii=True` escapes non-ASCII characters. Since the project deals with namespace names and user identifiers that may contain non-ASCII, `ensure_ascii=False` would produce cleaner manifests.

---

## Duplication Inventory

| What | Copies | Canonical Source | Duplicates |
|------|--------|-----------------|------------|
| RancherClient | 2 | `infrastructure/rancher_client.py` | `deletion_investigation_service.py:35-89` |
| `kubectl_json()` / `kubectl_text()` | 2 | `infrastructure/kubectl_client.py` | `deletion_investigation_service.py:95-117` |
| `subprocess.run("kubectl top ...")` | 2 | (none — missing from infrastructure) | `workload_efficiency_service.py:79`, `usage_efficiency_service.py:254` |
| `load_dotenv()` + `os.getenv()` | 3 | `config.py:load_config()` | `deletion_investigation_service.py:628`, `rancher_users_export_service.py:34` |
| `self.parse_cpu()` wrappers | 3 | `domain/quantity_parser.py` | wrapped in 3 service classes |
| `self.run_kubectl()` wrappers | 4 | `infrastructure/kubectl_client.py` | wrapped in 4 service classes |

---

## Architecture Compliance Matrix

| Rule (from ARCHITECTURE.md) | Status | Notes |
|------------------------------|--------|-------|
| CLI calls application only | PASS | |
| Application calls domain + infrastructure | PARTIAL | 3 services bypass infrastructure |
| Domain is IO-free | PASS | |
| Infrastructure wraps external systems | PASS | |
| No circular imports | PASS | |
| Consistent use-case/service pattern | PASS | All 9 capabilities follow it |
| Run artifact contract (manifest + summary) | PASS | |
| Error model by layer | PARTIAL | `deletion_investigation_service` raises generic `RuntimeError` |

---

## Test Coverage Assessment

6 test files exist:

| Test | Covers |
|------|--------|
| `test_core_parsers.py` | `domain/quantity_parser.py` |
| `test_core_namespace_filter.py` | `domain/namespace_policy.py` |
| `test_core_kubectl.py` | `infrastructure/kubectl_client.py` |
| `test_config.py` | `config.py` |
| `test_backend_rancher.py` | `infrastructure/rancher_client.py` |
| `test_run_writer.py` | `application/run_writer.py` |

**Not tested**: Any service or use-case. The application layer (~5000 lines) has zero tests. This is understandable since services call `kubectl` and Rancher at runtime, but key logic (e.g. `_analyze_container()`, `_calculate_cluster_stats()`, `analyze_namespace_efficiency()`, `_categorize_cpu_efficiency()`) is testable with mock data.

---

## Dependency Analysis

```
[required]
diskcache     — used (rancher_users_export_service)
httpx         — used (infrastructure/rancher_client)
matplotlib    — NOT imported anywhere in src/
numpy         — NOT imported anywhere in src/
pandas        — used (reports, dashboard, usage analysis)
python-dotenv — used (config.py + 2 services)
requests      — NOT imported anywhere in src/
rich          — used (cli/main, stdout_renderer)
seaborn       — NOT imported anywhere in src/
typer         — used (cli/main)
```

**Recommendation**: Remove `matplotlib`, `numpy`, `seaborn`, `requests` from required dependencies. If charting is planned, move them to an optional group.

---

## Actionable Summary

### Must fix (P0)

1. **Replace embedded RancherClient** in `deletion_investigation_service.py` with infrastructure client
2. **Replace `subprocess.run(shell=True)`** in 3 services with `kubectl_client` functions
3. **Replace inline `load_dotenv()`** in 2 services with `config.load_config()`

### Should fix (P1)

4. Make audit model dataclasses `frozen=True`
2. Flatten god-class services into module-level functions (or at minimum remove wrapper methods)
3. Extract node type from labels instead of name substring matching

### Nice to fix (P2)

7. Update stale `python3 resource_audit.py` reference in recommendations
2. Remove unused `requests`, `matplotlib`, `numpy`, `seaborn` dependencies
3. Read version from package metadata instead of hardcoding
4. `ensure_ascii=False` in manifest JSON output

### Future value

- Add pytest tests for pure analysis functions in services (mock kubectl data)
- Add `kubectl_top()` helper to infrastructure layer for metrics-server queries
- Wire `AuditConfig` through use-case → service so config loading happens once at startup
- Consider adding `--json` output mode for machine consumption alongside rich stdout
