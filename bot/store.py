"""SQLite persistence (WAL mode). Selections, alerts, subscriptions, gaps."""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS selections (
    id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    match_label TEXT NOT NULL,
    event_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    line TEXT,
    outcome TEXT NOT NULL,
    odds_decimal REAL NOT NULL,
    tier TEXT NOT NULL,
    edge REAL NOT NULL,
    ev REAL NOT NULL,
    stake_cap REAL NOT NULL,
    thesis TEXT NOT NULL,
    invalidation TEXT NOT NULL,
    sources TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    detected_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sel_dedupe
    ON selections(event_id, market_type, line, outcome, status);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL REFERENCES selections(id),
    chat_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id TEXT PRIMARY KEY,
    subscribed INTEGER NOT NULL DEFAULT 1,
    tiers TEXT NOT NULL DEFAULT 'obvious,value',
    quiet_start TEXT,
    quiet_end TEXT
);
CREATE TABLE IF NOT EXISTS gaps (
    sport TEXT NOT NULL,
    market_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (sport, market_type)
);
CREATE TABLE IF NOT EXISTS api_keys (
    provider TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quota_used INTEGER NOT NULL DEFAULT 0,
    quota_remaining INTEGER,
    notified INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or config.DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        try:
            self.conn.execute("ALTER TABLE selections ADD COLUMN outcome_label TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists on older databases

    def save_selection(self, sel: dict) -> str | None:
        """Insert unless an open dup exists. Returns id or None if duplicate."""
        cur = self.conn.execute(
            "SELECT id FROM selections WHERE event_id=? AND market_type=? "
            "AND COALESCE(line,'')=COALESCE(?,'') AND outcome=? AND status='open'",
            (sel["event_id"], sel["market_type"], sel.get("line"), sel["outcome"]),
        )
        if cur.fetchone():
            return None
        sel_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO selections (id,sport,match_label,event_id,market_type,line,"
            "outcome,outcome_label,odds_decimal,tier,edge,ev,stake_cap,thesis,invalidation,sources,"
            "status,detected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)",
            (sel_id, sel["sport"], sel["match_label"], sel["event_id"],
             sel["market_type"], sel.get("line"), sel["outcome"],
             sel.get("outcome_label"), sel["odds_decimal"], sel["tier"], sel["edge"], sel["ev"],
             sel["stake_cap"], sel["thesis"], sel["invalidation"],
             ",".join(sel["sources"]), _now()),
        )
        self.conn.commit()
        return sel_id

    def get_selection(self, sel_id: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM selections WHERE id=?", (sel_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_open(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM selections WHERE status='open' ORDER BY detected_at")
        return [dict(r) for r in cur.fetchall()]

    def settle(self, sel_id: str, status: str) -> None:
        assert status in ("won", "lost", "void", "pending")
        self.conn.execute(
            "UPDATE selections SET status=?, settled_at=? WHERE id=?",
            (status, _now(), sel_id),
        )
        self.conn.commit()

    def record_alert(self, selection_id: str, chat_id: str, state: str = "sent") -> None:
        self.conn.execute(
            "INSERT INTO alerts (id,selection_id,chat_id,state,sent_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), selection_id, chat_id, state,
             _now() if state == "sent" else None),
        )
        self.conn.commit()

    def alert_exists(self, selection_id: str, chat_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM alerts WHERE selection_id=? AND chat_id=? AND state='sent'",
            (selection_id, chat_id),
        )
        return cur.fetchone() is not None

    def ensure_subscription(self, chat_id: str) -> dict:
        self.conn.execute(
            "INSERT OR IGNORE INTO subscriptions (chat_id) VALUES (?)", (chat_id,))
        self.conn.commit()
        return self.get_subscription(chat_id)

    def get_subscription(self, chat_id: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM subscriptions WHERE chat_id=?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def set_subscription(self, chat_id: str, **fields) -> None:
        self.ensure_subscription(chat_id)
        allowed = {"subscribed", "tiers", "quiet_start", "quiet_end"}
        for key, value in fields.items():
            if key in allowed:
                self.conn.execute(
                    f"UPDATE subscriptions SET {key}=? WHERE chat_id=?", (value, chat_id))
        self.conn.commit()

    def subscribers_for_tier(self, tier: str) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM subscriptions WHERE subscribed=1")
        out = []
        for row in cur.fetchall():
            sub = dict(row)
            if tier in (sub["tiers"] or "").split(","):
                out.append(sub)
        return out

    def upsert_gap(self, sport: str, market_type: str, reason: str) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO gaps (sport,market_type,reason,first_seen,last_seen) "
            "VALUES (?,?,?,?,?) ON CONFLICT(sport,market_type) DO UPDATE SET "
            "last_seen=excluded.last_seen, reason=excluded.reason",
            (sport, market_type, reason, now, now),
        )
        self.conn.commit()

    def get_gaps(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM gaps ORDER BY sport, market_type")
        return [dict(r) for r in cur.fetchall()]

    # ---- API keys (user-supplied, highest-priority source) ----

    def set_api_key(self, provider: str, api_key: str) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO api_keys (provider,api_key,status,quota_used,"
            "quota_remaining,notified,updated_at) VALUES (?,?,'active',0,NULL,0,?) "
            "ON CONFLICT(provider) DO UPDATE SET api_key=excluded.api_key, "
            "status='active', quota_used=0, quota_remaining=NULL, notified=0, "
            "updated_at=excluded.updated_at",
            (provider, api_key, now),
        )
        self.conn.commit()

    def get_api_key(self, provider: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM api_keys WHERE provider=?", (provider,))
        row = cur.fetchone()
        return dict(row) if row else None

    def clear_api_key(self, provider: str) -> None:
        self.conn.execute("DELETE FROM api_keys WHERE provider=?", (provider,))
        self.conn.commit()

    def update_api_quota(self, provider: str, used: int | None,
                         remaining: int | None, status: str | None = None) -> None:
        row = self.get_api_key(provider)
        if not row:
            return
        self.conn.execute(
            "UPDATE api_keys SET quota_used=COALESCE(?,quota_used), "
            "quota_remaining=COALESCE(?,quota_remaining), "
            "status=COALESCE(?,status), updated_at=? WHERE provider=?",
            (used, remaining, status, _now(), provider),
        )
        self.conn.commit()

    def mark_api_notified(self, provider: str) -> None:
        self.conn.execute("UPDATE api_keys SET notified=1 WHERE provider=?", (provider,))
        self.conn.commit()

    def _tehran_day(self, day: str | None) -> str:
        if day:
            return day
        return datetime.now(config.TEHRAN_TZ).strftime("%Y-%m-%d")

    def stats_for_day(self, day: str | None = None) -> dict:
        target = self._tehran_day(day)
        cur = self.conn.execute("SELECT * FROM selections ORDER BY detected_at")
        picks, counts = [], {"won": 0, "lost": 0, "void": 0, "pending": 0, "open": 0}
        for row in cur.fetchall():
            sel = dict(row)
            detected = datetime.fromisoformat(sel["detected_at"])
            if detected.astimezone(config.TEHRAN_TZ).strftime("%Y-%m-%d") != target:
                continue
            picks.append(sel)
            counts[sel["status"]] = counts.get(sel["status"], 0) + 1
        return {"day": target, "picks": picks, "counts": counts}

    def record_alltime(self) -> dict:
        cur = self.conn.execute(
            "SELECT tier, status, COUNT(*) c FROM selections GROUP BY tier, status")
        return [{"tier": r["tier"], "status": r["status"], "count": r["c"]}
                for r in cur.fetchall()]

    def repair_instant_settlements(self, max_seconds: int = 120) -> int:
        """Void picks settled impossibly fast after detection.

        A real game cannot finish seconds after we spot it; such rows are
        bogus instant-settlements (stale odds evaluated on finished games)
        and must not pollute the track record. Returns voided count.
        """
        from datetime import datetime as _dt
        cur = self.conn.execute(
            "SELECT id, detected_at, settled_at FROM selections "
            "WHERE status IN ('won','lost') AND settled_at IS NOT NULL")
        voided = 0
        for row in cur.fetchall():
            try:
                detected = _dt.fromisoformat(row["detected_at"])
                settled = _dt.fromisoformat(row["settled_at"])
                if (settled - detected).total_seconds() < max_seconds:
                    self.conn.execute(
                        "UPDATE selections SET status='void' WHERE id=?", (row["id"],))
                    voided += 1
            except (TypeError, ValueError):
                continue
        if voided:
            self.conn.commit()
        return voided
