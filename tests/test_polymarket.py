from bot.feeds.base import LiveEvent, MarketPrice, OutcomePrice
from bot.feeds.polymarket import SERIES, PolymarketSource
from bot.monitor import same_match


def _ev(label, markets=None):
    return LiveEvent("1", "soccer-epl", label, False, markets or [])


def test_series_map_covers_all_sports():
    for sport in ["soccer-epl", "soccer-laliga", "soccer-bundesliga",
                  "soccer-seriea", "soccer-ligue1", "nba", "nfl", "mlb", "nhl"]:
        assert SERIES.get(sport), f"no polymarket series for {sport}"


def test_poly_fixture_titles_join_espn_labels():
    espn = _ev("Tottenham Hotspur vs Aston Villa")
    poly = _ev("Tottenham Hotspur FC vs. Aston Villa FC")
    assert same_match(espn, poly) is True


def test_poly_derivative_markets_still_join():
    espn = _ev("Tottenham Hotspur vs Aston Villa")
    assert same_match(espn, _ev("Tottenham Hotspur FC vs. Aston Villa FC - Halftime Result")) is True


def test_cross_league_teams_do_not_join():
    assert same_match(_ev("Arsenal vs Chelsea"), _ev("Arsenal vs Barcelona")) is False
