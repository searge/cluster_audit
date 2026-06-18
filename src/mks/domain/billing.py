"""Pure helpers for OVH MKS billing classification.

Maps free-text OVH billing line descriptions to the column layout of the
``[OVH MKS] Payment`` sheet. No IO, no side effects.
"""

import re

# CSV schema (matches the [OVH MKS] Payment sheet column order).
COLUMNS: tuple[str, ...] = (
    "Monthly total",
    "Hourly total",
    "add. hs disks",
    "Old LB",
    "Octavia LB",
    "Public Gateway",
    "Floating IP",
)

# Description -> column rules. First match wins; keep specific before generic.
# OVH descriptions are free French text and can drift, so anything unmatched is
# reported as UNMAPPED by the service rather than silently dropped.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "forfait mensuel ... b2-15" (full month) and "prorata de la facturation
    # mensuelle ... b2-15" (node added/removed mid-month) are both the monthly fee.
    (
        "Monthly total",
        re.compile(r"(forfait|facturation) mensuel(le)?.*b2-15", re.IGNORECASE),
    ),
    ("Hourly total", re.compile(r"consommation.*d2-8", re.IGNORECASE)),
    ("add. hs disks", re.compile(r"disques?\s+suppl", re.IGNORECASE)),
    ("Octavia LB", re.compile(r"octavia", re.IGNORECASE)),
    ("Old LB", re.compile(r"network\s*loadbalancer", re.IGNORECASE)),
    ("Public Gateway", re.compile(r"gateway|passerelle", re.IGNORECASE)),
    ("Floating IP", re.compile(r"floating\s*ip|ip\s*flottante", re.IGNORECASE)),
)


def classify(description: str) -> str | None:
    """Map a billing line description to a CSV column, or None if unknown."""
    for column, pattern in RULES:
        if pattern.search(description):
            return column
    return None


def shift_month(label: str, offset: int) -> str:
    """Shift a ``YYYY-MM-01`` label by ``offset`` months, same format."""
    year, month, _ = (int(part) for part in label.split("-"))
    idx = (year * 12 + (month - 1)) + offset
    new_year, new_month = divmod(idx, 12)
    return f"{new_year:04d}-{new_month + 1:02d}-01"


def month_floor_iso(year: int, month: int) -> str:
    """Return the first instant of ``year``-``month`` as an OVH API timestamp."""
    return f"{year:04d}-{month:02d}-01T00:00:00Z"


__all__ = ["COLUMNS", "RULES", "classify", "shift_month", "month_floor_iso"]
