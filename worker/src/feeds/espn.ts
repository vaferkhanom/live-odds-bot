/** ESPN scoreboard client (keyless). Mirrors bot/feeds/espn.py. */
import { ESPN_UA } from "../config.ts";
import type { FinalsMap, Http, LiveEvent, MarketPrice, OutcomePrice, Source } from "./base.ts";

const BASE = "https://site.api.espn.com/apis/site/v2/sports";

export const SPORT_PATHS: Record<string, string> = {
  "soccer-epl": "soccer/eng.1",
  "soccer-laliga": "soccer/esp.1",
  "soccer-bundesliga": "soccer/ger.1",
  "soccer-seriea": "soccer/ita.1",
  "soccer-ligue1": "soccer/fra.1",
  "soccer-ucl": "soccer/uefa.champions",
  nba: "basketball/nba",
  nfl: "football/nfl",
  mlb: "baseball/mlb",
  nhl: "hockey/nhl",
  "tennis-atp": "tennis/atp",
};

export function americanToDecimal(odds: number): number {
  if (odds === null || odds === undefined || Number.isNaN(Number(odds))) {
    throw new Error("missing odds");
  }
  const o = Number(odds);
  if (o > 0) return 1 + o / 100;
  return 1 + 100 / Math.abs(o);
}

interface EspnScoreboard {
  events?: EspnEvent[];
}
interface EspnEvent {
  id?: string | number;
  name?: string;
  date?: string;
  status?: { type?: { state?: string } };
  competitions?: EspnCompetition[];
}
interface EspnCompetition {
  competitors?: Array<{ homeAway?: string; score?: string; team?: { displayName?: string } }>;
  odds?: EspnOdds[];
}
interface EspnOdds {
  details?: string;
  overUnder?: number;
  homeAway?: Record<string, { summary?: string; odds?: string | number }>;
  homeaway?: Record<string, { summary?: string; odds?: string | number }>;
  draw?: { summary?: string; odds?: string | number } | string | number;
}

function parseMarket(odds: EspnOdds): MarketPrice | null {
  const outcomes: OutcomePrice[] = [];
  const homeAway = odds.homeAway ?? odds.homeaway;
  if (homeAway) {
    for (const side of ["home", "away"]) {
      const val = homeAway[side] ?? {};
      const summary = val.summary ?? val.odds;
      if (summary !== undefined && summary !== null && summary !== "") {
        try {
          outcomes.push({ name: side, decimalOdds: americanToDecimal(Number(summary)), volume: 0 });
        } catch {
          /* skip unparseable leg */
        }
      }
    }
  }
  const draw = odds.draw;
  const drawVal =
    draw !== null && typeof draw === "object" ? (draw.summary ?? draw.odds) : (draw as string | number | undefined);
  if (drawVal !== undefined && drawVal !== null && drawVal !== "") {
    try {
      outcomes.push({ name: "draw", decimalOdds: americanToDecimal(Number(drawVal)), volume: 0 });
    } catch {
      /* skip unparseable leg */
    }
  }
  if (outcomes.length < 2) return null;
  let marketType = outcomes.some((o) => o.name === "draw") ? "1x2" : "moneyline";
  const ou = odds.overUnder;
  const line = ou !== undefined && ou !== null ? String(ou) : null;
  if (line !== null) marketType = "total";
  return { marketType, line, outcomes };
}

const SUMMARY_CAP = 15;

interface EspnSummaryOdds {
  overUnder?: number;
  spread?: number | string;
  overOdds?: number | string;
  underOdds?: number | string;
  homeTeamOdds?: Record<string, unknown>;
  awayTeamOdds?: Record<string, unknown>;
  drawOdds?: Record<string, unknown>;
}

interface EspnSummary {
  pickcenter?: EspnSummaryOdds[];
  odds?: EspnSummaryOdds[];
}

function parseSummaryOdds(odds: EspnSummaryOdds): MarketPrice[] {
  const found: MarketPrice[] = [];
  const outcomes: OutcomePrice[] = [];
  const homeML = explicitMoneyLine(odds.homeTeamOdds);
  const awayML = explicitMoneyLine(odds.awayTeamOdds);
  const drawML = explicitMoneyLine(odds.drawOdds);
  if (homeML !== null) outcomes.push({ name: "home", decimalOdds: americanToDecimal(homeML), volume: 0 });
  if (awayML !== null) outcomes.push({ name: "away", decimalOdds: americanToDecimal(awayML), volume: 0 });
  if (drawML !== null) outcomes.push({ name: "draw", decimalOdds: americanToDecimal(drawML), volume: 0 });
  if (outcomes.length >= 2) {
    found.push({
      marketType: drawML !== null ? "1x2" : "moneyline",
      line: null,
      outcomes,
    });
  }
  const ou = odds.overUnder;
  const over = odds.overOdds !== undefined && odds.overOdds !== null && odds.overOdds !== "" ? Number(odds.overOdds) : null;
  const under = odds.underOdds !== undefined && odds.underOdds !== null && odds.underOdds !== "" ? Number(odds.underOdds) : null;
  if (ou !== undefined && ou !== null && over !== null && under !== null && Number.isFinite(over) && Number.isFinite(under)) {
    found.push({
      marketType: "total",
      line: String(ou),
      outcomes: [
        { name: "over", decimalOdds: americanToDecimal(over), volume: 0 },
        { name: "under", decimalOdds: americanToDecimal(under), volume: 0 },
      ],
    });
  }
  const spread = odds.spread;
  const homeSpread = spreadValue(odds.homeTeamOdds);
  const awaySpread = spreadValue(odds.awayTeamOdds);
  if (spread !== undefined && spread !== null && homeSpread !== null && awaySpread !== null) {
    found.push({
      marketType: "handicap",
      line: String(spread),
      outcomes: [
        { name: "home", decimalOdds: americanToDecimal(homeSpread), volume: 0 },
        { name: "away", decimalOdds: americanToDecimal(awaySpread), volume: 0 },
      ],
    });
  }
  return found;
}

