from bot.feeds.espn import EspnSource


def _dk_odds():
    return {
        "details": "BRE +155",
        "overUnder": 3.5,
        "overOdds": 130.0,
        "underOdds": -160.0,
        "homeTeamOdds": {"moneyLine": 155},
        "awayTeamOdds": {"moneyLine": 160, "favorite": False},
        "drawOdds": {"moneyLine": 265},
    }


def test_summary_odds_yields_moneyline_and_total():
    src = EspnSource()
    markets = src._parse_summary_odds(_dk_odds())
    by_type = {m.market_type: m for m in markets}
    assert set(by_type) == {"1x2", "total"}
    ml = {o.name: o.decimal_odds for o in by_type["1x2"].outcomes}
    assert abs(ml["home"] - 2.55) < 0.01
    assert abs(ml["away"] - 2.60) < 0.01
    total = {o.name: o.decimal_odds for o in by_type["total"].outcomes}
    assert by_type["total"].line == "3.5"
    assert abs(total["over"] - 2.30) < 0.01
    assert abs(total["under"] - 1.625) < 0.01


def test_summary_odds_empty_without_legs():
    src = EspnSource()
    assert src._parse_summary_odds({}) == []
    assert src._parse_summary_odds({"overUnder": 2.5}) == []
