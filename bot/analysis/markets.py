"""Per-sport market catalog vs 1xBet taxonomy + gap registry helpers."""
from __future__ import annotations

import os

_CATALOG: dict | None = None


def _load() -> dict:
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    path = os.path.join(os.path.dirname(__file__), "..", "catalog", "markets.yaml")
    catalog: dict[str, dict] = {}
    current_sport: str | None = None
    try:
        with open(path) as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if not line.strip() or line.strip().startswith("#"):
                    continue
                if not line.startswith((" ", "\t")) and line.strip().endswith(":"):
                    current_sport = line.strip()[:-1]
                    catalog[current_sport] = {}
                elif current_sport and ":" in line:
                    key, _, val = line.strip().partition(":")
                    catalog[current_sport][key.strip()] = val.strip().lower() in (
                        "true", "monitored", "yes", "1")
    except FileNotFoundError:
        pass
    _CATALOG = catalog
    return catalog


def is_monitored(sport: str, market_type: str) -> bool:
    return bool(_load().get(sport, {}).get(market_type, False))


def monitored_markets(sport: str) -> list[str]:
    return [m for m, on in _load().get(sport, {}).items() if on]


def unmonitored_markets(sport: str) -> list[str]:
    """Catalog-declared gaps (e.g. player props): never monitored by design."""
    return [m for m, on in _load().get(sport, {}).items() if not on]


def all_sports() -> list[str]:
    return sorted(_load().keys())
