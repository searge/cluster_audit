"""Shared run writer for standardized outputs."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunResult:
    """Standardized result returned by all use-cases."""

    run_id: str
    capability: str
    output_dir: Path
    manifest_path: Path
    summary_path: Path
    output_files: tuple[Path, ...]


@dataclass(frozen=True)
class RunContext:
    """Mutable context for an in-progress run."""

    run_id: str
    capability: str
    output_dir: Path
    started_at: str
    inputs: dict[str, Any]


@dataclass(frozen=True)
class SummaryContent:
    """Summary payload used to build summary markdown."""

    title: str
    key_findings: list[str] | None = None
    warnings: list[str] | None = None
    inputs: dict[str, Any] | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_run(
    capability: str,
    *,
    inputs: dict[str, Any],
    reports_root: str = "reports",
) -> RunContext:
    """Create run directory context."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(reports_root) / capability / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        run_id=run_id,
        capability=capability,
        output_dir=output_dir,
        started_at=_utc_now_iso(),
        inputs=inputs,
    )


def list_output_files(output_dir: Path) -> tuple[Path, ...]:
    """List report artifacts under output directory."""
    return tuple(sorted(p for p in output_dir.rglob("*") if p.is_file()))


def finalize_run(
    ctx: RunContext,
    *,
    status: str,
    output_files: tuple[Path, ...],
    summary_lines: list[str],
    error: str | None = None,
) -> RunResult:
    """Write summary.md and manifest.json and return run result."""
    summary_path = ctx.output_dir / "summary.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    manifest_path = ctx.output_dir / "manifest.json"
    all_outputs = tuple(output_files) + (summary_path,)

    manifest_payload = {
        "run_id": ctx.run_id,
        "capability": ctx.capability,
        "started_at": ctx.started_at,
        "finished_at": _utc_now_iso(),
        "status": status,
        "inputs": ctx.inputs,
        "outputs": [str(p.relative_to(ctx.output_dir)) for p in all_outputs]
        + ["manifest.json"],
        "error": error,
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return RunResult(
        run_id=ctx.run_id,
        capability=ctx.capability,
        output_dir=ctx.output_dir,
        manifest_path=manifest_path,
        summary_path=summary_path,
        output_files=all_outputs + (manifest_path,),
    )


def build_summary_lines(
    content: SummaryContent,
    capability: str,
    output_files: tuple[Path, ...],
) -> list[str]:
    """Build standardized summary markdown lines."""
    inputs = content.inputs or {}
    rel_outputs = [str(p.name) for p in output_files]

    lines = [f"# {content.title}", "", "## Inputs"]
    if inputs:
        for key in sorted(inputs):
            lines.append(f"- `{key}`: `{inputs[key]}`")
    else:
        lines.append("- (none)")

    lines.extend(["", "## Outputs", f"- `capability`: `{capability}`"])
    lines.append(f"- `artifacts_count`: `{len(output_files)}`")
    if rel_outputs:
        lines.append("- `artifacts`:")
        lines.extend(f"  - `{name}`" for name in rel_outputs)
    else:
        lines.append("- `artifacts`: (none)")

    lines.extend(["", "## Key Findings"])
    if content.key_findings:
        lines.extend(f"- {item}" for item in content.key_findings)
    else:
        lines.append("- No additional findings were recorded.")

    lines.extend(["", "## Warnings"])
    if content.warnings:
        lines.extend(f"- {item}" for item in content.warnings)
    else:
        lines.append("- None.")

    return lines


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    """Convert run result to JSON-serializable dictionary."""
    raw = asdict(result)
    raw["output_dir"] = str(result.output_dir)
    raw["manifest_path"] = str(result.manifest_path)
    raw["summary_path"] = str(result.summary_path)
    raw["output_files"] = [str(p) for p in result.output_files]
    return raw
