"""Candidate evaluation pipeline: corroborate -> de-vig -> tier -> thesis."""
from __future__ import annotations

from statistics import median

from .. import config
from ..feeds.base import LiveEvent
from . import odds as O


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


def evaluate(event: LiveEvent, corroborating: list[LiveEvent],
             enrichment: str = "") -> dict | None:
    """Evaluate one event. Returns a selection dict or None (discard)."""
    if not event.markets:
        return None
    market = event.markets[0]
    if len(market.outcomes) < 2:
        return None
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
    if volume and volume < config.MIN_VOLUME:
        return None  # thin market: withhold

    ed = O.edge(fair_prob, best_decimal)
    if ed < config.VALUE_EDGE:
        return None
    tier = "obvious" if ed >= config.OBVIOUS_EDGE else "value"
    cap = config.STAKE_CAP_OBVIOUS if tier == "obvious" else config.STAKE_CAP_VALUE
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
        f"Void thesis if {outcome} drifts beyond {config.OBVIOUS_EDGE:.0%} "
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
