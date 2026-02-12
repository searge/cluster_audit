# Architecture Overview

## Purpose

This codebase is a Kubernetes audit toolkit with a single CLI entrypoint (`mks`).
It supports two execution modes for most capabilities:

- preview mode: run in a temporary directory and render results to stdout
- report mode: persist artifacts under a run directory with a manifest and summary

The architecture is designed to keep command UX stable while allowing internal refactoring with low risk.

## Target Runtime

Python 3.13+ (forward-compatible with 3.14).

Use modern stdlib features as they become available:

- `type` statement for type aliases (3.12+)
- `match`/`case` where it replaces chained `if/elif` on enums or literals
- built-in generics (`list[str]`, `dict[str, Any]`) — no `typing.List`/`typing.Dict`
- `from __future__ import annotations` only when required for forward references in older code

Keep `requires-python = ">=3.13"` in `pyproject.toml` as the minimum.

## Design Philosophy

Functional-first, data-oriented:

- prefer pure functions over classes
- use `@dataclass(frozen=True)` for structured data, not for behavior
- use classes only when managing a stateful resource (connection pool, async context manager)
- avoid inheritance; compose with functions and typed parameters

A service module should read as a pipeline of function calls, not a class hierarchy.

## Layered Structure

The project follows a layered design under `src/mks`.

### `mks.cli`

- Defines commands, options, and help text.
- Calls application use-cases only.
- Maps exceptions to exit codes.
- Loads config once via `mks.config.load_config()` and passes it down.

### `mks.application`

- Hosts use-cases and orchestration services.
- Coordinates domain rules and infrastructure adapters.
- Owns report generation flows and capability-level composition.
- Services are modules of functions, not god-classes.

### `mks.domain`

- Contains reusable business rules and value transformations.
- Includes shared namespace policy, quantity parsing, and reporting helpers.
- Stays independent from CLI and network/process IO.
- All data models live here (frozen dataclasses, NamedTuples).
- No side effects, no imports from `application` or `infrastructure`.

### `mks.infrastructure`

- Wraps external systems and side effects.
- Includes kubectl and Rancher clients with explicit error boundaries.
- Only place that calls `subprocess`, HTTP clients, or filesystem IO for external data.
- Every external command must go through an adapter here. No inline `subprocess.run()` in services.

Compatibility shims still exist in `mks.audit`, `mks.backends`, and `mks.core` for stable imports, but active implementation lives in `mks.application`, `mks.domain`, and `mks.infrastructure`.

## Dependency Direction

Allowed direction is inward:

- `cli -> application`
- `cli -> config`
- `application -> domain`
- `application -> infrastructure`
- `infrastructure -> domain` (only when shared types/helpers are needed)

Not allowed:

- `domain` importing `application`, `infrastructure`, or `cli`
- `cli` calling kubectl, HTTP clients, or filesystem-heavy logic directly
- `application` calling `subprocess`, `urllib`, or `httpx` directly
- `application` calling `load_dotenv()` or `os.getenv()` — use `mks.config`

## Hard Rules

These rules exist because each one was violated at least once. They are non-negotiable:

1. **No `subprocess` outside `mks.infrastructure`**. Every kubectl call, every shell command goes through an adapter. No exceptions.
2. **No `load_dotenv()` or `os.getenv()` outside `mks.config`**. Config is loaded once, passed as a frozen dataclass.
3. **No duplicate infrastructure clients**. One RancherClient, one kubectl adapter. If a service needs a different mode (sync vs async), add a wrapper to the infrastructure module.
4. **No `shell=True`**. Use `shlex.split()` or argument lists. `shell=True` is a command injection vector.
5. **All dataclasses are `frozen=True`**. Mutable dataclasses are a source of bugs. Use `frozen=True` everywhere. If you need to "update" a value, create a new instance with `dataclasses.replace()`.

## Capability Pattern

Each capability uses a consistent pair:

- `*_use_case.py`: command-facing orchestration and run finalization
- `*_service.py`: capability implementation as module-level functions

Use-cases expose stable `execute_*` functions and return `RunResult` in persisted mode.
Shared helpers in `mks.application.use_case_utils` remove repeated flow around temporary execution, stdout rendering, and successful run finalization.

### Service module structure

A service module should look like this:

```python
"""Workload efficiency analysis."""

from mks.domain.quantity_parser import parse_cpu, parse_memory
from mks.infrastructure.kubectl_client import kubectl_json

def collect_workload_data(include_system: bool) -> list[WorkloadMetrics]:
    """Collect and aggregate workload metrics from cluster."""
    pods_data = kubectl_json("get pods -A")
    ...

def generate_report(metrics: list[WorkloadMetrics], output_dir: Path) -> Path:
    """Write CSV report and return file path."""
    ...

def execute_workload_efficiency(
    *, include_system: bool, data_dir: str
) -> None:
    """Top-level service entry point."""
    metrics = collect_workload_data(include_system)
    generate_report(metrics, Path(data_dir))
```

No class wrapping. Call domain and infrastructure functions directly.

## Configuration Flow

```py
CLI (main.py)
  -> load_config()            # once, returns frozen AuditConfig
  -> execute_*(..., config)   # pass relevant config slice to use-case
  -> service function         # receives config as parameter, never reads env
```

Services must never call `load_dotenv()` or `os.getenv()`. They receive what they need as function arguments.

## Run Artifact Contract

Persisted runs use a standard contract from `mks.application.run_writer`:

- run directory: `reports/<capability>/<run_id>/`
- `summary.md`: human-readable summary
- `manifest.json`: machine-readable metadata (`ensure_ascii=False` for clean Unicode)
- generated capability artifacts (CSV/JSON/MD/TXT)

This gives stable automation points for CI, post-processing, and future integrations.

## Error Model

Error handling is explicit by layer.

- infrastructure raises typed runtime errors (for example `KubectlError`, `RancherApiError`)
- application either propagates or translates to capability-level `RuntimeError`/`ValueError`
- CLI maps errors to exit codes

Current CLI mapping:

- `ValueError` -> exit code `1`
- `RuntimeError` -> exit code `2`
- success -> exit code `0`

## Practical Module Map

Core modules to understand first:

- CLI: `src/mks/cli/main.py`
- Config: `src/mks/config.py`
- Use-case contract: `src/mks/application/run_writer.py`
- Use-case shared flow: `src/mks/application/use_case_utils.py`
- Domain policies: `src/mks/domain/namespace_policy.py`
- Quantity parsing: `src/mks/domain/quantity_parser.py`
- Kubectl adapter: `src/mks/infrastructure/kubectl_client.py`
- Rancher adapter: `src/mks/infrastructure/rancher_client.py`

For new features, follow existing capability modules under `src/mks/application/*_use_case.py` and `src/mks/application/*_service.py`.
