"""Prices refresh use-case."""

from mks.application.prices_refresh_service import (
    execute_prices_refresh as _service,
)
from mks.config import load_config, load_prices


def execute_prices_refresh() -> None:
    """Print live OVH catalog prices vs the local price reference.

    Advisory and read-only: it never writes ``config/ovh_prices.toml`` (which
    holds hand-written comments) — it prints a TOML block to paste by hand.
    """
    _service(ovh_config=load_config().ovh, current=load_prices())


__all__ = ["execute_prices_refresh"]
