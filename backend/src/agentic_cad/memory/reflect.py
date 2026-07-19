"""Reflective memory: consolidate raw episodes into short, transferable
*lessons*, retrieve the relevant ones at planning time, and prune the store.

Retrieval is embedding-based (Ollama's local all-minilm model, vectors stored
as float32 blobs in the same SQLite file — cosine via numpy), degrading
gracefully to keyword overlap whenever embeddings are unavailable, so the
whole pipeline still works fully offline.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import Callable

from agentic_cad import config
from agentic_cad.cad import bootstrap
from agentic_cad.memory import episodic

bootstrap.ensure_freecad_importable()   # numpy ships in FreeCAD's site-packages
import numpy as np  # noqa: E402

# embed_fn(text) -> vector (list[float]) or None when unavailable.
EmbedFn = Callable[[str], "list[float] | None"]
# chat_fn(messages) -> reply text.
ChatFn = Callable[[list[dict]], str]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    text           TEXT NOT NULL,
    source_episode INTEGER,
    embedding      BLOB
);
"""

REFLECT_SYSTEM = """\
You consolidate CAD-agent experience. Given one episode (instruction, plan,
outcome), state ONE short transferable lesson (max 2 sentences) that would
help plan a SIMILAR future request better. Be concrete about geometry/tool
choices, not generic advice. Reply with the lesson text only."""


def _connect() -> sqlite3.Connection:
    path = episodic.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Embeddings (optional, local)
# --------------------------------------------------------------------------- #
def make_ollama_embed_fn(model: str = config.MODEL_EMBED, host: str = config.OLLAMA_HOST) -> EmbedFn:
    def embed(text: str):
        try:
            import ollama
            resp = ollama.Client(host=host).embeddings(model=model, prompt=text)
            vec = resp.get("embedding")
            return list(vec) if vec else None
        except Exception:
            return None

    return embed


def _to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denom if denom > 1e-12 else 0.0


# --------------------------------------------------------------------------- #
# Lessons
# --------------------------------------------------------------------------- #
def add_lesson(text: str, *, source_episode: int | None = None,
               embed_fn: EmbedFn | None = None) -> int:
    vec = embed_fn(text) if embed_fn else None
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO lessons (ts, text, source_episode, embedding) VALUES (?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), text.strip(), source_episode,
             _to_blob(vec) if vec else None))
        return cur.lastrowid


def all_lessons() -> list[dict]:
    with closing(_connect()) as conn, conn:
        rows = conn.execute("SELECT id, ts, text, source_episode, "
                            "embedding IS NOT NULL AS embedded FROM lessons "
                            "ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def _keyword_score(query: str, text: str) -> float:
    q = {w for w in query.lower().split() if len(w) > 3}
    t = {w for w in text.lower().split() if len(w) > 3}
    return len(q & t) / len(q) if q else 0.0


def relevant_lessons(query: str, k: int = 3, *, embed_fn: EmbedFn | None = None,
                     min_score: float = 0.25) -> list[str]:
    """Top-k lessons for a new instruction — cosine over stored embeddings when
    possible, keyword overlap otherwise."""
    qvec = embed_fn(query) if embed_fn else None
    q = np.asarray(qvec, dtype=np.float32) if qvec else None
    scored: list[tuple[float, str]] = []
    with closing(_connect()) as conn, conn:
        for row in conn.execute("SELECT text, embedding FROM lessons"):
            if q is not None and row["embedding"]:
                score = _cosine(q, _from_blob(row["embedding"]))
            else:
                score = _keyword_score(query, row["text"])
            if score >= min_score:
                scored.append((score, row["text"]))
    scored.sort(key=lambda s: -s[0])
    return [text for _, text in scored[:k]]


def lessons_context(query: str, k: int = 3, *, embed_fn: EmbedFn | None = None) -> str | None:
    """The planner-context block, or None when nothing relevant is stored."""
    lessons = relevant_lessons(query, k, embed_fn=embed_fn)
    if not lessons:
        return None
    return ("Hints from past attempts (the geometry rules above take precedence "
            "if these conflict):\n" + "\n".join(f"- {t}" for t in lessons))


# --------------------------------------------------------------------------- #
# Reflection + consolidation (prune)
# --------------------------------------------------------------------------- #
def reflect_on_episode(episode: dict, *, chat_fn: ChatFn,
                       embed_fn: EmbedFn | None = None) -> str | None:
    """Ask the model for one lesson from an episode; store + mark reflected."""
    outcome = "SUCCEEDED" if episode.get("success") else f"FAILED: {episode.get('error')}"
    user = (f"Instruction: {episode['instruction']}\n"
            f"Plan: {episode.get('plan_json') or '(none)'}\n"
            f"Outcome: {outcome}")
    try:
        lesson = chat_fn([{"role": "system", "content": REFLECT_SYSTEM},
                          {"role": "user", "content": user}]).strip()
    except Exception:
        return None
    if not lesson:
        return None
    add_lesson(lesson, source_episode=episode["id"], embed_fn=embed_fn)
    episodic.mark_reflected(episode["id"])
    return lesson


def consolidate(*, similarity_threshold: float = 0.92, max_lessons: int = 60) -> int:
    """Prune the lesson store: drop near-duplicates (keep the newest) and cap
    the total count (drop the oldest). Returns how many were removed."""
    with closing(_connect()) as conn, conn:
        rows = conn.execute("SELECT id, embedding FROM lessons ORDER BY id DESC").fetchall()
        drop: set[int] = set()
        embedded = [(r["id"], _from_blob(r["embedding"])) for r in rows if r["embedding"]]
        for i in range(len(embedded)):
            if embedded[i][0] in drop:
                continue
            for j in range(i + 1, len(embedded)):  # j is older than i
                if embedded[j][0] in drop:
                    continue
                if _cosine(embedded[i][1], embedded[j][1]) >= similarity_threshold:
                    drop.add(embedded[j][0])
        keep = [r["id"] for r in rows if r["id"] not in drop]
        drop.update(keep[max_lessons:])           # cap: rows are newest-first
        if drop:
            conn.executemany("DELETE FROM lessons WHERE id = ?", [(i,) for i in drop])
        return len(drop)
