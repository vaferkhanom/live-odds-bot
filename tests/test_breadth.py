import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot import monitor
from bot.analysis import pipeline
from bot.analysis.pipeline import _compatible, _matching_events, outcome_label
from bot.feeds.base import LiveEvent, MarketPrice, OutcomePrice
from bot.feeds.oddsapi import TheOddsAPISource
from bot.store import Store


def _mk(mtype, line, outcomes):
    return MarketPrice(mtype, line,
                       [OutcomePrice(n, o) for n, o in outcomes])


def _ev(label, markets, sport="soccer-epl", source="espn"):
    ev = LiveEvent("e1", sport, label, True, markets)
    ev.source_name = source
    return ev


def test_outcome_labels_are_accurate():
    assert outcome_label("1x2", "home", "Brentford vs Chelsea") == "Win for Brentford"
    assert outcome_label("moneyline", "away", "Brentford vs Chelsea") == "Win for Chelsea"
    assert outcome_label("1x2", "draw", "Brentford vs Chelsea") == "Draw"
    assert outcome_label("total", "over", "Brentford vs Chelsea") == "over"
    assert outcome_label("moneyline", "yes", "A vs B") == "Yes — A win"
    assert outcome_label("total", "yes", "A vs B") == "yes"


def test_evaluate_all_covers_every_market():
    import bot.config as config
    ev = _ev("A vs B", [_mk("moneyline", None, [("home", 1.7), ("away", 2.4)]),
                        _mk("total", "2.5", [("over", 1.9), ("under", 2.0)])])
    cor = _ev("A vs B", [_mk("moneyline", None, [("home", 2.6), ("away", 1.6)]),
                         _mk("total", "2.5", [("over", 2.6), ("under", 1.6)])],
              source="polymarket")
    sels = pipeline.evaluate_all(ev, [cor], config)
    types = {s["market_type"] for s in sels}
    assert "moneyline" in types and "total" in types
    assert all("outcome_label" in s for s in sels)
    ml = next(s for s in sels if s["market_type"] == "moneyline")
    assert ml["outcome_label"] == "Win for A"


def test_totals_require_matching_line():
    assert _compatible("total", "2.5", "total", "2.5") is True
    assert _compatible("total", "2.5", "total", "3.5") is False
    assert _compatible("moneyline", None, "1x2", None) is True
    assert _compatible("moneyline", None, "total", None) is False
    cor = _ev("A vs B", [_mk("total", "3.5", [("over", 2.6), ("under", 1.6)])],
              source="polymarket")
    matched = _matching_events([cor], "total", "2.5")
    assert matched == []


def test_stale_events_rejected():
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    ev = LiveEvent("e", "nba", "A vs B", False, [], starts_at=old)
    assert monitor.is_stale(ev) is True
    live = LiveEvent("e", "nba", "A vs B", True, [], starts_at=old)
    assert monitor.is_stale(live) is False
    fresh = LiveEvent("e", "nba", "A vs B", False, [], starts_at=None)
    assert monitor.is_stale(fresh) is False


def test_past_games_excluded_from_backfill_window():
    from bot.feeds.espn import EspnSource
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
    board = {"events": [{"id": "9", "name": "X vs Y", "date": yesterday,
                         "status": {"type": {"state": "pre"}},
                         "competitions": [{"competitors": [
                             {"homeAway": "home", "team": {"displayName": "X"}},
                             {"homeAway": "away", "team": {"displayName": "Y"}}],
                             "odds": []}]}]}
    with patch("bot.feeds.espn.get_json", return_value=board) as g:
        evs = EspnSource().fetch_live("nba")
    assert evs[0].markets == []
    assert all("summary" not in str(c) for c in [g.call_args_list])


def test_match_final_fuzzy_settles_cross_source():
    finals = {"101": {"home": 2, "away": 1, "label": "Arsenal vs Chelsea"}}
    hit = monitor._match_final("Arsenal FC vs. Chelsea FC", finals)
    assert hit == {"home": 2, "away": 1, "label": "Arsenal vs Chelsea"}
    assert monitor._match_final("Lakers vs Celtics", finals) is None


def test_oddsapi_parses_spreads_and_totals():
    books = [{"markets": [
        {"key": "h2h", "outcomes": [{"name": "A", "price": 1.8},
                                    {"name": "B", "price": 2.1}]},
        {"key": "spreads", "outcomes": [{"name": "A", "price": 1.9, "point": -1.5},
                                        {"name": "B", "price": 1.95, "point": 1.5}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": 1.85, "point": 2.5},
                                       {"name": "Under", "price": 2.0, "point": 2.5}]},
    ]}]
    markets = TheOddsAPISource._parse_books(books, "A", "B")
    by_type = {m.market_type: m for m in markets}
    assert set(by_type) == {"moneyline", "handicap", "total"}
    assert by_type["handicap"].line == "-1.5"
    assert by_type["total"].line == "2.5"
    assert {o.name for o in by_type["total"].outcomes} == {"over", "under"}
