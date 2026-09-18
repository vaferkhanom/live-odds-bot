import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from bot import commands, monitor
from bot.feeds.oddsapi import PROVIDER, TheOddsAPISource
from bot.notify import format_api_status, mask_key
from bot.store import Store


def _auth():
    import bot.config as config
    old = config.AUTHORIZED_CHAT_IDS
    config.AUTHORIZED_CHAT_IDS = ["111"]
    return old


def _ctx(store, args=None):
    update = MagicMock()
    update.effective_chat.id = 111
    update.message.reply_text = AsyncMock()
    update.message.delete = AsyncMock()
    ctx = MagicMock()
    ctx.application.bot_data = {"store": store}
    ctx.args = args or []
    return update, ctx


def test_mask_key_never_shows_full_key():
    assert mask_key("abcdef123456") == "…3456"
    assert "abcdef123456" not in mask_key("abcdef123456")


def test_api_status_no_key():
    text = format_api_status(None)
    assert "No API key" in text and "/api set" in text


def test_api_status_active_shows_priority():
    text = format_api_status({"api_key": "abcdef123456", "status": "active",
                              "quota_used": 10, "quota_remaining": 490,
                              "notified": 0})
    assert "…3456" in text and "HIGHEST" in text and "490" in text
    assert "abcdef123456" not in text


def test_api_set_validates_and_saves():
    old = _auth()
    try:
        store = Store(path=tempfile.mktemp(suffix=".db"))
        update, ctx = _ctx(store, ["set", "TESTKEY123"])
        with patch.object(TheOddsAPISource, "validate",
                          return_value={"ok": True, "sports": 5,
                                        "used": 0, "remaining": 500}):
            asyncio.run(commands.api(update, ctx))
        row = store.get_api_key(PROVIDER)
        assert row and row["api_key"] == "TESTKEY123" and row["status"] == "active"
        text = update.message.reply_text.await_args[0][0]
        assert "HIGHEST priority" in text
        assert "TESTKEY123" not in text  # masked in reply
    finally:
        import bot.config as config
        config.AUTHORIZED_CHAT_IDS = old


def test_api_set_rejects_bad_key():
    old = _auth()
    try:
        store = Store(path=tempfile.mktemp(suffix=".db"))
        update, ctx = _ctx(store, ["set", "BADKEY"])
        from bot.feeds.oddsapi import OddsAPIExhausted
        with patch.object(TheOddsAPISource, "validate", side_effect=OddsAPIExhausted("x")):
            asyncio.run(commands.api(update, ctx))
        assert store.get_api_key(PROVIDER) is None
        assert "Not saved" in update.message.reply_text.await_args[0][0]
    finally:
        import bot.config as config
        config.AUTHORIZED_CHAT_IDS = old


def test_api_clear_and_status_flow():
    old = _auth()
    try:
        store = Store(path=tempfile.mktemp(suffix=".db"))
        store.set_api_key(PROVIDER, "K1")
        update, ctx = _ctx(store, ["status"])
        asyncio.run(commands.api(update, ctx))
        assert "status: active" in update.message.reply_text.await_args[0][0]
        update2, ctx2 = _ctx(store, ["clear"])
        asyncio.run(commands.api(update2, ctx2))
        assert store.get_api_key(PROVIDER) is None
    finally:
        import bot.config as config
        config.AUTHORIZED_CHAT_IDS = old


def test_cycle_sources_puts_key_first():
    store = Store(path=tempfile.mktemp(suffix=".db"))
    names = [s.name for s in monitor.cycle_sources(store)]
    assert names[0] == "espn"  # no key -> keyless first
    store.set_api_key(PROVIDER, "K1")
    names = [s.name for s in monitor.cycle_sources(store)]
    assert names[0] == "oddsapi"  # key present -> highest priority
    store.update_api_quota(PROVIDER, 500, 0, status="exhausted")
    names = [s.name for s in monitor.cycle_sources(store)]
    assert names[0] == "espn"  # exhausted -> back to free feeds


def test_exhaustion_notifies_once():
    store = Store(path=tempfile.mktemp(suffix=".db"))
    store.set_api_key(PROVIDER, "K1")
    store.ensure_subscription("111")
    store.update_api_quota(PROVIDER, 500, 0, status="exhausted")
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=True)
    assert asyncio.run(monitor.check_api_exhaustion(store, bot)) is True
    text = bot.send_message.await_args.kwargs.get("text", "")
    assert "exhausted" in text and "K1" not in text
    bot.send_message.reset_mock()
    assert asyncio.run(monitor.check_api_exhaustion(store, bot)) is False
    bot.send_message.assert_not_called()
