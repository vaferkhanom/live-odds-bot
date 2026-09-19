/**
 * Candidate evaluation pipeline. Port of bot/analysis/pipeline.py:
 * corroborate -> de-vig -> tier -> thesis. Same thresholds, same rules.
 */
import type { Settings } from "../config.ts";
import type { LiveEvent, MarketPrice } from "../feeds/base.ts";
import type { NewSelection } from "../store.ts";
import { decimalToProb, devig, edge, expectedValue, kellyLite } from "./odds.ts";

/** Market families comparable across sources. Winner markets compare by
 * position (outcome[0] = home side); all other types require the same line. */
const WINNER_FAMILY = new Set(["moneyline", "1x2"]);

export function compatible(primaryType: string, primaryLine: string | null, otherType: string, otherLine: string | null): boolean {
  if (WINNER_FAMILY.has(primaryType) && WINNER_FAMILY.has(otherType)) return true;
  return primaryType === otherType && (primaryLine ?? null) === (otherLine ?? null);
}

function matchingEvents(coroborating: LiveEvent[], marketType: string, line: string | null): LiveEvent[] {
  const out: LiveEvent[] = [];
  for (const ev of coroborating) {
    const mkts = ev.markets.filter((m) => compatible(marketType, line, m.marketType, m.line));
    if (mkts.length > 0) out.push({ ...ev, markets: mkts });
  }
  return out;
}

export function outcomeLabel(marketType: string, outcome: string, matchLabel: string): string {
  const teams = matchLabel.split(" vs ").map((t) => t.trim().replace(/\.+$/, ""));
  if ((marketType === "moneyline" || marketType === "1x2") && teams.length === 2) {
    if (outcome === "home") return `Win for ${teams[0]}`;
    if (outcome === "away") return `Win for ${teams[1]}`;
    if (outcome === "draw") return "Draw";
    if (outcome === "yes") return `Yes — ${teams[0]} win`;
    if (outcome === "no") return `No — ${teams[0]} win`;
  }
  return outcome;
}

function bestPrice(events: LiveEvent[]): { best: number; volume: number } | null {
  // Zero-volume quotes at exactly 2.00 are unpriced 50/50 placeholders
  // (illiquid markets) and are ignored. Mirrors bot/analysis/pipeline.py.
  let best: number | null = null;
  let vol = 0;
  for (const ev of events) {
    for (const mk of ev.markets) {
      if (mk.outcomes.length === 0) continue;
      const o = mk.outcomes[0]!;
      if (o.decimalOdds && o.decimalOdds > 1) {
        if (o.volume === 0 && o.decimalOdds === 2.0) continue;
        if (best === null || o.decimalOdds < best) best = o.decimalOdds;
        vol = Math.max(vol, o.volume);
      }
    }
  }
  return best === null ? null : { best, volume: vol };
}

export function evaluate(
  event: LiveEvent,
  corroborating: LiveEvent[],
  cfg: Settings,
  enrichment = "",
): NewSelection | null {
  const sels = evaluateAll(event, corroborating, cfg, enrichment);
  return sels.length > 0 ? sels[0]! : null;
}

export function evaluateAll(
  event: LiveEvent,
  corroborating: LiveEvent[],
  cfg: Settings,
  enrichment = "",
): NewSelection[] {
  if (event.markets.length === 0) return [];
  const out: NewSelection[] = [];
  for (const market of event.markets) {
    const matched = matchingEvents(corroborating, market.marketType, market.line);
    const sel = evaluateMarket(event, market, matched, cfg, enrichment);
    if (sel) out.push(sel);
  }
  return out;
}

function evaluateMarket(
  event: LiveEvent,
  market: MarketPrice,
  corroborating: LiveEvent[],
  cfg: Settings,
  enrichment = "",
): NewSelection | null {
  if (market.outcomes.length < 2) return null;
  const quotes = market.outcomes.filter((o) => o.decimalOdds > 1).map((o) => o.decimalOdds);
  if (quotes.length < 2) return null;
  const fair = devig(quotes);
  const idx = 0; // recommended side: first outcome (favorite legible side)
  const fairProb = fair[idx]!;

  const others = bestPrice(corroborating);
  if (!others) return null; // single-source outlier: discard, never alert
  if (others.volume && others.volume < cfg.minVolume) return null; // thin market: withhold

  const ed = edge(fairProb, others.best);
  if (ed < cfg.valueEdge) return null;
  const tier = ed >= cfg.obviousEdge ? "obvious" : "value";
  const cap = tier === "obvious" ? cfg.stakeCapObvious : cfg.stakeCapValue;
  const ev = expectedValue(fairProb, others.best);
  const stake = kellyLite(fairProb, others.best, cap);

  const outcome = market.outcomes[idx]!.name;  const thesisBits = [
    `Fair ${(fairProb * 100).toFixed(1)}% vs market ${(decimalToProb(others.best) * 100).toFixed(1)}%`,
    `corroborated by ${corroborating.length + 1} sources`,
  ];
  if (enrichment) thesisBits.push(enrichment);
  const invalidation =
    `Void thesis if ${outcome} drifts beyond ${Math.round(cfg.obviousEdge * 100)}% ` +
    `against, the market suspends, or team news breaks`;
  const sources = new Set<string>(["primary"]);
  for (const e of [event, ...corroborating]) sources.add(e.sourceName ?? e.sport);
  return {
    sport: event.sport,
    match_label: event.matchLabel,
    event_id: event.eventId,
    market_type: market.marketType,
    line: market.line,
    outcome,
    outcome_label: outcomeLabel(market.marketType, outcome, event.matchLabel),
    odds_decimal: Math.round(others.best * 100) / 100,
    tier,
    edge: Math.round(ed * 10000) / 10000,
    ev: Math.round(ev * 10000) / 10000,
    stake_cap: stake,
    thesis: thesisBits.join("; "),
    invalidation,
    sources: [...sources].sort(),
  };
}

export function medianFair(decimals: number[]): number {
  const fair = devig(decimals);
  const sorted = [...fair].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}