function explicitMoneyLine(node: unknown): number | null {
  if (!node || typeof node !== "object") return null;
  const rec = node as Record<string, unknown>;
  for (const key of ["moneyLine", "moneyline"]) {
    const v = rec[key];
    if (v !== undefined && v !== null && v !== "") {
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function spreadValue(node: unknown): number | null {
  if (!node || typeof node !== "object") return null;
  const v = (node as Record<string, unknown>)["spreadOdds"];
  if (v === undefined || v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export class EspnSource implements Source {
  readonly name = "espn";
  async fetchLive(sport: string, http: Http): Promise<LiveEvent[]> {
    const path = SPORT_PATHS[sport];
    if (!path) return [];
    let data: EspnScoreboard;
    try {
      data = await http.getJson<EspnScoreboard>(`${BASE}/${path}/scoreboard`, undefined, ESPN_UA);
    } catch {
      return [];
    }
    const events: LiveEvent[] = [];
    for (const ev of data.events ?? []) {
      // Finished games must never be evaluated (stale odds + instant
      // settlement = bogus picks + spam loop). Mirrors bot/feeds/espn.py.
      if (ev.status?.type?.state === "post") continue;
      const comp = (ev.competitions ?? [{}])[0]!;
      const competitors = comp.competitors ?? [];
      const names = competitors.map((c) => c.team?.displayName ?? "?");
      const label = names.length > 0 ? names.join(" vs ") : (ev.name ?? "?");
      const state = ev.status?.type?.state ?? "";
      const markets: MarketPrice[] = [];
      for (const odd of comp.odds ?? []) {
        try {
          const mk = parseMarket(odd);
          if (mk) markets.push(mk);
        } catch {
          continue;
        }
      }
      events.push({
        eventId: String(ev.id ?? ""),
        sport,
        matchLabel: label,
        isLive: state === "in",
        markets,
        rawScore: "",
        startsAt: ev.date ?? null,
      });
    }
    // Scoreboards often omit odds; the per-event summary endpoint still
    // carries book prices. Backfill live events first, then pre-match
    // events starting within the next 36h. Games that started more than
    // 3h ago are stale (played yesterday) and are never backfilled.
    const now = Date.now();
    const pending: LiveEvent[] = [];
    const upcoming: LiveEvent[] = [];
    for (const ev of events) {
      if (ev.markets.length > 0) continue;
      if (ev.isLive) {
        pending.unshift(ev);
        continue;
      }
      const raw = data.events?.find((e) => String(e.id ?? "") === ev.eventId)?.date;
      if (!raw) continue;
      const kickoff = Date.parse(raw);
      if (!Number.isFinite(kickoff)) continue;
      const deltaH = (kickoff - now) / 3_600_000;
      if (deltaH >= -3 && deltaH <= 36) upcoming.push(ev);
    }
    const targets = [...pending, ...upcoming].slice(0, SUMMARY_CAP);
    await Promise.all(
      targets.map(async (ev) => {
        const extra = await this.summaryMarkets(path, ev.eventId, http);
        if (extra.length > 0) ev.markets.push(...extra);
      }),
    );
    return events;
  }

  async summaryMarkets(path: string, eventId: string, http: Http): Promise<MarketPrice[]> {
    let data: EspnSummary;
    try {
      data = await http.getJson<EspnSummary>(`${BASE}/${path}/summary`, { event: eventId }, ESPN_UA);
    } catch {
      return [];
    }
    const out: MarketPrice[] = [];
    for (const odd of data.pickcenter ?? data.odds ?? []) {
      try {
        out.push(...parseSummaryOdds(odd));
      } catch {
        continue;
      }
    }
    return out;
  }

  async fetchFinals(http: Http): Promise<FinalsMap> {
    const finals: FinalsMap = {};
    for (const path of Object.values(SPORT_PATHS)) {
      let data: EspnScoreboard;
      try {
        data = await http.getJson<EspnScoreboard>(`${BASE}/${path}/scoreboard`, undefined, ESPN_UA);
      } catch {
        continue;
      }
      for (const ev of data.events ?? []) {
        const comp = (ev.competitions ?? [{}])[0]!;
        if (ev.status?.type?.state !== "post") continue;
        try {
          const paired: Record<string, number> = {};
          for (const c of comp.competitors ?? []) {
            if (c.homeAway) paired[c.homeAway] = parseInt(c.score ?? "", 10);
          }
          if (Number.isFinite(paired["home"]) && Number.isFinite(paired["away"])) {
            const competitors = comp.competitors ?? [];
            const names = competitors.map((c) => c.team?.displayName ?? "?");
            finals[String(ev.id)] = {
              home: paired["home"]!,
              away: paired["away"]!,
              label: names.length > 0 ? names.join(" vs ") : (ev.name ?? "?"),
            };
          }
        } catch {
          continue;
        }
      }
    }
    return finals;
  }
}
