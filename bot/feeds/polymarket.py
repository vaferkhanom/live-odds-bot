"""Polymarket Gamma API client (keyless, browser UA required)."""
from __future__ import annotations

from .base import LiveEvent, MarketPrice, OutcomePrice, Source
from .http import get_json

GAMMA = "https://gamma-api.polymarket.com"

# bot sport key -> Polymarket series id (verified 2026-09-18). None = no
# dedicated series: fall back to keyword-filtered global fetch.
SERIES = {
    "soccer-epl": "10188",
    "soccer-laliga": "10193",
    "soccer-bundesliga": "10194",
    "soccer-seriea": "10203",
    "soccer-ligue1": "10195",
    "soccer-ucl": None,
    "nba": "10345",
    "nfl": "12185",
    "mlb": "3",
    "nhl": "10346",
    "tennis-atp": None,
}

# Fallback keywords, used only when a sport has no series id.
SPORT_KEYWORDS = {
    "soccer-ucl": ["champions league"],
    "tennis-atp": ["atp"],
}


class PolymarketSource(Source):
    name = "polymarket"

    def fetch_live(self, sport: str) -> list[LiveEvent]:
        series = SERIES.get(sport)
        try:
            if series:
                data = get_json(f"{GAMMA}/events",
                                params={"series_id": series, "closed": "false",
                                        "limit": 100})
            else:
                data = get_json(f"{GAMMA}/events",
                                params={"closed": "false", "limit": 150})
        except Exception:
            return []
        keywords = [] if series else SPORT_KEYWORDS.get(sport, [])
        events: list[LiveEvent] = []
        for ev in data or []:
            title = ev.get("title") or ""
            if keywords and not any(k in title.lower() for k in keywords):
                continue
            markets: list[MarketPrice] = []
            for m in ev.get("markets") or []:
                try:
                    outcomes = m.get("outcomes")
                    prices = m.get("outcomePrices")
                    if isinstance(outcomes, str):
                        import json as _json
                        outcomes = _json.loads(outcomes)
                        prices = _json.loads(prices)
                    if not outcomes or not prices:
                        continue
                    volume = float(m.get("volume") or 0)
                    ops = [OutcomePrice(name=str(o),
                                        decimal_odds=(1 / float(p)) if float(p) > 0 else 0.0,
                                        volume=volume)
                           for o, p in zip(outcomes, prices) if float(p) > 0]
                    if len(ops) >= 2:
                        markets.append(MarketPrice(market_type="moneyline",
                                                   line=None, outcomes=ops))
                except Exception:
                    continue
            if markets:
                events.append(LiveEvent(
                    event_id=f"poly-{ev.get('id')}",
                    sport=sport,
                    match_label=ev.get("title", "?"),
                    is_live=True,
                    markets=markets,
                    starts_at=ev.get("startTime") or ev.get("gameStartTime") or None,
                ))
        return events
