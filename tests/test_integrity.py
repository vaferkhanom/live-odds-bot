import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot import monitor
from bot.analysis import pipeline
from bot.feeds.base import LiveEvent, MarketPrice, OutcomePrice
from bot.store import Store


def _mk(market_type="moneyline", outcomes=None):
    return MarketPrice(market_type, None, outcomes or [
        OutcomePrice("home", 1.8), OutcomePrice("away", 2.2)])


def _board_event(eid, home, away, state):
    return {
        "id": eid,
        "status": {"type": {"state": state}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ],
            "odds": [{
                "homeAway": {
                    "home": {"summary": "-150"},
                    "away": {"summary": "+130"},
                },
            }],
        }],
    }


def test_espn_skips_finished_games():
    from bot.feeds.espn import EspnSource
    board = {"events": [
        _board_event("1", "A", "B", "post"),
        _board_event("2", "C", "D", "pre"),
    ]}
    with patch("bot.feeds.espn.get_json", return_value=board):
        evs = EspnSource().fetch_live("nba")
    assert [e.event_id for e in evs] == ["2"]


def test_best_price_ignores_zero_volume_placeholder():
    phantom = LiveEvent("p", "mlb", "A vs B", True,
                        [MarketPrice("moneyline", None,
                                     [OutcomePrice("home", 2.0, 0.0),
                                      OutcomePrice("away", 2.0, 0.0)])])
    assert pipeline._best_price([phantom]) is None


def test_best_price_keeps_real_two_dollar_price():
    real = LiveEvent("p", "mlb", "A vs B", True,
                     [MarketPrice("moneyline", None,
                                  [OutcomePrice("home", 2.0, 5000.0),
                                   OutcomePrice("away", 2.0, 5000.0)])])
    best, vol = pipeline._best_price([real])
    assert best == 2.0 and vol == 5000.0


def test_repair_voids_instant_settlements_only():
    store = Store(path=tempfile.mktemp(suffix=".db"))
    now = datetime.now(timezone.utc)
    fast = store.save_selection({
        "sport": "mlb", "match_label": "A vs B", "event_id": "f1",
        "market_type": "moneyline", "line": None, "outcome": "home",
        "odds_decimal": 2.0, "tier": "obvious", "edge": 0.18, "ev": 0.36,
        "stake_cap": 0.18, "thesis": "t", "invalidation": "i",
        "sources": ["a", "b"]})
    store.conn.execute(
        "UPDATE selections SET detected_at=?, settled_at=?, status='won' WHERE id=?",
        ((now - timedelta(seconds=30)).isoformat(), now.isoformat(), fast))
    slow = store.save_selection({
        "sport": "mlb", "match_label": "C vs D", "event_id": "f2",
        "market_type": "moneyline", "line": None, "outcome": "home",
        "odds_decimal": 2.0, "tier": "value", "edge": 0.04, "ev": 0.08,
        "stake_cap": 0.04, "thesis": "t", "invalidation": "i",
        "sources": ["a", "b"]})
    store.conn.execute(
        "UPDATE selections SET detected_at=?, settled_at=?, status='won' WHERE id=?",
        ((now - timedelta(hours=5)).isoformat(), now.isoformat(), slow))
    store.conn.commit()
    assert store.repair_instant_settlements() == 1
    assert store.get_selection(fast)["status"] == "void"
    assert store.get_selection(slow)["status"] == "won"


def test_alert_cap_bounds_cycle_spam():
    store = Store(path=tempfile.mktemp(suffix=".db"))
    store.ensure_subscription("111")

    def make_events(tag):
        evs = []
        for i in range(10):
            evs.append(LiveEvent(f"cap-{i}", "nba", f"Team{i} vs Opp{i}", True,
                                 [MarketPrice("moneyline", None,
                                              [OutcomePrice("home", 1.7),
                                               OutcomePrice("away", 2.5)])]))
        return evs

    class StubSource:
        def __init__(self, name):
            self.name = name

        def fetch_live(self, sport):
            if sport != "nba":
                return []
            return make_events(self.name)

    with patch.object(monitor, "SOURCES", [StubSource("espn"), StubSource("poly")]), \
         patch.object(monitor.catalog, "all_sports", return_value=["nba"]), \
         patch.object(monitor.catalog, "is_monitored", return_value=True):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)
        stats = asyncio.run(monitor.run_cycle(store, bot))
    assert stats["alerted"] <= monitor.MAX_ALERTS_PER_CYCLE
    assert bot.send_message.await_count <= monitor.MAX_ALERTS_PER_CYCLE * 1
