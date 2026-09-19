"""Tiered polling loop with quota budgeting and graceful degradation."""
from __future__ import annotations

import asyncio
import logging
import time

from . import config
from .analysis import markets as catalog
from .analysis import pipeline
from .feeds.base import LiveEvent, Source
from .feeds.espn import EspnSource
from .feeds.kalshi import KalshiSource
from .feeds.oddsapi import PROVIDER as ODDS_PROVIDER, OddsAPIExhausted, TheOddsAPISource
from .feeds.polymarket import PolymarketSource
from .notify import format_alert, format_result, in_quiet_hours, send_with_backoff
from .settle import settle
from .store import Store

log = logging.getLogger("live-odds-bot")

SOURCES: list[Source] = [EspnSource(), PolymarketSource(), KalshiSource()]

# Preferred primary order: user-supplied API key first, then ESPN.
PRIMARY_PREFERENCE = ("oddsapi", "espn")

# Per-source call budget per cycle; exhausted meters degrade instead of dying
CALL_BUDGET = {"espn": 40, "polymarket": 20, "kalshi": 20}


def _key(ev: LiveEvent) -> str:
    return f"{ev.sport}|{ev.match_label}".lower()


STOPWORDS = {"vs", "v", "the", "fc", "cf", "sc", "ac", "united", "city",
             "real", "club", "de", "la", "le", "les", "at", "of", "and"}

# Cities with multiple major teams: a bare city token must not join fixtures.
CITY_TOKENS = {"toronto", "london", "madrid", "milan", "manchester", "paris",
               "moscow", "istanbul", "los", "angeles", "new", "york", "mexico",
               "buenos", "aires", "rio", "sao", "paulo", "rome", "berlin"}


def team_tokens(label: str) -> set[str]:
    toks = set()
    for part in label.lower().replace("vs.", " ").replace("vs", " ").split():
        word = "".join(c for c in part if c.isalnum())
        if len(word) > 2 and word not in STOPWORDS:
            toks.add(word)
    return toks


def same_match(a: LiveEvent, b: LiveEvent) -> bool:
    """Fuzzy fixture join across heterogeneous source labels."""
    if _key(a) == _key(b):
        return True
    ta, tb = team_tokens(a.match_label), team_tokens(b.match_label)
    if not ta or not tb:
        return False
    overlap = ta & tb
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1:
        tok = next(iter(overlap))
        # single shared token only counts if distinctive, long, and not a
        # multi-team city (e.g. Toronto Maple Leafs vs Toronto Raptors)
        return len(tok) >= 8 and tok not in CITY_TOKENS
    la, lb = a.match_label.lower(), b.match_label.lower()
    return (len(la) > 8 and la in lb) or (len(lb) > 8 and lb in la)


