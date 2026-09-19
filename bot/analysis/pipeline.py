"""Candidate evaluation pipeline: corroborate -> de-vig -> tier -> thesis."""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from .. import config
from ..feeds.base import LiveEvent
from . import odds as O

# Market families considered comparable across sources. Match-winner
# markets compare by position (outcome[0] = home side); every other type
# requires the exact same line, otherwise prices describe different bets.
WINNER_FAMILY = {"moneyline", "1x2"}


def _compatible(primary_type: str, primary_line, other_type: str, other_line) -> bool:
    if primary_type in WINNER_FAMILY and other_type in WINNER_FAMILY:
        return True
    return primary_type == other_type and (primary_line or None) == (other_line or None)


def _matching_events(corroborating: list[LiveEvent], market_type: str, line) -> list[LiveEvent]:
    """Corroborating events trimmed to markets describing the same bet."""
    out = []
    for ev in corroborating:
        mkts = [m for m in ev.markets
                if _compatible(market_type, line, m.market_type, m.line)]
        if mkts:
            out.append(replace(ev, markets=mkts))
    return out


def _best_price(events: list[LiveEvent]) -> tuple[float, float] | None:
    """Best (lowest decimal) price and max volume across sources for outcome 0.

    Zero-volume quotes priced at exactly 2.00 are unpriced 50/50
    placeholders (illiquid markets) and are ignored, so they can never
    manufacture a phantom edge.
    """
    best, vol = None, 0.0
    for ev in events:
        for mk in ev.markets:
            if not mk.outcomes:
                continue
            o = mk.outcomes[0]
            if o.decimal_odds and o.decimal_odds > 1:
                if o.volume == 0 and o.decimal_odds == 2.0:
                    continue
                if best is None or o.decimal_odds < best:
                    best = o.decimal_odds
                vol = max(vol, o.volume)
    return (best, vol) if best else None


def outcome_label(market_type: str, outcome: str, match_label: str) -> str:
    """Human-accurate side name, e.g. 'Win for Chelsea'.

    Team mapping applies only to match-winner markets; propositions
    (totals, halves, corners) keep their native Yes/No/over/under names
    so labels are never misleading.
    """
    teams = [t.strip(" .") for t in match_label.split(" vs ")]
    if market_type in ("moneyline", "1x2") and len(teams) == 2:
        if outcome == "home":
            return f"Win for {teams[0]}"
        if outcome == "away":
            return f"Win for {teams[1]}"
        if outcome == "draw":
            return "Draw"
        if outcome == "yes":
            return f"Yes — {teams[0]} win"
        if outcome == "no":
            return f"No — {teams[0]} win"
    return outcome


def evaluate(event: LiveEvent, corroborating: list[LiveEvent],
             enrichment: str = "") -> dict | None:
    """Evaluate one event. Returns a selection dict or None (discard)."""
    sels = evaluate_all(event, corroborating, config, enrichment)
    return sels[0] if sels else None


def evaluate_all(event: LiveEvent, corroborating: list[LiveEvent],
                 cfg, enrichment: str = "") -> list[dict]:
    """Evaluate every market on the event. Returns one selection per
    qualifying market (moneyline, totals, halves, handicaps, ...)."""
    if not event.markets:
        return []
    out: list[dict] = []
    for market in event.markets:
        matched = _matching_events(corroborating, market.market_type, market.line)
        sel = _evaluate_market(event, market, matched, cfg, enrichment)
        if sel:
            out.append(sel)
    return out


def _evaluate_market(event: LiveEvent, market, corroborating: list[LiveEvent],
                     cfg, enrichment: str = "") -> dict | None:
    quotes = [o.decimal_odds for o in market.outcomes if o.decimal_odds > 1]
    if len(quotes) < 2:
        return None
    fair = O.devig(quotes)
    idx = 0  # recommended side: first outcome (favorite legible side)
    fair_prob = fair[idx]

    others = _best_price(corroborating)
    if others is None:
        return None  # single-source outlier: discard, never alert
    best_decimal, volume = others
    if volume and volume < cfg.MIN_VOLUME:
        return None  # thin market: withhold

    ed = O.edge(fair_prob, best_decimal)
    if ed < cfg.VALUE_EDGE:
        return None
    tier = "obvious" if ed >= cfg.OBVIOUS_EDGE else "value"
    cap = cfg.STAKE_CAP_OBVIOUS if tier == "obvious" else cfg.STAKE_CAP_VALUE
    ev = O.expected_value(fair_prob, best_decimal)
    stake = O.kelly_lite(fair_prob, best_decimal, cap)

    outcome = market.outcomes[idx].name
    thesis_bits = [
        f"Fair {fair_prob:.1%} vs market {O.decimal_to_prob(best_decimal):.1%}",
        f"corroborated by {len(corroborating) + 1} sources",
    ]
    if enrichment:
        thesis_bits.append(enrichment)
    invalidation = (
        f"Void thesis if {outcome} drifts beyond {cfg.OBVIOUS_EDGE:.0%} "
        f"against, the market suspends, or team news breaks"
    )
    all_events = [event, *corroborating]
    return {
        "sport": event.sport,
        "match_label": event.match_label,
        "event_id": event.event_id,
        "market_type": market.market_type,
        "line": market.line,
        "outcome": outcome,
        "outcome_label": outcome_label(market.market_type, outcome, event.match_label),
        "odds_decimal": round(best_decimal, 2),
        "tier": tier,
        "edge": round(ed, 4),
        "ev": round(ev, 4),
        "stake_cap": stake,
        "thesis": "; ".join(thesis_bits),
        "invalidation": invalidation,
        "sources": sorted({getattr(e, "source_name", event.sport) for e in all_events}
                          | {"primary"}),
    }


def median_fair(decimals: list[float]) -> float:
    """Median fair probability helper exposed for tests/tools."""
    return median(O.devig(decimals))
