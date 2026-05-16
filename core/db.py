"""
core/db.py
==========
SQLite database — all tables and helpers.
Includes result tracking for sports bets and trading signals.
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from core.config import DB_PATH

log = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create all tables. Safe to call multiple times."""
    c = _conn()
    c.executescript("""
        -- ── Sports signals ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS sports_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type     TEXT    NOT NULL,   -- POSITIVE_EV | ARBITRAGE
            source          TEXT,               -- SPORTSBOOK | POLYMARKET | KALSHI | CROSS_MARKET
            matchup         TEXT,
            play            TEXT,
            book            TEXT,
            odds            TEXT,
            edge_pct        REAL,
            arb_pct         REAL,
            confidence      INTEGER,
            reasoning       TEXT,
            timing          TEXT,
            legs_json       TEXT,               -- JSON array for arb legs
            created_at      TEXT,
            result          TEXT,               -- WIN | LOSS | PUSH | VOID | NULL
            result_note     TEXT,
            resolved_at     TEXT,
            status          TEXT    DEFAULT 'ACTIVE'   -- ACTIVE | RESOLVED | EXPIRED
        );

        -- ── Trading signals ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trading_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT    NOT NULL,
            signal_type     TEXT    NOT NULL,   -- BUY | SELL
            pattern         TEXT,
            entry_price     REAL,
            target_1        REAL,
            target_2        REAL,
            stop_loss       REAL,
            risk_reward     REAL,
            confidence      INTEGER,
            signal_strength TEXT,
            reasoning       TEXT,
            invalidation    TEXT,
            volume_confirmed INTEGER DEFAULT 0,
            timeframe       TEXT,
            created_at      TEXT,
            closed_at       TEXT,
            close_price     REAL,
            outcome         TEXT,               -- TARGET_1 | TARGET_2 | STOP | MANUAL | NULL
            pnl_pct         REAL,
            status          TEXT    DEFAULT 'ACTIVE'  -- ACTIVE | CLOSED | INVALIDATED
        );

        -- ── Content queue ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS content_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name    TEXT,
            asin            TEXT,
            platform        TEXT,
            hook            TEXT,
            script_json     TEXT,
            affiliate_url   TEXT,
            tiktok_shop_url TEXT,
            priority_score  REAL,
            status          TEXT    DEFAULT 'PENDING',
            queued_at       TEXT,
            posted_at       TEXT,
            post_url        TEXT
        );

        -- ── Dead letter queue ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS dead_letter (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            func_name   TEXT,
            args_json   TEXT,
            error       TEXT,
            created_at  TEXT,
            resolved    INTEGER DEFAULT 0
        );
    """)
    c.commit()
    c.close()
    log.info("Database ready: %s", DB_PATH)


# ── Sports helpers ────────────────────────────────────────────────────────────

def save_sports_signal(signal: dict) -> int:
    c = _conn()
    cur = c.execute("""
        INSERT INTO sports_signals
            (signal_type, source, matchup, play, book, odds,
             edge_pct, arb_pct, confidence, reasoning, timing,
             legs_json, created_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        signal.get("type"),
        signal.get("source"),
        signal.get("matchup"),
        signal.get("play"),
        signal.get("book"),
        signal.get("odds"),
        signal.get("edge", signal.get("edge_pct", 0.0)),
        signal.get("arb_percentage", 0.0),
        signal.get("confidence", 0),
        signal.get("reasoning"),
        signal.get("timing"),
        json.dumps(signal.get("legs", [])),
        datetime.utcnow().isoformat(),
        "ACTIVE",
    ))
    row_id = cur.lastrowid
    c.commit()
    c.close()
    return row_id


def get_active_sports_signals() -> list:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM sports_signals WHERE status='ACTIVE' ORDER BY created_at DESC"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def resolve_sports_signal(signal_id: int, result: str, note: str = ""):
    """Mark a sports signal as resolved with WIN/LOSS/PUSH/VOID."""
    c = _conn()
    c.execute("""
        UPDATE sports_signals
        SET result=?, result_note=?, resolved_at=?, status='RESOLVED'
        WHERE id=?
    """, (result, note, datetime.utcnow().isoformat(), signal_id))
    c.commit()
    c.close()


def get_signal_by_id(signal_id: int) -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM sports_signals WHERE id=?", (signal_id,)).fetchone()
    c.close()
    return dict(row) if row else None


# ── Trading helpers ───────────────────────────────────────────────────────────

def save_trading_signal(signal: dict) -> int:
    c = _conn()
    cur = c.execute("""
        INSERT INTO trading_signals
            (ticker, signal_type, pattern, entry_price, target_1, target_2,
             stop_loss, risk_reward, confidence, signal_strength, reasoning,
             invalidation, volume_confirmed, timeframe, created_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        signal["ticker"],
        signal["signal_type"],
        signal.get("pattern"),
        signal.get("entry_price"),
        signal.get("target_1"),
        signal.get("target_2"),
        signal.get("stop_loss"),
        signal.get("risk_reward"),
        signal.get("confidence"),
        signal.get("signal_strength"),
        signal.get("reasoning"),
        signal.get("invalidation"),
        1 if signal.get("volume_confirmed") else 0,
        signal.get("timeframe", "INTRADAY"),
        datetime.utcnow().isoformat(),
        "ACTIVE",
    ))
    row_id = cur.lastrowid
    c.commit()
    c.close()
    return row_id