def is_stale(ev: LiveEvent, max_age_hours: float = 6.0) -> bool:
    """True when a non-live event started too long ago to trust.

    Yesterday's games lingering in feeds must never produce alerts.
    Events without a known start time are never judged stale here.
    """
    if ev.is_live or not ev.starts_at:
        return False
    try:
        from datetime import datetime, timedelta, timezone
        kickoff = datetime.fromisoformat(str(ev.starts_at).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - kickoff > timedelta(hours=max_age_hours)
    except Exception:
        return False


FETCH_TIMEOUT = 12
MAX_CONCURRENT_FETCHES = 8

# Hard bound on pushes per cycle: backstop against any future spam loop.
# Capped picks are skipped entirely (not saved), so a later cycle retries.
MAX_ALERTS_PER_CYCLE = 5

# Per-sport fairness cap: no single sport may dominate a cycle's alerts.
MAX_ALERTS_PER_SPORT = 2


async def _fetch_one(sem: asyncio.Semaphore, src: Source, sport: str,
                     store: Store | None = None):
    async with sem:
        try:
            evs = await asyncio.wait_for(
                asyncio.to_thread(src.fetch_live, sport), timeout=FETCH_TIMEOUT)
            if isinstance(src, TheOddsAPISource) and store is not None:
                store.update_api_quota(ODDS_PROVIDER, src.last_used, src.last_remaining)
            return src.name, sport, evs or []
        except OddsAPIExhausted as exc:
            if store is not None:
                src_obj = src if isinstance(src, TheOddsAPISource) else None
                store.update_api_quota(
                    ODDS_PROVIDER,
                    src_obj.last_used if src_obj else None,
                    0, status="exhausted")
                log.warning("odds api exhausted: %s", exc)
            return src.name, sport, []
        except Exception as exc:
            log.warning("feed %s failed for %s: %s", src.name, sport, exc)
            return src.name, sport, []


def cycle_sources(store: Store) -> list[Source]:
    """Source list for one cycle. A usable API key goes FIRST (highest priority)."""
    sources: list[Source] = list(SOURCES)
    row = store.get_api_key(ODDS_PROVIDER)
    if row and row["api_key"] and row["status"] == "active":
        sources.insert(0, TheOddsAPISource(row["api_key"]))
    return sources


async def run_cycle(store: Store, bot=None) -> dict:
    """One monitor pass over all catalog sports. Returns cycle stats."""
    stats = {"evaluated": 0, "alerted": 0, "discarded": 0, "degraded": []}
    sport_alerts: dict[str, int] = {}
    sources = cycle_sources(store)
    remaining = dict(CALL_BUDGET)
    # Key quota is precious but the budget must cover every sport, else the
    # last sport permanently reports degraded.
    remaining.setdefault("oddsapi", len(catalog.all_sports()))
    sem = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    jobs = []
    for sport in catalog.all_sports():
        for src in sources:
            if remaining.get(src.name, 0) <= 0:
                if src.name not in stats["degraded"]:
                    stats["degraded"].append(src.name)
                continue
            remaining[src.name] -= 1
            jobs.append(_fetch_one(sem, src, sport, store))
    per_sport: dict[str, dict[str, list[LiveEvent]]] = {}
    for name, sport, evs in await asyncio.gather(*jobs):
        per_sport.setdefault(sport, {})[name] = evs
    for sport in catalog.all_sports():
        per_source = per_sport.get(sport, {})
        # Build one candidate pool from ALL sources, joined by fuzzy
        # fixture matching, so any single-source outage degrades gracefully.
        pool: list[LiveEvent] = []
        for src in sources:
            for ev in per_source.get(src.name, []):
                if not ev.markets:
                    continue
                if is_stale(ev):
                    continue
                ev.source_name = src.name
                pool.append(ev)
        groups: list[list[LiveEvent]] = []
        for ev in pool:
            placed = False
            for g in groups:
                if any(same_match(ev, member) for member in g):
                    if not any(m.source_name == ev.source_name for m in g):
                        g.append(ev)
                    placed = True
                    break
            if not placed:
                groups.append([ev])
        for members in groups:
            if len(members) < 2:
                ev = members[0]
                store.upsert_gap(ev.sport, ev.markets[0].market_type,
                                 "single-source-only")
                stats["discarded"] += 1
                continue
            # Primary = API-key source first, then ESPN, else first available.
            by_source = {e.source_name: e for e in members}
            ev = members[0]
            for preferred in PRIMARY_PREFERENCE:
                if preferred in by_source:
                    ev = by_source[preferred]
                    break
            corroborating = [e for e in members if e is not ev]
            monitored = [m for m in ev.markets
                         if catalog.is_monitored(ev.sport, m.market_type)]
            for m in ev.markets:
                if not catalog.is_monitored(ev.sport, m.market_type):
                    store.upsert_gap(ev.sport, m.market_type, "not-in-catalog")
            if not monitored:
                continue
            stats["evaluated"] += 1
            sels = pipeline.evaluate_all(ev, corroborating, config)
            if not sels:
                stats["discarded"] += 1
                continue
            for sel in sels:
                if stats["alerted"] >= MAX_ALERTS_PER_CYCLE:
                    stats["discarded"] += 1
                    continue
                if sport_alerts.get(ev.sport, 0) >= MAX_ALERTS_PER_SPORT:
                    stats["discarded"] += 1
                    continue
                sel_id = store.save_selection(sel)
                if not sel_id:
                    continue  # duplicate open selection
                sel["id"] = sel_id
                stats["alerted"] += 1
                sport_alerts[ev.sport] = sport_alerts.get(ev.sport, 0) + 1
                if bot is not None:
                    await dispatch(store, bot, sel)
    if bot is not None:
        stats["settled"] = await settle_open(store, bot)
    else:
        stats["settled"] = await settle_open(store)
    await check_api_exhaustion(store, bot)
    return stats


async def check_api_exhaustion(store: Store, bot=None) -> bool:
    """Notify once when the API key is exhausted. Returns True if notified now."""
    row = store.get_api_key(ODDS_PROVIDER)
    if not row or row["status"] != "exhausted" or row["notified"]:
        return False
    store.mark_api_notified(ODDS_PROVIDER)
    if bot is None:
        return True
    text = ("⚠️ API key exhausted — quota reached its limit.\n"
            "Switched back to free feeds. Send /api status to check, "
            "or /api set <new-key> to restore priority data.")
    sent_any = False
    seen: set[str] = set()
    for tier in ("obvious", "value"):
        for sub in store.subscribers_for_tier(tier):
            if sub["chat_id"] in seen:
                continue
            seen.add(sub["chat_id"])
            if await send_with_backoff(bot, sub["chat_id"], text):
                sent_any = True
    return sent_any


def _match_final(match_label: str, finals: dict) -> dict | None:
    """Fuzzy-match a pick's fixture to a final score by label."""
    from .feeds.base import LiveEvent as _LE
    probe = _LE(event_id="", sport="", match_label=match_label, is_live=False)
    for final in finals.values():
        label = final.get("label") or ""
        if label and same_match(probe, _LE(event_id="", sport="",
                                           match_label=label, is_live=False)):
            return final
    return None


async def settle_open(store: Store, bot=None) -> int:
    """Settle open picks from one batched ESPN finals pass.

    Every newly settled pick is pushed live to its tier subscribers, so the
    running won/lost tally reaches chats without asking. Returns count.
    """
    espn = next((s for s in SOURCES if s.name == "espn"), None)
    if espn is None or not hasattr(espn, "fetch_finals"):
        return 0
    try:
        finals = await asyncio.to_thread(espn.fetch_finals)
    except Exception as exc:
        log.warning("finals fetch failed: %s", exc)
        return 0
    settled = 0
    for sel in store.get_open():
        final = finals.get(str(sel["event_id"]))
        if final is None:
            # Cross-source pick (prediction-market event id): settle by
            # fuzzy fixture match so stale picks never linger as open.
            final = _match_final(sel["match_label"], finals)
            if final is None:
                continue
        outcome = settle(sel, final)
        if outcome in ("won", "lost", "void"):
            store.settle(sel["id"], outcome)
            settled += 1
            if bot is not None:
                await dispatch_result(store, bot, {**sel, "status": outcome})
    return settled


async def dispatch_result(store: Store, bot, sel: dict) -> None:
    """Push a live won/lost/void update for a settled pick."""
    text = format_result(sel)
    for sub in store.subscribers_for_tier(sel["tier"]):
        chat_id = sub["chat_id"]
        ok = await send_with_backoff(bot, chat_id, text)
        store.record_alert(sel["id"], chat_id, state="sent" if ok else "failed")


async def dispatch(store: Store, bot, sel: dict) -> None:
    text = format_alert(sel)
    for sub in store.subscribers_for_tier(sel["tier"]):
        chat_id = sub["chat_id"]
        if store.alert_exists(sel["id"], chat_id):
            continue
        if in_quiet_hours(sub):
            store.record_alert(sel["id"], chat_id, state="queued")
            continue
        ok = await send_with_backoff(bot, chat_id, text)
        store.record_alert(sel["id"], chat_id, state="sent" if ok else "failed")


async def monitor_forever(store: Store, bot) -> None:
    log.info("monitor loop started")
    while True:
        started = time.monotonic()
        try:
            stats = await run_cycle(store, bot)
            log.info("cycle done: %s", stats)
        except Exception as exc:
            log.exception("cycle failed: %s", exc)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(config.LIVE_CADENCE - elapsed, 10))
