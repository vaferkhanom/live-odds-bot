/**
 * Monitor cycle. Port of bot/monitor.py adapted to Workers:
 * - No threads/sleep loop: one bounded cycle per cron invocation.
 * - In-cycle URL cache (feeds/http.ts) keeps subrequests under the free-plan limit.
 * - Same fuzzy fixture join, budgets, dedupe, dispatch, and settlement rules.
 */
import type { Settings } from "./config.ts";
import type { Http, LiveEvent, Source } from "./feeds/base.ts";
import { EspnSource } from "./feeds/espn.ts";
import { KalshiSource } from "./feeds/kalshi.ts";
import { PolymarketSource } from "./feeds/polymarket.ts";
import { allSports, isMonitored } from "./analysis/catalog.ts";
import { evaluateAll } from "./analysis/pipeline.ts";
import { settle } from "./settle.ts";
import { Store } from "./store.ts";
import { TelegramClient } from "./telegram.ts";
import { formatAlert, inQuietHours } from "./notify.ts";

export const SOURCES: Source[] = [new EspnSource(), new PolymarketSource(), new KalshiSource()];

/** Per-source call budget per cycle; exhausted meters degrade instead of dying. */
const CALL_BUDGET: Record<string, number> = { espn: 40, polymarket: 20, kalshi: 20 };

const MAX_CONCURRENT_FETCHES = 8;

/**
 * Hard bound on pushes per cycle: backstop against any future spam loop.
 * Capped picks are skipped entirely (not saved), so a later cycle retries.
 */
const MAX_ALERTS_PER_CYCLE = 5;

/** Per-sport fairness cap: no single sport may dominate a cycle's alerts. */
const MAX_ALERTS_PER_SPORT = 2;

/** True when a non-live event started too long ago to trust (stale feed). */
export function isStale(ev: LiveEvent, maxAgeHours = 6): boolean {
  if (ev.isLive || !ev.startsAt) return false;
  const kickoff = Date.parse(ev.startsAt);
  if (!Number.isFinite(kickoff)) return false;
  return Date.now() - kickoff > maxAgeHours * 3_600_000;
}

export const STOPWORDS = new Set(
  "vs v the fc cf sc ac united city real club de la le les at of and".split(" "),
);

/** Cities with multiple major teams: a bare city token must not join fixtures. */
export const CITY_TOKENS = new Set(
  "toronto london madrid milan manchester paris moscow istanbul los angeles new york mexico buenos aires rio sao paulo rome berlin".split(
    " ",
  ),
);

export function teamTokens(label: string): Set<string> {
  const toks = new Set<string>();
  for (const part of label.toLowerCase().replace("vs.", " ").replace("vs", " ").split(/\s+/)) {
    const word = part.replace(/[^a-z0-9]/g, "");
    if (word.length > 2 && !STOPWORDS.has(word)) toks.add(word);
  }
  return toks;
}

export function sameMatch(a: LiveEvent, b: LiveEvent): boolean {
  if (`${a.sport}|${a.matchLabel}`.toLowerCase() === `${b.sport}|${b.matchLabel}`.toLowerCase()) {
    return true;
  }
  const ta = teamTokens(a.matchLabel);
  const tb = teamTokens(b.matchLabel);
  if (ta.size === 0 || tb.size === 0) return false;
  const overlap = [...ta].filter((t) => tb.has(t));
  if (overlap.length >= 2) return true;
  if (overlap.length === 1) {
    const tok = overlap[0]!;
    // Single shared token only counts if distinctive, long, and not a
    // multi-team city (e.g. Toronto Maple Leafs vs Toronto Raptors).
    return tok.length >= 8 && !CITY_TOKENS.has(tok);
  }
  const la = a.matchLabel.toLowerCase();
  const lb = b.matchLabel.toLowerCase();
  return (la.length > 8 && lb.includes(la)) || (lb.length > 8 && la.includes(lb));
}

