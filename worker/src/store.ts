/**
 * D1 persistence. Same tables, columns, and method semantics as bot/store.py.
 * D1 is SQLite-compatible, so the schema is unchanged (see migrations/).
 */
import { tehranDay, tehranDayOf, utcNowIso } from "./time.ts";

export interface Selection {
  id: string;
  sport: string;
  match_label: string;
  event_id: string;
  market_type: string;
  line: string | null;
  outcome: string;
  outcome_label: string | null;
  odds_decimal: number;
  tier: string;
  edge: number;
  ev: number;
  stake_cap: number;
  thesis: string;
  invalidation: string;
  sources: string;
  status: string;
  detected_at: string;
  settled_at: string | null;
}

export interface NewSelection extends Omit<Selection, "id" | "status" | "detected_at" | "settled_at" | "sources"> {
  sources: string[];
}

export interface Alert {
  id: string;
  selection_id: string;
  chat_id: string;
  state: string;
  sent_at: string | null;
}

export interface Subscription {
  chat_id: string;
  subscribed: number;
  tiers: string;
  quiet_start: string | null;
  quiet_end: string | null;
}

export interface Gap {
  sport: string;
  market_type: string;
  reason: string;
  first_seen: string;
  last_seen: string;
}

export type SettleStatus = "won" | "lost" | "void" | "pending";

export class Store {
  private db: D1Database;

  constructor(db: D1Database) {
    this.db = db;
  }

  async saveSelection(sel: NewSelection): Promise<string | null> {
    const dup = await this.db
      .prepare(
        "SELECT id FROM selections WHERE event_id=? AND market_type=? " +
          "AND COALESCE(line,'')=COALESCE(?,'') AND outcome=? AND status='open'",
      )
      .bind(sel.event_id, sel.market_type, sel.line, sel.outcome)
      .first<{ id: string }>();
    if (dup) return null;
    const id = crypto.randomUUID();
    await this.db
      .prepare(
        "INSERT INTO selections (id,sport,match_label,event_id,market_type,line," +
          "outcome,outcome_label,odds_decimal,tier,edge,ev,stake_cap,thesis,invalidation,sources," +
          "status,detected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)",
      )
      .bind(
        id, sel.sport, sel.match_label, sel.event_id, sel.market_type, sel.line,
        sel.outcome, sel.outcome_label ?? null, sel.odds_decimal, sel.tier, sel.edge, sel.ev, sel.stake_cap,
        sel.thesis, sel.invalidation, sel.sources.join(","), utcNowIso(),
      )
      .run();
    return id;
  }

  async getSelection(id: string): Promise<Selection | null> {
    return this.db.prepare("SELECT * FROM selections WHERE id=?").bind(id).first<Selection>();
  }

  async getOpen(): Promise<Selection[]> {
    const res = await this.db
      .prepare("SELECT * FROM selections WHERE status='open' ORDER BY detected_at")
      .all<Selection>();
    return res.results ?? [];
  }

  async settle(id: string, status: SettleStatus): Promise<void> {
    await this.db
      .prepare("UPDATE selections SET status=?, settled_at=? WHERE id=?")
      .bind(status, utcNowIso(), id)
      .run();
  }

  async recordAlert(selectionId: string, chatId: string, state = "sent"): Promise<void> {
    await this.db
      .prepare("INSERT INTO alerts (id,selection_id,chat_id,state,sent_at) VALUES (?,?,?,?,?)")
      .bind(crypto.randomUUID(), selectionId, chatId, state, state === "sent" ? utcNowIso() : null)
      .run();
  }

  async alertExists(selectionId: string, chatId: string): Promise<boolean> {
    const row = await this.db
      .prepare("SELECT 1 AS one FROM alerts WHERE selection_id=? AND chat_id=? AND state='sent'")
      .bind(selectionId, chatId)
      .first<{ one: number }>();
    return row !== null;
  }

  async ensureSubscription(chatId: string): Promise<Subscription> {
    await this.db
      .prepare("INSERT OR IGNORE INTO subscriptions (chat_id) VALUES (?)")
      .bind(chatId)
      .run();
    return (await this.getSubscription(chatId))!;
  }

  async getSubscription(chatId: string): Promise<Subscription | null> {
    return this.db
      .prepare("SELECT * FROM subscriptions WHERE chat_id=?")
      .bind(chatId)
      .first<Subscription>();
  }

  async setSubscription(
    chatId: string,
    fields: Partial<Pick<Subscription, "subscribed" | "tiers" | "quiet_start" | "quiet_end">>,
  ): Promise<void> {
    await this.ensureSubscription(chatId);
    const allowed = ["subscribed", "tiers", "quiet_start", "quiet_end"] as const;
    for (const key of allowed) {
      const value = fields[key];
      if (value !== undefined) {
        await this.db
          .prepare(`UPDATE subscriptions SET ${key}=? WHERE chat_id=?`)
          .bind(value, chatId)
          .run();
      }
    }
  }

  async subscribersForTier(tier: string): Promise<Subscription[]> {
    const res = await this.db
      .prepare("SELECT * FROM subscriptions WHERE subscribed=1")
      .all<Subscription>();
    return (res.results ?? []).filter((s) => (s.tiers || "").split(",").includes(tier));
  }

  async upsertGap(sport: string, marketType: string, reason: string): Promise<void> {
    const now = utcNowIso();
    await this.db
      .prepare(
        "INSERT INTO gaps (sport,market_type,reason,first_seen,last_seen) " +
          "VALUES (?,?,?,?,?) ON CONFLICT(sport,market_type) DO UPDATE SET " +
          "last_seen=excluded.last_seen, reason=excluded.reason",
      )
      .bind(sport, marketType, reason, now, now)
      .run();
  }

  async getGaps(): Promise<Gap[]> {
    const res = await this.db
      .prepare("SELECT * FROM gaps ORDER BY sport, market_type")
      .all<Gap>();
    return res.results ?? [];
  }

  async statsForDay(day?: string): Promise<{ day: string; picks: Selection[]; counts: Record<string, number> }> {
    const target = day || tehranDay();
    const res = await this.db.prepare("SELECT * FROM selections ORDER BY detected_at").all<Selection>();
    const picks: Selection[] = [];
    const counts: Record<string, number> = { won: 0, lost: 0, void: 0, pending: 0, open: 0 };
    for (const sel of res.results ?? []) {
      if (tehranDayOf(sel.detected_at) !== target) continue;
      picks.push(sel);
      counts[sel.status] = (counts[sel.status] ?? 0) + 1;
    }
    return { day: target, picks, counts };
  }

  async recordAlltime(): Promise<Array<{ tier: string; status: string; count: number }>> {
    const res = await this.db
      .prepare("SELECT tier, status, COUNT(*) AS count FROM selections GROUP BY tier, status")
      .all<{ tier: string; status: string; count: number }>();
    return res.results ?? [];
  }
}
