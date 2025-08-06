# Python Coding Guidelines

## Code Style

**Formatting**: Use Ruff for automatic formatting and linting
```bash
uv run ruff format .
uv run ruff check .
```

**Type Hints**: Always use type hints for function parameters and returns
```python
def parse_cpu(self, cpu_str: str) -> float:
    """Parse CPU string to millicores"""
```

**Docstrings**: Use concise docstrings for all public functions and classes
```python
@dataclass
class ContainerResources:
    """Container resource specification"""
```

## Architecture Patterns

**Dataclasses**: Use `@dataclass` for data structures instead of plain classes
```python
@dataclass
class AuditSnapshot:
    """Complete audit snapshot at a point in time"""
    timestamp: datetime
    nodes: list[NodeInfo]
    pods: list[PodInfo]
```

**Functional Style**: Prefer pure functions and immutable data where possible
```python
@dataclass(frozen=True)
class AuditConfig:
    """Configuration for audit dashboard analysis"""
    data_dir: Path
    cpu_waste_threshold_m: int = 100
```

**Error Handling**: Use explicit error handling with informative messages
```python
try:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)
except subprocess.CalledProcessError as e:
    print(f"❌ Error running kubectl: {e}")
    sys.exit(1)
```

## File Organization

- **Single Responsibility**: Each module handles one domain (audit, dashboard, analysis)
- **Clear Naming**: Use descriptive names (`resource_audit.py`, `audit_dashboard.py`)
- **Configuration**: Keep configuration centralized in dataclasses

## Best Practices

- Use `pathlib.Path` for file operations
- Use f-strings for string formatting
- Prefer list comprehensions over loops when readable
- Use `subprocess.run()` with proper error handling for shell commands
- Always specify encoding when opening files

## Dependencies

Stick to minimal, well-maintained dependencies:
- `pandas` for data analysis
- `matplotlib`/`seaborn` for visualization
- Standard library for everything else

## Testing

Follow the existing pattern:
```bash
uv run python -m mypy .
uv run ruff check .
```