async function pool<T>(items: Array<() => Promise<T>>, limit: number): Promise<T[]> {
  const results: T[] = new Array(items.length);
  let next = 0;
  async function worker(): Promise<void> {
    while (next < items.length) {
      const i = next++;
      results[i] = await items[i]!();
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

export interface CycleStats {
  evaluated: number;
  alerted: number;
  discarded: number;
  degraded: string[];
  settled: number;
}

export async function runCycle(
  store: Store,
  tg: TelegramClient,
  cfg: Settings,
  http: Http,
): Promise<CycleStats> {
  const stats: CycleStats = { evaluated: 0, alerted: 0, discarded: 0, degraded: [], settled: 0 };
  const sportAlerts: Record<string, number> = {};
  const remaining: Record<string, number> = { ...CALL_BUDGET };

  const jobs: Array<() => Promise<{ name: string; sport: string; events: LiveEvent[] }>> = [];
  for (const sport of allSports()) {
    for (const src of SOURCES) {
      if ((remaining[src.name] ?? 0) <= 0) {
        if (!stats.degraded.includes(src.name)) stats.degraded.push(src.name);
        continue;
      }
      remaining[src.name]!--;
      jobs.push(async () => {
        try {
          return { name: src.name, sport, events: await src.fetchLive(sport, http) };
        } catch {
          return { name: src.name, sport, events: [] };
        }
      });
    }
  }
  const fetched = await pool(jobs, MAX_CONCURRENT_FETCHES);
  const perSport = new Map<string, Map<string, LiveEvent[]>>();
  for (const { name, sport, events } of fetched) {
    if (!perSport.has(sport)) perSport.set(sport, new Map());
    perSport.get(sport)!.set(name, events);
  }

  for (const sport of allSports()) {
    const perSource = perSport.get(sport) ?? new Map<string, LiveEvent[]>();
    const eventPool: LiveEvent[] = [];
    for (const src of SOURCES) {
      for (const ev of perSource.get(src.name) ?? []) {
        if (ev.markets.length === 0) continue;
        if (isStale(ev)) continue;
        ev.sourceName = src.name;
        eventPool.push(ev);
      }
    }
    const groups: LiveEvent[][] = [];
    for (const ev of eventPool) {
      let placed = false;
      for (const g of groups) {
        if (g.some((member) => sameMatch(ev, member))) {
          if (!g.some((m) => m.sourceName === ev.sourceName)) g.push(ev);
          placed = true;
          break;
        }
      }
      if (!placed) groups.push([ev]);
    }
    for (const members of groups) {
      if (members.length < 2) {
        const ev = members[0]!;
        await store.upsertGap(ev.sport, ev.markets[0]!.marketType, "single-source-only");
        stats.discarded++;
        continue;
      }
      const bySource = new Map(members.map((e) => [e.sourceName, e]));
      const ev = bySource.get("espn") ?? members[0]!;
      const corroborating = members.filter((e) => e !== ev);
      for (const m of ev.markets) {
        if (!isMonitored(ev.sport, m.marketType)) {
          await store.upsertGap(ev.sport, m.marketType, "not-in-catalog");
        }
      }
      if (!ev.markets.some((m) => isMonitored(ev.sport, m.marketType))) continue;
      stats.evaluated++;
      const sels = evaluateAll(ev, corroborating, cfg);
      if (sels.length === 0) {
        stats.discarded++;
        continue;
      }
      for (const sel of sels) {
        if (stats.alerted >= MAX_ALERTS_PER_CYCLE) {
          stats.discarded++;
          continue;
        }
        if ((sportAlerts[ev.sport] ?? 0) >= MAX_ALERTS_PER_SPORT) {
          stats.discarded++;
          continue;
        }
        const selId = await store.saveSelection(sel);
        if (!selId) continue; // duplicate open selection
        stats.alerted++;
        sportAlerts[ev.sport] = (sportAlerts[ev.sport] ?? 0) + 1;
        await dispatch(store, tg, { ...sel, id: selId, status: "open" });
      }
    }
  }
  stats.settled = await settleOpen(store, http);
  return stats;
}

/** Fuzzy-match a pick's fixture to a final score by label. */
export function matchFinal(
  matchLabel: string,
  finals: Record<string, { home: number; away: number; label?: string }>,
): { home: number; away: number } | null {
  const probe: LiveEvent = { eventId: "", sport: "", matchLabel, isLive: false, markets: [], rawScore: "" };
  for (const final of Object.values(finals)) {
    if (!final.label) continue;
    const other: LiveEvent = { eventId: "", sport: "", matchLabel: final.label, isLive: false, markets: [], rawScore: "" };
    if (sameMatch(probe, other)) return { home: final.home, away: final.away };
  }
  return null;
}

export async function settleOpen(store: Store, http: Http): Promise<number> {
  const espn = SOURCES.find((s) => s.name === "espn");
  if (!espn?.fetchFinals) return 0;
  let finals;
  try {
    finals = await espn.fetchFinals(http);
  } catch {
    return 0;
  }
  let settled = 0;
  for (const sel of await store.getOpen()) {
    const direct = finals[String(sel.event_id)];
    const final =
      direct ??
      matchFinal(sel.match_label, finals);
    if (!final) continue;
    const outcome = settle(
      { market_type: sel.market_type, outcome: sel.outcome, line: sel.line },
      final,
    );
    if (outcome === "won" || outcome === "lost" || outcome === "void") {
      await store.settle(sel.id, outcome);
      settled++;
    }
  }
  return settled;
}

export async function dispatch(
  store: Store,
  tg: TelegramClient,
  sel: { id: string; tier: string } & Parameters<typeof formatAlert>[0],
): Promise<void> {
  const text = formatAlert(sel);
  for (const sub of await store.subscribersForTier(sel.tier)) {
    if (await store.alertExists(sel.id, sub.chat_id)) continue;
    if (inQuietHours(sub)) {
      await store.recordAlert(sel.id, sub.chat_id, "queued");
      continue;
    }
    const ok = await tg.sendWithBackoff(sub.chat_id, text);
    await store.recordAlert(sel.id, sub.chat_id, ok ? "sent" : "failed");
  }
}
