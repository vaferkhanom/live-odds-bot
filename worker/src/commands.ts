/**
 * Command handlers. Same commands, texts, and auth gate as bot/commands.py,
 * adapted from PTB handlers to plain webhook functions.
 */
import type { Settings } from "./config.ts";
import { allSports, monitoredMarkets } from "./analysis/catalog.ts";
import { formatRecord, formatStats } from "./notify.ts";
import { Store } from "./store.ts";
import { TelegramClient } from "./telegram.ts";

export const REFUSAL = "⛔ This bot is private.";

export function isAuthorized(chatId: string | number, cfg: Settings): boolean {
  return cfg.authorizedChatIds.includes(String(chatId));
}

export interface CommandContext {
  store: Store;
  tg: TelegramClient;
  cfg: Settings;
  chatId: string;
  args: string[];
}

async function gate(ctx: CommandContext): Promise<Store | null> {
  if (!isAuthorized(ctx.chatId, ctx.cfg)) {
    await ctx.tg.sendMessage(ctx.chatId, REFUSAL);
    return null;
  }
  await ctx.store.ensureSubscription(ctx.chatId);
  return ctx.store;
}

export async function handleStart(ctx: CommandContext): Promise<void> {
  if (!(await gate(ctx))) return;
  await ctx.tg.sendMessage(
    ctx.chatId,
    "🤖 Live Odds Bot online.\n" +
      "I push 🟢 obvious edges and 🟡 value picks automatically.\n" +
      "Try /help, /opportunities, /stats, /record, /coverage.",
  );
}

export async function handleHelp(ctx: CommandContext): Promise<void> {
  if (!(await gate(ctx))) return;
  await ctx.tg.sendMessage(
    ctx.chatId,
    "/opportunities — current open picks\n" +
      "/stats [YYYY-MM-DD] — daily won/lost table\n" +
      "/record — all-time tally\n" +
      "/coverage — monitored markets + gaps\n" +
      "/subscribe [obvious|value|all] — enable alerts\n" +
      "/unsubscribe — disable push alerts\n" +
      "/settings — show your alert settings",
  );
}

export async function handleOpportunities(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  const open = await store.getOpen();
  if (open.length === 0) {
    await ctx.tg.sendMessage(ctx.chatId, "No open picks right now. I'll ping you when an edge appears.");
    return;
  }
  const badge = (t: string) => (t === "obvious" ? "🟢" : t === "value" ? "🟡" : "⚪");
  await ctx.tg.sendMessage(
    ctx.chatId,
    open.map((s) => `${badge(s.tier)} ${s.match_label} — ${s.market_type}: ${s.outcome_label || s.outcome} @ ${s.odds_decimal}`).join("\n"),
  );
}

export async function handleStats(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  await ctx.tg.sendMessage(ctx.chatId, formatStats(await store.statsForDay(ctx.args[0])));
}

export async function handleRecord(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  await ctx.tg.sendMessage(ctx.chatId, formatRecord(await store.recordAlltime()));
}

export async function handleCoverage(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  const lines = ["🗂 Monitored markets:"];
  for (const sport of allSports()) {
    lines.push(`• ${sport}: ${monitoredMarkets(sport).join(", ") || "—"}`);
  }
  const gaps = await store.getGaps();
  if (gaps.length > 0) {
    lines.push("\n⚠️ Coverage gaps (never alerted):");
    for (const g of gaps) lines.push(`• ${g.sport}: ${g.market_type} (${g.reason})`);
  }
  await ctx.tg.sendMessage(ctx.chatId, lines.join("\n"));
}

export async function handleSubscribe(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  const arg = (ctx.args[0] ?? "all").toLowerCase();
  const tiers = arg === "obvious" ? "obvious" : arg === "value" ? "value" : "obvious,value";
  await store.setSubscription(ctx.chatId, { subscribed: 1, tiers });
  await ctx.tg.sendMessage(ctx.chatId, `✅ Subscribed to: ${tiers}`);
}

export async function handleUnsubscribe(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  await store.setSubscription(ctx.chatId, { subscribed: 0 });
  await ctx.tg.sendMessage(ctx.chatId, "🔕 Unsubscribed. Pull commands still work.");
}

export async function handleSettings(ctx: CommandContext): Promise<void> {
  const store = await gate(ctx);
  if (!store) return;
  const sub = await store.getSubscription(ctx.chatId);
  await ctx.tg.sendMessage(
    ctx.chatId,
    `⚙️ subscribed=${sub?.subscribed === 1} tiers=${sub?.tiers} ` +
      `quiet=${sub?.quiet_start ?? "—"}–${sub?.quiet_end ?? "—"}`,
  );
}

export async function routeCommand(
  command: string,
  ctx: CommandContext,
): Promise<boolean> {
  switch (command) {
    case "start": await handleStart(ctx); return true;
    case "help": await handleHelp(ctx); return true;
    case "opportunities": await handleOpportunities(ctx); return true;
    case "stats": await handleStats(ctx); return true;
    case "record": await handleRecord(ctx); return true;
    case "coverage": await handleCoverage(ctx); return true;
    case "subscribe": await handleSubscribe(ctx); return true;
    case "unsubscribe": await handleUnsubscribe(ctx); return true;
    case "settings": await handleSettings(ctx); return true;
    default: return false;
  }
}
