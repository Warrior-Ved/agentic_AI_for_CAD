"""Episodic memory: an append-only SQLite log of every plan->execute episode.

This is the raw interaction history that reflection consolidates into lessons.
All data stays in ``var/memory`` on the local machine, per the privacy design.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import time
from pathlib import Path

from agentic_cad import config

# Tests may point this at a temp file; None = the default local store.
DB_PATH: Path | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    instruction TEXT NOT NULL,
    context     TEXT,
    plan_json   TEXT,
    success     INTEGER NOT NULL,
    error       TEXT,
    repairs     INTEGER DEFAULT 0,
    volume_mm3  REAL,
    reflected   INTEGER DEFAULT 0
);
"""


def db_path() -> Path:
    return DB_PATH or (config.MEMORY_DIR / "memory.sqlite")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def log_episode(instruction: str, *, context: str | None = None, plan: dict | None = None,
                success: bool = False, error: str | None = None, repairs: int = 0,
                volume_mm3: float | None = None) -> int:
    """Record one episode; returns its id."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO episodes (ts, instruction, context, plan_json, success, error, "
            "repairs, volume_mm3) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), instruction, context,
             json.dumps(plan) if plan else None, int(success), error, repairs, volume_mm3))
        return cur.lastrowid


def get_episode(episode_id: int) -> dict | None:
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return dict(row) if row else None


def recent(n: int = 20) -> list[dict]:
    with closing(_connect()) as conn, conn:
        rows = conn.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]


def unreflected(limit: int = 5, failures_only: bool = False) -> list[dict]:
    """Episodes reflection has not consolidated yet (failures first).

    ``failures_only`` matters for AUTOMATIC reflection: a failure is an
    objective kernel error worth learning from, but a "success" only means the
    plan executed — the geometry could still be semantically wrong, and
    learning such patterns poisons future planning.
    """
    with closing(_connect()) as conn, conn:
        where = "reflected = 0 AND success = 0" if failures_only else "reflected = 0"
        rows = conn.execute(
            f"SELECT * FROM episodes WHERE {where} "
            "ORDER BY success ASC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def mark_reflected(episode_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE episodes SET reflected = 1 WHERE id = ?", (episode_id,))


def stats() -> dict:
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(success), 0) AS successes "
            "FROM episodes").fetchone()
        return {"episodes": row["total"], "successes": row["successes"],
                "failures": row["total"] - row["successes"]}
