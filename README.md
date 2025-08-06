# Kubernetes Resource Audit Tool

A comprehensive Kubernetes resource auditing tool that analyzes resource usage, limits, and efficiency across your cluster.

## Quick Start

### Install Dependencies

```bash
# Install Python dependencies
uv sync

# Install pyright for type checking (optional)
pnpm add -g pyright
```

### Running Scripts

**Main resource audit:**

```bash
# Run complete audit
uv run python resource_audit.py

# Run real usage analysis (requires metrics-server)
uv run python real_usage_analysis.py

# Generate dashboard summary
uv run python audit_dashboard.py
```

**Automated runs (cronjobs):**

```bash
# Run current audit (for hourly cron)
./scripts/cronjobs.sh current

# Run weekly analysis
./scripts/cronjobs.sh weekly

# Run dashboard summary
./scripts/cronjobs.sh dashboard

# Health check
./scripts/cronjobs.sh health
```

## Generated Reports

All reports are saved to the `reports/` directory:

- **CSV files**: Detailed data for pods, nodes, and namespaces
- **Markdown files**: Human-readable recommendations and analysis
- **Trends data**: Historical tracking of cluster metrics

## Updates

```bash
# Update dependencies
uv sync

# Update pyright
pnpm add -g pyright@latest
```

## Development

See [docs/CODING_GUIDELINES.md](docs/CODING_GUIDELINES.md) for development standards and code quality tools.

**Quality checks:**

```bash
uv run ruff format .     # Format code
uv run ruff check .      # Lint code
uv run python -m mypy .  # Type check
pyright .                # Advanced type checking
```

## Prerequisites

- Python 3.12+
- `kubectl` configured with cluster access
- `uv` for dependency management
- `pnpm` for Node.js packages (optional)
- Kubernetes metrics-server (for real usage analysis)
