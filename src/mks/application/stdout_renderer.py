"""Render temporary report artifacts to stdout using rich."""

import csv
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

_MAX_PREVIEW_ROWS = 20
_TARGET_COLUMN_WIDTH = 16

_CSV_PROFILES: dict[str, tuple[str, ...]] = {
    "rancher_project_users_": (
        "namespace",
        "project_name",
        "project_id",
        "role_template_name",
        "subject_type",
        "user_name",
        "user_username",
        "user_enabled",
    ),
    "rancher_namespaces_simple_": (
        "namespace",
        "project_name",
        "display_name",
        "total_pods",
        "running_pods",
        "failed_pods",
        "pending_pods",
        "created",
    ),
    "rancher_projects_simple_": (
        "project_name",
        "namespace_count",
        "total_pods",
        "running_pods",
        "failed_pods",
        "health_status",
        "namespaces",
    ),
}

_PRIORITY_KEYWORDS = (
    "namespace",
    "project",
    "user",
    "role",
    "status",
    "health",
    "pods",
    "cpu",
    "memory",
    "efficiency",
)


def _normalize(value: str) -> str:
    return value.strip()


def _is_numeric_column(values: Iterable[str]) -> bool:
    seen = False
    for raw in values:
        v = _normalize(raw)
        if not v:
            continue
        seen = True
        try:
            float(v.replace("%", ""))
        except ValueError:
            return False
    return seen


def _non_empty_count(values: list[str]) -> int:
    return sum(1 for value in values if _normalize(value))


def _profile_for_file(file_name: str) -> tuple[str, ...] | None:
    for prefix, columns in _CSV_PROFILES.items():
        if file_name.startswith(prefix):
            return columns
    return None


def _columns_from_rows(headers: list[str], rows: list[list[str]]) -> list[list[str]]:
    columns: list[list[str]] = []
    for col_idx in range(len(headers)):
        columns.append([row[col_idx] if col_idx < len(row) else "" for row in rows])
    return columns


def _drop_redundant_columns(
    headers: list[str],
    columns: list[list[str]],
) -> tuple[list[str], list[list[str]], int]:
    kept_headers: list[str] = []
    kept_columns: list[list[str]] = []
    dropped = 0
    signature_to_index: dict[tuple[str, ...], int] = {}

    for idx, header in enumerate(headers):
        col = columns[idx]
        signature = tuple(_normalize(v) for v in col)
        if _non_empty_count(col) == 0:
            dropped += 1
            continue
        if signature in signature_to_index:
            dropped += 1
            continue
        signature_to_index[signature] = idx
        kept_headers.append(header)
        kept_columns.append(col)

    return kept_headers, kept_columns, dropped


