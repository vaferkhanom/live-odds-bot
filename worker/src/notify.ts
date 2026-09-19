/** Alert formatting + quiet hours. Port of bot/notify.py (message text identical). */
import { tehranClock } from "./time.ts";

export const TIER_EMOJI: Record<string, string> = { obvious: "🟢", value: "🟡" };
export const OUTCOME_EMOJI: Record<string, string> = {
  won: "✅",
  lost: "❌",
  void: "➖",
  pending: "⏳",
  open: "🔵",
};

export interface SelectionLike {
  sport: string;
  match_label: string;
  market_type: string;
  line: string | null;
  outcome: string;
  outcome_label?: string | null;
  odds_decimal: number;
  tier: string;
  edge: number;
  ev: number;
  stake_cap: number;
  thesis: string;
  invalidation: string;
  status: string;
}

function side(sel: SelectionLike): string {
  return sel.outcome_label || sel.outcome;
}

export function formatAlert(sel: SelectionLike): string {
  const badge = TIER_EMOJI[sel.tier] ?? "⚪";
  const tierName = sel.tier === "obvious" ? "OBVIOUS EDGE" : "VALUE PICK";
  const line = sel.line ? ` — Line ${sel.line}` : "";
  return (
    `${badge} ${tierName} — ${sel.sport}\n` +
    `${sel.match_label} — ${sel.market_type}${line}: ${side(sel)} @ ${sel.odds_decimal}\n` +
    `Edge ${(sel.edge * 100).toFixed(1)}% · EV ${sel.ev >= 0 ? "+" : ""}${sel.ev.toFixed(2)}u · Cap ${sel.stake_cap}u\n` +
    `Thesis: ${sel.thesis}\n` +
    `Invalid if: ${sel.invalidation}`
  );
}

export interface QuietSub {
  quiet_start: string | null;
  quiet_end: string | null;
}

export function inQuietHours(sub: QuietSub, now?: Date): boolean {
  const qs = sub.quiet_start;
  const qe = sub.quiet_end;
  if (!qs || !qe) return false;
  const cur = tehranClock(now);
  if (qs <= qe) return qs <= cur && cur < qe;
  return cur >= qs || cur < qe; // overnight window
}

export interface StatsReport {
  day: string;
  picks: SelectionLike[];
  counts: Record<string, number>;
}

export function formatStats(report: StatsReport): string {
  const lines = [`📊 Selections for ${report.day} (Asia/Tehran)`];
  if (report.picks.length === 0) return lines[0] + "\nNo selections today.";
  for (const sel of report.picks) {
    const emo = OUTCOME_EMOJI[sel.status] ?? "❔";
    const badge = TIER_EMOJI[sel.tier] ?? "⚪";
    lines.push(
      `${emo} ${badge} ${sel.match_label} — ${sel.market_type}: ${side(sel)} @ ${sel.odds_decimal} (${sel.status})`,
    );
  }
  const c = report.counts;
  lines.push(
    `\nTally: ✅ ${c["won"] ?? 0} · ❌ ${c["lost"] ?? 0} · ➖ ${c["void"] ?? 0} · ⏳ ${(c["pending"] ?? 0) + (c["open"] ?? 0)}`,
  );
  return lines.join("\n");
}

export function formatRecord(rows: Array<{ tier: string; status: string; count: number }>): string {
  if (rows.length === 0) return "📈 No settled selections yet.";
  const lines = ["📈 All-time record (settled only)"];
  for (const r of rows) lines.push(`${r.tier}: ${r.status} × ${r.count}`);
  return lines.join("\n");
}
