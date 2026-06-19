"""Prices refresh service: compare config/ovh_prices.toml with the live catalog.

Read-only: it never writes the file (which carries hand-written comments). It
prints the live catalog price for each tracked flavor, the delta vs the local
file, and a ready-to-paste TOML block to update by hand.
"""

from mks.application._ovh_session import ovh_session
from mks.application._step_report import banner, info, ok, warn
from mks.config import OvhConfig, Prices


def _fmt(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "—"


def _delta(current: float | None, live: float | None) -> str:
    if current is None or live is None or abs(current - live) < 1e-9:
        return ""
    return f"  (was {current:.4f}, Δ {live - current:+.4f})"


def _print_toml(live: Prices, tracked: list[str]) -> None:
    """Print a ready-to-paste TOML block for the tracked flavors + volume."""
    info("ready-to-paste config/ovh_prices.toml values:")
    for name in tracked:
        price = live.flavors.get(name)
        print(f"\n[flavors.{name}]")
        if price and price.monthly_eur is not None:
            print(f"monthly_eur = {price.monthly_eur}")
        if price and price.hourly_eur is not None:
            print(f"hourly_eur = {price.hourly_eur}")
    print("\n[volume]")
    print(f"high_speed_eur_per_gb_month = {live.volume_high_speed_eur_per_gb_month}")


def execute_prices_refresh(*, ovh_config: OvhConfig, current: Prices) -> None:
    """Print live catalog prices vs the local file for tracked flavors."""
    with ovh_session(ovh_config) as client:
        banner(2, "Fetch live catalog prices")
        live = client.get_catalog_prices()
    ok(f"catalog has {len(live.flavors)} flavors priced")

    banner(3, "Compare with config/ovh_prices.toml")
    tracked = sorted(current.flavors)
    if not tracked:
        warn("no flavors tracked in config/ovh_prices.toml yet")
    for name in tracked:
        cur = current.flavors[name]
        new = live.flavors.get(name)
        if new is None:
            warn(f"{name}: not found in live catalog")
            continue
        monthly = f"{_fmt(new.monthly_eur)}{_delta(cur.monthly_eur, new.monthly_eur)}"
        hourly = f"{_fmt(new.hourly_eur)}{_delta(cur.hourly_eur, new.hourly_eur)}"
        info(f"{name}: monthly {monthly} | hourly {hourly}")
    vol_cur = current.volume_high_speed_eur_per_gb_month
    vol_new = live.volume_high_speed_eur_per_gb_month
    info(f"volume high-speed/GB: {_fmt(vol_new)}{_delta(vol_cur, vol_new)}")

    banner(4, "Update")
    _print_toml(live, tracked)


__all__ = ["execute_prices_refresh"]