def get_active_trading_signals() -> list:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM trading_signals WHERE status='ACTIVE' ORDER BY created_at DESC"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def close_trading_signal(signal_id: int, outcome: str, close_price: float, pnl_pct: float):
    """Record a trade closing event: TARGET_1, TARGET_2, STOP, MANUAL."""
    c = _conn()
    c.execute("""
        UPDATE trading_signals
        SET outcome=?, close_price=?, pnl_pct=?, closed_at=?, status='CLOSED'
        WHERE id=?
    """, (outcome, close_price, pnl_pct, datetime.utcnow().isoformat(), signal_id))
    c.commit()
    c.close()


def invalidate_trading_signal(ticker: str):
    c = _conn()
    c.execute("""
        UPDATE trading_signals
        SET status='INVALIDATED', closed_at=?
        WHERE ticker=? AND status='ACTIVE'
    """, (datetime.utcnow().isoformat(), ticker))
    c.commit()
    c.close()


def get_trading_signal_by_ticker(ticker: str) -> Optional[dict]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM trading_signals WHERE ticker=? AND status='ACTIVE'",
        (ticker,)
    ).fetchone()
    c.close()
    return dict(row) if row else None


# ── Content helpers ───────────────────────────────────────────────────────────

def queue_content_item(package: dict):
    c = _conn()
    c.execute("""
        INSERT INTO content_queue
            (product_name, asin, platform, hook, script_json,
             affiliate_url, tiktok_shop_url, priority_score, queued_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        package.get("product_name"),
        package.get("asin"),
        package.get("platform_priority", "AMAZON"),
        package.get("hook"),
        json.dumps(package.get("script", {})),
        package.get("affiliate_url"),
        package.get("tiktok_shop_url"),
        package.get("priority_score", 5.0),
        datetime.utcnow().isoformat(),
        "PENDING",
    ))
    c.commit()
    c.close()


def get_pending_content(limit: int = 3) -> list:
    c = _conn()
    rows = c.execute("""
        SELECT * FROM content_queue
        WHERE status='PENDING'
        ORDER BY priority_score DESC LIMIT ?
    """, (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def mark_content_posted(item_id: int, post_url: str = ""):
    c = _conn()
    c.execute("""
        UPDATE content_queue SET status='POSTED', posted_at=?, post_url=? WHERE id=?
    """, (datetime.utcnow().isoformat(), post_url, item_id))
    c.commit()
    c.close()


def already_queued_today(product_name: str) -> bool:
    c = _conn()
    n = c.execute("""
        SELECT COUNT(*) FROM content_queue
        WHERE product_name=? AND date(queued_at)=date('now')
    """, (product_name,)).fetchone()[0]
    c.close()
    return n > 0


# ── Dead letter ───────────────────────────────────────────────────────────────

def write_dead_letter(func_name: str, args: str, error: str):
    try:
        c = _conn()
        c.execute("""
            INSERT INTO dead_letter (func_name, args_json, error, created_at)
            VALUES (?,?,?,?)
        """, (func_name, args, error, datetime.utcnow().isoformat()))
        c.commit()
        c.close()
    except Exception as e:
        log.error("Dead letter write failed: %s", e)
