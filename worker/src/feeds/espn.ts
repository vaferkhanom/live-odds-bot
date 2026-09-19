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
      });
    }
    return events;
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
            finals[String(ev.id)] = { home: paired["home"]!, away: paired["away"]! };
          }
        } catch {
          continue;
        }
      }
    }
    return finals;
  }
}
