"""Shared step-by-step console helpers for self-verifying services.

Services print ``[ ok ]`` / ``[warn]`` lines under numbered STEP banners so a
human can eyeball a run before trusting its artifacts. Output is captured by the
use-case stdout helpers and re-rendered.
"""


def banner(step: int, title: str) -> None:
    """Print a numbered step banner."""
    print(f"\n{'=' * 70}\nSTEP {step}: {title}\n{'=' * 70}")


def ok(msg: str) -> None:
    """Print a success line."""
    print(f"  [ ok ] {msg}")


def warn(msg: str) -> None:
    """Print a warning line."""
    print(f"  [warn] {msg}")


def info(msg: str) -> None:
    """Print an indented informational line."""
    print(f"         {msg}")


__all__ = ["banner", "info", "ok", "warn"]
