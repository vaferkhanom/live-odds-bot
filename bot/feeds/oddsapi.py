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
                             {"regions": "us,uk", "markets": "h2h,spreads,totals",
                              "oddsFormat": "decimal"})
        except OddsAPIExhausted:
            raise
        except Exception:
            return []
        events: list[LiveEvent] = []
        for ev in data or []:
            try:
                home, away = ev.get("home_team", ""), ev.get("away_team", "")
                books = ev.get("bookmakers") or []
                markets = self._parse_books(books, home, away)
                if not markets:
                    continue
                events.append(LiveEvent(
                    event_id=f"oddsapi-{ev.get('id')}",
                    sport=sport,
                    match_label=f"{home} vs {away}",
                    is_live=True,
                    markets=markets,
                ))
            except Exception:
                continue
        return events

    @staticmethod
    def _parse_books(books: list, home: str, away: str) -> list[MarketPrice]:
        """Median-across-books prices per market: h2h, spreads, totals.

        Spread/total legs carry opposite points per side (-1.5/+1.5), so
        sides group by market key and the line is taken from the
        home/over side (first side otherwise).
        """
        by_market: dict[str, dict[str, list[float]]] = {}
        side_point: dict[tuple, str] = {}
        for book in books:
            for market in book.get("markets") or []:
                mkey = market.get("key")
                if mkey not in ("h2h", "spreads", "totals"):
                    continue
                for outcome in market.get("outcomes") or []:
                    try:
                        price = float(outcome.get("price") or 0)
                    except (TypeError, ValueError):
                        continue
                    if price <= 1:
                        continue
                    name = str(outcome.get("name"))
                    if mkey == "h2h":
                        side = "home" if name == home else "away" if name == away else "draw"
                    elif mkey == "spreads":
                        side = "home" if name == home else "away" if name == away else name
                    else:
                        side = name.lower()  # Over/Under
                    by_market.setdefault(mkey, {}).setdefault(side, []).append(price)
                    point = outcome.get("point")
                    if point is not None and (mkey, side) not in side_point:
                        side_point[(mkey, side)] = str(point)
        out: list[MarketPrice] = []
        for mkey, sides in by_market.items():
            if len(sides) < 2:
                continue
            mtype = {"h2h": "moneyline", "spreads": "handicap", "totals": "total"}[mkey]
            if mkey == "h2h":
                line = None
            else:
                first_side = "home" if "home" in sides else "over" if "over" in sides else sorted(sides)[0]
                line = side_point.get((mkey, first_side))
            out.append(MarketPrice(
                market_type=mtype, line=line,
                outcomes=[OutcomePrice(name=s, decimal_odds=median(p)) for s, p in sides.items()],
            ))
        return out
