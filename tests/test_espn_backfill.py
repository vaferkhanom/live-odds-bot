from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot.feeds.espn import EspnSource


def _scoreboard(events):
    return {"events": events}


def _ev(eid, label, date, state="pre", odds=None):
    return {"id": eid, "name": label, "date": date,
            "status": {"type": {"state": state}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": label.split(" vs ")[0]}},
                {"homeAway": "away", "team": {"displayName": label.split(" vs ")[1]}}],
                "odds": odds or []}]}


def test_prematch_within_36h_gets_summary_backfill():
    src = EspnSource()
    soon = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    board = _scoreboard([_ev("1", "A vs B", soon)])
    summary = {"pickcenter": [{"homeTeamOdds": {"moneyLine": -150},
                               "awayTeamOdds": {"moneyLine": 130}}]}

    def fake_get(url, **kwargs):
        if "summary" in url:
            return summary
        return board

    with patch("bot.feeds.espn.get_json", side_effect=fake_get):
        evs = src.fetch_live("soccer-epl")
    assert len(evs) == 1 and len(evs[0].markets) == 1
    assert evs[0].markets[0].market_type == "moneyline"


def test_far_future_prematch_skipped():
    src = EspnSource()
    later = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    board = _scoreboard([_ev("1", "A vs B", later)])

    def fake_get(url, **kwargs):
        if "summary" in url:
            raise AssertionError("summary must not be fetched for far-future games")
        return board

    with patch("bot.feeds.espn.get_json", side_effect=fake_get):
        evs = src.fetch_live("soccer-epl")
    assert evs[0].markets == []
