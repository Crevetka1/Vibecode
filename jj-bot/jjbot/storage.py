from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (chat_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    side TEXT NOT NULL,
    setup TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    take REAL NOT NULL,
    opened_at TEXT NOT NULL,
    fair REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at TEXT,
    exit_price REAL
);
CREATE TABLE IF NOT EXISTS signal_messages (
    trade_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (trade_id, chat_id)
);
"""


class Storage:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- подписчики ---
    def add_subscriber(self, chat_id: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO subscribers VALUES (?)", (chat_id,))
        self.db.commit()

    def remove_subscriber(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        self.db.commit()

    def subscribers(self) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT chat_id FROM subscribers")]

    # --- сделки ---
    def save_trade(self, t: Trade) -> Trade:
        cur = self.db.execute(
            "INSERT INTO trades (day, side, setup, entry, stop, take, opened_at, fair, status)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (t.day, t.side, t.setup, t.entry, t.stop, t.take, t.opened_at.isoformat(), t.fair, t.status),
        )
        self.db.commit()
        t.id = cur.lastrowid
        return t

    def close_trade(self, t: Trade) -> None:
        self.db.execute(
            "UPDATE trades SET status = ?, closed_at = ?, exit_price = ? WHERE id = ?",
            (t.status, t.closed_at.isoformat() if t.closed_at else None, t.exit_price, t.id),
        )
        self.db.commit()

    def open_trade(self, day: str) -> Trade | None:
        row = self.db.execute("SELECT * FROM trades WHERE day = ? AND status = 'open' LIMIT 1", (day,)).fetchone()
        return _row_to_trade(row) if row else None

    def stale_open_trades(self, day: str) -> list[Trade]:
        rows = self.db.execute("SELECT * FROM trades WHERE day < ? AND status = 'open'", (day,)).fetchall()
        return [_row_to_trade(r) for r in rows]

    def trades_count(self, day: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM trades WHERE day = ?", (day,)).fetchone()[0]

    def trades_between(self, first_day: str, last_day: str) -> list[Trade]:
        rows = self.db.execute(
            "SELECT * FROM trades WHERE day BETWEEN ? AND ? ORDER BY id", (first_day, last_day)
        ).fetchall()
        return [_row_to_trade(r) for r in rows]

    # --- id сообщений, чтобы отвечать на сигнал ---
    def save_message(self, trade_id: int, chat_id: int, message_id: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO signal_messages VALUES (?,?,?)", (trade_id, chat_id, message_id))
        self.db.commit()

    def messages_for(self, trade_id: int) -> list[tuple[int, int]]:
        rows = self.db.execute("SELECT chat_id, message_id FROM signal_messages WHERE trade_id = ?", (trade_id,))
        return [(r[0], r[1]) for r in rows]


def _row_to_trade(r: sqlite3.Row) -> Trade:
    return Trade(
        id=r["id"],
        day=r["day"],
        side=r["side"],
        setup=r["setup"],
        entry=r["entry"],
        stop=r["stop"],
        take=r["take"],
        opened_at=datetime.fromisoformat(r["opened_at"]),
        fair=r["fair"],
        status=r["status"],
        closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
        exit_price=r["exit_price"],
    )
