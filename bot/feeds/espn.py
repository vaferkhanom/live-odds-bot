"""ESPN scoreboard client (keyless). Carries sportsbook odds blocks per event."""
from __future__ import annotations

from .base import LiveEvent, MarketPrice, OutcomePrice, Source
from .http import get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN's edge WAF blocks browser-like UAs; a neutral client UA is accepted.
ESPN_UA = "live-odds-bot/1.0 (+https://github.com/)"


# sport key -> ESPN league path
SPORT_PATHS = {
    "soccer-epl": "soccer/eng.1",
    "soccer-laliga": "soccer/esp.1",
    "soccer-bundesliga": "soccer/ger.1",
    "soccer-seriea": "soccer/ita.1",
    "soccer-ligue1": "soccer/fra.1",
    "soccer-ucl": "soccer/uefa.champions",
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
    "tennis-atp": "tennis/atp",
}


def _american_to_decimal(odds: float) -> float:
    if odds is None:
        raise ValueError("missing odds")
    odds = float(odds)
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


class EspnSource(Source):
    name = "espn"

    # Max per-cycle summary fetches (live events lacking scoreboard odds only).
    SUMMARY_CAP = 15

    def fetch_live(self, sport: str) -> list[LiveEvent]:
        path = SPORT_PATHS.get(sport)
        if not path:
            return []
        try:
            data = get_json(f"{BASE}/{path}/scoreboard", user_agent=ESPN_UA)
        except Exception:
            return []
        events: list[LiveEvent] = []
        for ev in (data.get("events") or []):
            comps = ev.get("competitions") or [{}]
            comp = comps[0]
            competitors = comp.get("competitors") or []
            names = [c.get("team", {}).get("displayName", "?") for c in competitors]
            label = " vs ".join(names) if names else ev.get("name", "?")
            state = (ev.get("status", {}).get("type", {}) or {}).get("state", "")
            markets: list[MarketPrice] = []
            for odds in comp.get("odds") or []:
                try:
                    details = odds.get("details", "")
                    mk = self._parse_market(odds, details)
                    if mk:
                        markets.append(mk)
                except Exception:
                    continue
            events.append(LiveEvent(
                event_id=str(ev.get("id", "")),
                sport=sport,
                match_label=label,
                is_live=(state == "in"),
                markets=markets,
                raw_score="",
            ))
        # Scoreboards often omit odds for live games; the per-event summary
        # endpoint still carries book prices. Backfill live events only.
        summaries = 0
        for ev in events:
            if ev.markets or not ev.is_live or summaries >= self.SUMMARY_CAP:
                continue
            extra = self._summary_markets(path, ev.event_id)
            if extra:
                ev.markets.extend(extra)
                summaries += 1
        return events

    def _summary_markets(self, path: str, event_id: str) -> list[MarketPrice]:
        """Parse DraftKings odds from the event summary endpoint."""
        try:
            data = get_json(f"{BASE}/{path}/summary?event={event_id}",
                            user_agent=ESPN_UA)
        except Exception:
            return []
        out: list[MarketPrice] = []
        for odds in data.get("pickcenter") or data.get("odds") or []:
            try:
                out.extend(self._parse_summary_odds(odds))
            except Exception:
                continue
        return out

    @staticmethod
    def _moneyline_value(node) -> float | None:
        if not isinstance(node, dict):
            return None
        for key in ("moneyLine", "moneyline", "summary", "odds", "value"):
            if node.get(key) not in (None, ""):
                try:
                    return float(node[key])
                except (TypeError, ValueError):
                    pass
        return None

    def _parse_summary_odds(self, odds: dict) -> list[MarketPrice]:
        found: list[MarketPrice] = []
        outcomes: list[OutcomePrice] = []
        home = self._moneyline_value(odds.get("homeTeamOdds"))
        away = self._moneyline_value(odds.get("awayTeamOdds"))
        draw = self._moneyline_value(odds.get("drawOdds"))
        if home is not None:
            outcomes.append(OutcomePrice(name="home", decimal_odds=_american_to_decimal(home)))
        if away is not None:
            outcomes.append(OutcomePrice(name="away", decimal_odds=_american_to_decimal(away)))
        if draw is not None:
            outcomes.append(OutcomePrice(name="draw", decimal_odds=_american_to_decimal(draw)))
        if len(outcomes) >= 2:
            found.append(MarketPrice(
                market_type="1x2" if draw is not None else "moneyline",
                line=None, outcomes=outcomes))
        ou = odds.get("overUnder")
        try:
            over = float(odds.get("overOdds")) if odds.get("overOdds") not in (None, "") else None
            under = float(odds.get("underOdds")) if odds.get("underOdds") not in (None, "") else None
        except (TypeError, ValueError):
            over = under = None
        if ou is not None and over is not None and under is not None:
            found.append(MarketPrice(market_type="total", line=str(ou), outcomes=[
                OutcomePrice(name="over", decimal_odds=_american_to_decimal(over)),
                OutcomePrice(name="under", decimal_odds=_american_to_decimal(under)),
            ]))
        return found

    def _parse_market(self, odds: dict, details: str) -> MarketPrice | None:
        outcomes: list[OutcomePrice] = []
        home_away = odds.get("homeAway") or odds.get("homeaway")
        if home_away:
            for side in ("home", "away"):
                val = (home_away.get(side) or {})
                summary = val.get("summary") or val.get("odds")
                if summary:
                    try:
                        outcomes.append(OutcomePrice(
                            name=side, decimal_odds=_american_to_decimal(summary)))
                    except (ValueError, TypeError):
                        pass
        draw = odds.get("draw") or {}
        draw_val = draw.get("summary") or draw.get("odds") if isinstance(draw, dict) else draw
        if draw_val:
            try:
                outcomes.append(OutcomePrice(
                    name="draw", decimal_odds=_american_to_decimal(draw_val)))
            except (ValueError, TypeError):
                pass
        if len(outcomes) < 2:
            return None
        market_type = "1x2" if any(o.name == "draw" for o in outcomes) else "moneyline"
        ou = odds.get("overUnder")
        line = str(ou) if ou is not None else None
        if line is not None:
            market_type = "total"
        return MarketPrice(market_type=market_type, line=line, outcomes=outcomes)

    def fetch_finals(self) -> dict[str, dict]:
        """One pass over league scoreboards: event_id -> {'home','away'} finals."""
        finals: dict[str, dict] = {}
        for path in SPORT_PATHS.values():
            try:
                data = get_json(f"{BASE}/{path}/scoreboard", user_agent=ESPN_UA)
            except Exception:
                continue
            for ev in data.get("events") or []:
                comp = (ev.get("competitions") or [{}])[0]
                state = (ev.get("status", {}).get("type", {}) or {}).get("state")
                if state != "post":
                    continue
                try:
                    paired = {c.get("homeAway"): int(c.get("score"))
                              for c in comp.get("competitors") or []}
                    finals[str(ev.get("id"))] = {"home": paired.get("home"),
                                                 "away": paired.get("away")}
                except (TypeError, ValueError):
                    continue
        return finals

    def fetch_final(self, event_id: str) -> dict | None:
        return self.fetch_finals().get(str(event_id))
