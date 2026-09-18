"""The Odds API v4 client (user-supplied key, highest-priority source).

Free tier: ~500 requests/month. Every response carries quota headers
(x-requests-used / x-requests-remaining); 401 = bad key, 429 = exhausted.
Never logs the key.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from statistics import median

from .. import config
from .base import LiveEvent, MarketPrice, OutcomePrice, Source

BASE = "https://api.the-odds-api.com/v4"

# bot sport key -> Odds API sport key
SPORT_KEYS = {
    "soccer-epl": "soccer_epl",
    "soccer-laliga": "soccer_spain_la_liga",
    "soccer-bundesliga": "soccer_germany_bundesliga",
    "soccer-seriea": "soccer_italy_serie_a",
    "soccer-ligue1": "soccer_france_ligue_1",
    "soccer-ucl": "soccer_uefa_champs_league",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "tennis-atp": "tennis_atp",
}

PROVIDER = "theoddsapi"


class OddsAPIExhausted(Exception):
    """Raised on 401/429 or zero remaining quota. Message is safe to log."""


class TheOddsAPISource(Source):
    name = "oddsapi"

    def __init__(self, api_key: str):
        self._key = api_key
        self.last_used: int | None = None
        self.last_remaining: int | None = None

    def _get(self, path: str, params: dict) -> object:
        from urllib.parse import urlencode
        params = {**params, "apiKey": self._key}
        req = urllib.request.Request(
            f"{BASE}{path}?{urlencode(params)}",
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                headers = resp.headers
                body = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 429):
                raise OddsAPIExhausted(f"theoddsapi quota error HTTP {exc.code}")
            raise
        try:
            self.last_used = int(headers.get("x-requests-used")) if headers.get("x-requests-used") else None
            rem = headers.get("x-requests-remaining")
            self.last_remaining = int(rem) if rem else None
        except (TypeError, ValueError):
            pass
        if self.last_remaining == 0:
            raise OddsAPIExhausted("theoddsapi quota exhausted (0 remaining)")
        return body

    def validate(self) -> dict:
        """Cheap key check: /sports costs 0 requests. Returns quota info."""
        data = self._get("/sports", {})
        return {"ok": True, "sports": len(data) if isinstance(data, list) else 0,
                "used": self.last_used, "remaining": self.last_remaining}

    def fetch_live(self, sport: str) -> list[LiveEvent]:
        key = SPORT_KEYS.get(sport)
        if not key:
            return []
        try:
            data = self._get(f"/sports/{key}/odds",
                             {"regions": "us", "markets": "h2h", "oddsFormat": "decimal"})
        except OddsAPIExhausted:
            raise
        except Exception:
            return []
        events: list[LiveEvent] = []
        for ev in data or []:
            try:
                books = ev.get("bookmakers") or []
                by_outcome: dict[str, list[float]] = {}
                for book in books:
                    for market in book.get("markets") or []:
                        if market.get("key") != "h2h":
                            continue
                        for outcome in market.get("outcomes") or []:
                            price = float(outcome.get("price") or 0)
                            if price > 1:
                                by_outcome.setdefault(str(outcome.get("name")), []).append(price)
                if len(by_outcome) < 2:
                    continue
                home, away = ev.get("home_team", ""), ev.get("away_team", "")
                outcomes = []
                for name, prices in by_outcome.items():
                    side = "home" if name == home else "away" if name == away else "draw"
                    outcomes.append(OutcomePrice(name=side, decimal_odds=median(prices)))
                if len(outcomes) < 2:
                    continue
                events.append(LiveEvent(
                    event_id=f"oddsapi-{ev.get('id')}",
                    sport=sport,
                    match_label=f"{home} vs {away}",
                    is_live=True,
                    markets=[MarketPrice(market_type="moneyline", line=None, outcomes=outcomes)],
                ))
            except Exception:
                continue
        return events
