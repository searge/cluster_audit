# Coding Guidelines

## Writing Goals

Write code that is easy to read, safe to change, and consistent with the current architecture.
Prefer clear boundaries and small, focused changes over clever shortcuts.

## Python Version

Target Python 3.13+, forward-compatible with 3.14.

- use built-in generics: `list[str]`, `dict[str, Any]`, `tuple[int, ...]`, `str | None`
- use `type` statement for type aliases: `type NodeName = str`
- use `match`/`case` for dispatch on known literals or enums
- drop `from __future__ import annotations` in new code (only keep in existing modules that need it for migration)
- use `Self` from `typing` for classmethod returns when needed

Set `target-version = "py313"` in ruff and `python_version = "3.13"` in mypy.

## Design Principles

Use these principles in both implementation and refactoring:

- single responsibility per module and function
- explicit layer boundaries
- stable external behavior during internal changes
- shared helpers for repeated logic
- predictable outputs and error handling

When you refactor, preserve contracts first, then improve internals.

## Functional-First Approach

Prefer functions over classes. Use classes only when you manage a lifecycle (connection pool, async context manager, file handle).

**Do this** — module-level functions:

```python
def collect_pods(*, include_system: bool = False) -> list[PodInfo]:
    pods_data = kubectl_json("get pods -A")
    return [
        _parse_pod(pod)
        for pod in pods_data.get("items", [])
        if include_system or not is_system_namespace(pod["metadata"]["namespace"])
    ]

def analyze_efficiency(pods: list[PodInfo]) -> list[EfficiencyRow]:
    return [_efficiency_for(pod) for pod in pods]
```

**Avoid this** — wrapping functions in a class just to hold `self.data_dir`:

```python
# wrong: class exists only to carry parameters between methods
class PodAnalyzer:
    def __init__(self, data_dir: str, include_system: bool):
        self.data_dir = Path(data_dir)
        self.include_system = include_system

    def run_kubectl(self, cmd: str) -> dict:       # pointless wrapper
        return kubectl_json(cmd)

    def parse_cpu(self, s: str) -> int:             # pointless wrapper
        return parse_cpu(s)

    def collect_pods(self) -> list[PodInfo]:
        ...
```

If you see a class where every method takes `self` only to access `self.data_dir` or `self.timestamp`, replace it with functions that take those as parameters.

**When a class is justified:**

- `RancherClient` — manages an `httpx.AsyncClient` connection pool and auth state
- `Console` (from `rich`) — external library, used as-is

## Layer-Aware Coding

Keep responsibilities strict:

- CLI code handles command UX and exit behavior only
- application code orchestrates use-cases and report flows
- domain code contains reusable rules and pure transformations
- infrastructure code owns process, network, and filesystem adapters

If a function starts doing work from another layer, split it.

### Forbidden patterns

These patterns are banned because each caused real bugs or duplication:

| Pattern                                     | Where it was found                   | What to do instead                          |
| ------------------------------------------- | ------------------------------------ | ------------------------------------------- |
| `subprocess.run()` in application code      | 3 services                           | call `kubectl_client` functions             |
| `load_dotenv()` / `os.getenv()` in services | 2 services                           | accept config as a function parameter       |
| Duplicate `RancherClient`                   | deletion investigation service       | import from `infrastructure.rancher_client` |
| `shell=True` in subprocess                  | 3 services                           | use `shlex.split()` or argument lists       |
| Instance method wrapping a shared function  | 4 services (`self.parse_cpu()` etc.) | call the domain function directly           |

## Use-Case Structure

For each capability, prefer:

- one `*_use_case.py` file for command-level orchestration
- one `*_service.py` file for implementation logic as module-level functions

Use shared utilities for common run flow (`create_run`, summary/manifest generation, stdout rendering) instead of re-implementing the same steps.

## Data Modeling

Model intent with typed structures. All data structures are immutable.

- use `@dataclass(frozen=True)` for named entities with serialization or computed properties
- use `NamedTuple` for compact immutable records (especially with many numeric fields)
- never use mutable `@dataclass` — always `frozen=True`
- keep model names explicit (`RunResult`, `AuditSnapshot`, `SchedulingIssue`)

Example:

