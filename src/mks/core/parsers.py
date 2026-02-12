"""Backward-compatible shim for quantity parsers."""

from mks.domain.quantity_parser import parse_cpu, parse_memory

__all__ = ["parse_cpu", "parse_memory"]
