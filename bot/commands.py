"""Command handlers. Auth-gated: only AUTHORIZED_CHAT_IDS may operate the bot."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from . import config
from .analysis import markets as catalog
from .feeds.oddsapi import PROVIDER as ODDS_PROVIDER, OddsAPIExhausted, TheOddsAPISource
from .notify import format_api_status, format_record, format_stats, format_status, mask_key
from .store import Store

REFUSAL = "⛔ This bot is private."


def _authorized(chat_id: str | int) -> bool:
    return str(chat_id) in config.AUTHORIZED_CHAT_IDS


async def _gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Store | None:
    chat_id = update.effective_chat.id
    if not _authorized(chat_id):
        await update.message.reply_text(REFUSAL)
        return None
    store: Store = ctx.application.bot_data["store"]
    store.ensure_subscription(str(chat_id))
    return store


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    await update.message.reply_text(
        "🤖 Live Odds Bot online.\n"
        "I push 🟢 obvious edges and 🟡 value picks automatically.\n"
        "Try /help, /opportunities, /stats, /status, /api status.")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    await update.message.reply_text(
        "/opportunities — current open picks\n"
        "/stats [YYYY-MM-DD] — daily won/lost table\n"
        "/record — all-time tally\n"
        "/status — live status: open picks + won/lost right now\n"
        "/coverage — monitored markets + gaps\n"
        "/subscribe [obvious|value|all] — enable alerts\n"
        "/unsubscribe — disable push alerts\n"
        "/api set <key> | status | clear — priority API key\n"
        "/settings — show your alert settings")


async def opportunities(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from .notify import TIER_EMOJI
    store = await _gate(update, ctx)
    if not store:
        return
    open_picks = store.get_open()
    if not open_picks:
        await update.message.reply_text("No open picks right now. I'll ping you when an edge appears.")
        return
    lines = []
    for sel in open_picks:
        badge = TIER_EMOJI.get(sel["tier"], "⚪")
        lines.append(f"{badge} {sel['match_label']} — {sel['market_type']}: "
                     f"{sel['outcome']} @ {sel['odds_decimal']}")
    await update.message.reply_text("\n".join(lines))


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    day = ctx.args[0] if ctx.args else None
    await update.message.reply_text(format_stats(store.stats_for_day(day)))


async def record(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    await update.message.reply_text(format_record(store.record_alltime()))


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Live snapshot: open picks right now + today's won/lost tally."""
    store = await _gate(update, ctx)
    if not store:
        return
    report = store.stats_for_day()
    await update.message.reply_text(
        format_status(store.get_open(), report["counts"], report["day"]))


async def coverage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    lines = ["🗂 Monitored markets:"]
    for sport in catalog.all_sports():
        lines.append(f"• {sport}: {', '.join(catalog.monitored_markets(sport)) or '—'}")
    gaps = store.get_gaps()
    if gaps:
        lines.append("\n⚠️ Coverage gaps (never alerted):")
        lines += [f"• {g['sport']}: {g['market_type']} ({g['reason']})" for g in gaps]
    await update.message.reply_text("\n".join(lines))


async def subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    arg = (ctx.args[0] if ctx.args else "all").lower()
    tiers = {"obvious": "obvious", "value": "value"}.get(arg, "obvious,value")
    store.set_subscription(str(update.effective_chat.id), subscribed=1, tiers=tiers)
    await update.message.reply_text(f"✅ Subscribed to: {tiers}")


async def unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    store.set_subscription(str(update.effective_chat.id), subscribed=0)
    await update.message.reply_text("🔕 Unsubscribed. Pull commands still work.")


async def settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = await _gate(update, ctx)
    if not store:
        return
    sub = store.get_subscription(str(update.effective_chat.id))
    await update.message.reply_text(
        f"⚙️ subscribed={bool(sub['subscribed'])} tiers={sub['tiers']} "
        f"quiet={sub['quiet_start'] or '—'}–{sub['quiet_end'] or '—'}")


async def api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage the priority API key: /api set <key> | status | clear."""
    store = await _gate(update, ctx)
    if not store:
        return
    args = ctx.args or []
    action = (args[0] if args else "status").lower()

    if action == "set":
        if len(args) < 2 or not args[1].strip():
            await update.message.reply_text("Usage: /api set <your-key>")
            return
        key = args[1].strip()
        try:
            info = await asyncio.to_thread(TheOddsAPISource(key).validate)
        except OddsAPIExhausted:
            await update.message.reply_text(
                "❌ That key is invalid or already exhausted. Not saved.")
            return
        except Exception:
            await update.message.reply_text(
                "❌ Could not validate the key right now (network issue). Not saved.")
            return
        store.set_api_key(ODDS_PROVIDER, key)
        try:
            await update.message.delete()
        except Exception:
            pass  # best effort: remove the key from chat history
        await update.message.reply_text(
            f"✅ API key {mask_key(key)} saved and validated "
            f"({info.get('sports', '?')} sports available).\n"
            f"It now has HIGHEST priority. I will notify you if it runs out.")
        return

    if action == "clear":
        store.clear_api_key(ODDS_PROVIDER)
        await update.message.reply_text(
            "🗑 API key removed. Bot runs on free feeds.")
        return

    # default: status
    await update.message.reply_text(format_api_status(store.get_api_key(ODDS_PROVIDER)))
