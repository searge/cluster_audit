# Kubernetes Resource Audit Tool

A comprehensive Kubernetes resource auditing tool that analyzes resource usage, limits, and efficiency across your cluster.

## Quick Start

Use `Taskfile.yaml` as the primary interface. Avoid running Python scripts directly.

### Install Dependencies

```bash
task install
```

### Common Tasks

```bash
# Show available tasks
task --list

# Standard audit
task audit

# Extended operational audit
task audit-extended

# Workload efficiency CSV (requests/limits vs actual usage)
task workload-efficiency
# or short alias
task we

# Generate dashboard summary
task dashboard
```

### Automated Runs (cronjobs)

```bash
./scripts/cronjobs.sh current
./scripts/cronjobs.sh dashboard
./scripts/cronjobs.sh health
./scripts/cronjobs.sh cleanup
```

`cleanup` removes old report files:

- `*.csv` older than 14 days
- `recommendations_*.md` and `usage_recommendations_*.md` older than 7 days
- keeps only the last 5 `weekly_summary_*.md`

## Generated Reports

All reports are saved to the `reports/` directory:

- **CSV files**: Detailed data for pods, nodes, and namespaces
- **Markdown files**: Human-readable recommendations and analysis
- **Trends data**: Historical tracking of cluster metrics

## Development

See [docs/CODING_GUIDELINES.md](docs/CODING_GUIDELINES.md) for development standards and code quality tools.

**Quality checks:**

```bash
task lint
task typecheck
task fix
```

## Prerequisites

- Python 3.12+
- `kubectl` configured with cluster access
- `uv` for dependency management
- Kubernetes metrics-server (for real usage analysis)
