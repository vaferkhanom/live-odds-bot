"""Feed interface + normalized event model. All sources implement Source."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OutcomePrice:
    name: str
    decimal_odds: float
    volume: float = 0.0  # source-native units; 0 = unknown


@dataclass
class MarketPrice:
    market_type: str
    line: str | None
    outcomes: list[OutcomePrice] = field(default_factory=list)


@dataclass
class LiveEvent:
    event_id: str
    sport: str
    match_label: str
    is_live: bool
    markets: list[MarketPrice] = field(default_factory=list)
    raw_score: str = ""
    # ISO kickoff/start time when the source provides it. Used to drop
    # stale (already-played) fixtures that would otherwise alert on old odds.
    starts_at: str | None = None


class Source:
    name: str = "base"

    def fetch_live(self, sport: str) -> list[LiveEvent]:
        raise NotImplementedError

    def fetch_final(self, event_id: str) -> dict | None:
        """Return {'home': int, 'away': int} or None if unavailable."""
        return None