def _profile_selected_indices(
    file_name: str,
    headers: list[str],
    console_width: int,
) -> list[int]:
    profile = _profile_for_file(file_name)
    if not profile:
        return []
    by_name = {header: idx for idx, header in enumerate(headers)}
    max_cols = max(4, console_width // _TARGET_COLUMN_WIDTH)
    return [by_name[h] for h in profile if h in by_name][:max_cols]


def _ranked_indices_for_columns(
    headers: list[str],
    columns: list[list[str]],
    rows_count: int,
    max_cols: int,
) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for idx, header in enumerate(headers):
        lowered = header.lower()
        non_empty = _non_empty_count(columns[idx])
        unique_count = len({_normalize(v) for v in columns[idx] if _normalize(v)})
        scores[idx] += non_empty + (unique_count * 0.5)
        if any(keyword in lowered for keyword in _PRIORITY_KEYWORDS):
            scores[idx] += rows_count * 0.75
    ranked = sorted(scores, key=lambda idx: scores[idx], reverse=True)[:max_cols]
    ranked.sort()
    return ranked


def _select_columns(
    *,
    file_name: str,
    headers: list[str],
    rows: list[list[str]],
    console_width: int,
) -> tuple[list[str], list[list[str]], int]:
    if not headers:
        return [], [], 0

    columns = _columns_from_rows(headers, rows)
    headers, columns, dropped = _drop_redundant_columns(headers, columns)
    if not headers:
        return [], [], dropped

    profile_indices = _profile_selected_indices(file_name, headers, console_width)
    if profile_indices:
        selected_headers = [headers[i] for i in profile_indices]
        selected_columns = [columns[i] for i in profile_indices]
        hidden = len(headers) - len(selected_headers) + dropped
        return selected_headers, selected_columns, hidden

    max_cols = max(4, console_width // _TARGET_COLUMN_WIDTH)
    if len(headers) <= max_cols:
        return headers, columns, dropped

    ranked = _ranked_indices_for_columns(headers, columns, len(rows), max_cols)
    selected_headers = [headers[i] for i in ranked]
    selected_columns = [columns[i] for i in ranked]
    hidden = len(headers) - len(selected_headers) + dropped
    return selected_headers, selected_columns, hidden


def _build_table(
    file_name: str,
    selected_headers: list[str],
    selected_columns: list[list[str]],
) -> Table:
    table = Table(
        title=file_name,
        show_lines=False,
        expand=True,
        box=box.SIMPLE_HEAVY,
    )
    for col_idx, header in enumerate(selected_headers):
        if _is_numeric_column(selected_columns[col_idx]):
            table.add_column(header, overflow="fold", no_wrap=False, justify="right")
        else:
            table.add_column(header, overflow="fold", no_wrap=False, justify="left")
    return table


def _render_csv(console: Console, file_path: Path) -> None:
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        console.print(f"[yellow]{file_path.name} is empty[/yellow]")
        return

    headers = rows[0]
    body = rows[1:]

    selected_headers, selected_columns, hidden = _select_columns(
        file_name=file_path.name,
        headers=headers,
        rows=body,
        console_width=console.width,
    )

    if not selected_headers:
        console.print(f"[yellow]{file_path.name} has no informative columns.[/yellow]")
        return

    table = _build_table(file_path.name, selected_headers, selected_columns)
    selected_index_map = {header: idx for idx, header in enumerate(headers)}
    visible_indices = [selected_index_map[header] for header in selected_headers]

    for row in body[:_MAX_PREVIEW_ROWS]:
        normalized = row + [""] * (len(headers) - len(row))
        table.add_row(*(normalized[idx] for idx in visible_indices))

    console.print(table)
    if hidden > 0:
        console.print(f"[dim]Hidden {hidden} low-value columns.[/dim]")
    if len(body) > _MAX_PREVIEW_ROWS:
        console.print(
            f"[dim]Showing first {_MAX_PREVIEW_ROWS} of {len(body)} rows.[/dim]"
        )


def _render_text_like(console: Console, file_path: Path) -> None:
    text = file_path.read_text(encoding="utf-8")
    lexer = "json" if file_path.suffix == ".json" else "markdown"
    console.print(
        Panel(
            Syntax(text, lexer=lexer, line_numbers=False, word_wrap=True),
            title=file_path.name,
        )
    )


def render_stdout_report(
    *,
    title: str,
    captured_stdout: str,
    output_files: tuple[Path, ...],
) -> None:
    """Render stdout execution summary and artifact previews."""
    console = Console()
    console.print(f"[bold cyan]{title}[/bold cyan]")

    if captured_stdout.strip():
        console.print(
            Panel(captured_stdout.strip(), title="Execution Log", border_style="blue")
        )

    if not output_files:
        console.print("[dim]No output files were generated.[/dim]")
        return

    for file_path in output_files:
        if file_path.suffix.lower() == ".csv":
            _render_csv(console, file_path)
            continue
        if file_path.suffix.lower() in {".md", ".txt", ".json"}:
            _render_text_like(console, file_path)
            continue
        console.print(f"[dim]Generated file: {file_path.name}[/dim]")