```python
@dataclass(frozen=True)
class ContainerResources:
    name: str
    cpu_request: int       # millicores
    cpu_limit: int         # millicores
    memory_request: int    # bytes
    memory_limit: int      # bytes
    issues: tuple[str, ...]  # immutable collection

    @property
    def severity(self) -> str:
        if any("NO_" in i and "RESOURCES" in i for i in self.issues):
            return "CRITICAL"
        ...
```

For "updating" a frozen dataclass, use `dataclasses.replace()`:

```python
from dataclasses import replace

updated = replace(original, cpu_limit=new_limit)
```

Prefer `tuple` over `list` in frozen dataclass fields. Lists inside frozen dataclasses are still mutable — tuples enforce full immutability.

## Configuration

Config is loaded once at startup and passed through the call chain as frozen dataclasses.

```python
# cli/main.py — load once
config = load_config()

# pass relevant slice to use-case
execute_rancher_users_export(namespaces, config=config.rancher, ...)
```

Services must never:

- call `load_dotenv()`
- call `os.getenv()`
- import from `dotenv`

These belong exclusively in `mks.config`.

## Typing Rules

- add type hints for all public functions and methods
- keep return types explicit
- avoid `Any` unless interoperability requires it (raw kubectl JSON is acceptable as `dict[str, Any]`)
- narrow exceptions and values as close to source as possible
- use `type` aliases for domain concepts: `type Millicores = int`

Run type checks regularly while editing, not only at the end.

## Error Handling

Handle errors at the correct level.

- infrastructure raises adapter-specific runtime errors (`KubectlError`, `RancherApiError`)
- application converts low-level failures into capability-level errors
- CLI maps known exceptions to user-facing exit codes

Do not hide failures silently. If returning a fallback value (for example `{}`), do it intentionally and only where the flow is designed to continue.

## IO and External Commands

- route all kubectl access through `mks.infrastructure.kubectl_client`
- route all Rancher API access through `mks.infrastructure.rancher_client`
- use `Path` APIs for filesystem paths
- always specify `encoding="utf-8"` when reading/writing text files
- prefer deterministic outputs and sorted artifact lists
- use `ensure_ascii=False` in JSON output for clean Unicode

If command execution or parsing is reused, extract it once in the infrastructure layer and share it.

**Never** call `subprocess.run()` from application or domain code. If you need a new external command, add an adapter function to `mks.infrastructure`.

## Duplication Policy

Avoid copy-paste across services.

When the same logic appears more than once, extract a shared helper in the right layer:

- domain for pure rules and transformations
- infrastructure for adapter behavior
- application for orchestration patterns

Call domain functions directly — do not wrap them in instance methods:

```python
# wrong
class MyService:
    def parse_cpu(self, s: str) -> int:
        return parse_cpu(s)  # pointless delegation

# right
from mks.domain.quantity_parser import parse_cpu

def analyze(pod: dict) -> int:
    return parse_cpu(pod["resources"]["requests"]["cpu"])
```

Keep helper APIs small and intention-revealing.

## Refactoring Practice

Refactor incrementally and verify after each step.

- keep public entrypoints stable
- split large functions into named helpers
- move shared logic without changing output schema
- prefer small PR-style changes over broad rewrites
- replace classes with functions when the class holds no real state

A safe refactor is one where behavior stays the same and the structure becomes easier to reason about.

## Dependencies

Keep the dependency list honest:

- only list packages that are actually imported in `src/`
- use optional dependency groups for backends that not every user needs (e.g., `[ovh]`)
- review dependencies when removing features — drop unused packages

Current required runtime deps and their purpose:

| Package         | Used by                                    |
| --------------- | ------------------------------------------ |
| `diskcache`     | rancher users export (API response cache)  |
| `httpx`         | infrastructure/rancher_client (async HTTP) |
| `pandas`        | report generation, dashboard analytics     |
| `python-dotenv` | config.py (env file loading)               |
| `rich`          | CLI output, stdout renderer                |
| `typer`         | CLI framework                              |

## Documentation Style

- write concise docstrings for public functions (one line when possible)
- describe purpose and contract, not implementation trivia
- keep comments rare and meaningful
- update architecture and guideline documents when patterns evolve

## Quality Gate

Before finishing work, run the full project checks:

```bash
task test -- src/
```

For fast feedback during development, use targeted commands as needed (`ruff`, `mypy`, `pyright`, `pylint`), but final status must pass the full task.
