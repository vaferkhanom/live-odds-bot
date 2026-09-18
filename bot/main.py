"""Entrypoint: PTB app + monitor background task + heartbeat."""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application, CommandHandler

from . import commands, config
from .monitor import monitor_forever
from .store import Store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
# Silence chatty HTTP loggers: httpx logs full request URLs, which would
# leak the bot token into deployment logs. Never log secrets.
for noisy in ("httpx", "httpcore", "h11", "telegram", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("live-odds-bot")


async def _post_init(app) -> None:
    store: Store = app.bot_data["store"]
    for chat_id in config.AUTHORIZED_CHAT_IDS:
        store.ensure_subscription(chat_id)
    app.create_task(monitor_forever(store, app.bot))
    log.info("heartbeat: bot started, monitor scheduled")


def build_app(store: Store | None = None):
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (env only, never in code).")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    app.bot_data["store"] = store or Store()
    app.add_handler(CommandHandler("start", commands.start))
    app.add_handler(CommandHandler("help", commands.help_cmd))
    app.add_handler(CommandHandler("opportunities", commands.opportunities))
    app.add_handler(CommandHandler("stats", commands.stats))
    app.add_handler(CommandHandler("record", commands.record))
    app.add_handler(CommandHandler("status", commands.status))
    app.add_handler(CommandHandler("coverage", commands.coverage))
    app.add_handler(CommandHandler("subscribe", commands.subscribe))
    app.add_handler(CommandHandler("unsubscribe", commands.unsubscribe))
    app.add_handler(CommandHandler("api", commands.api))
    app.add_handler(CommandHandler("settings", commands.settings))
    return app


def main() -> None:
    build_app().run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
