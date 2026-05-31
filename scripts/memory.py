"""
Tiramisu memory layer.

A SQLite database at ~/.tiramisu/learnings.db that captures every meaningful
interaction with the agents:

  - Cookie reviews and whether you accepted them
  - Eclair commit message drafts and what you actually committed
  - Croissant task plans and how they panned out
  - Manual preferences added via `t learn`
  - Override patterns (you said "commit anyway" -> learn what to relax on)

Everything is opt-in and fail-soft: if the DB is unavailable, the hooks still
work without learning.
"""
import os
import sqlite3
import sys
import hashlib
import difflib
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
DB_PATH = TIRAMISU_HOME / "learnings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    repo_path TEXT,
    diff_hash TEXT,
    files TEXT,
    diff_chars INTEGER,
    review TEXT,
    blockers_found INTEGER DEFAULT 0,
    outcome TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_ts ON reviews(ts);
CREATE INDEX IF NOT EXISTS idx_reviews_repo ON reviews(repo_path);

CREATE TABLE IF NOT EXISTS commit_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    repo_path TEXT,
    files TEXT,
    draft TEXT,
    final TEXT,
    similarity REAL,
    accepted INTEGER
);
CREATE INDEX IF NOT EXISTS idx_drafts_ts ON commit_drafts(ts);
CREATE INDEX IF NOT EXISTS idx_drafts_repo ON commit_drafts(repo_path);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    plan TEXT,
    saved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    text TEXT NOT NULL,
    category TEXT,
    source TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    script TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_create_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(ts);
CREATE INDEX IF NOT EXISTS idx_token_usage_script ON token_usage(script);

CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    review_id INTEGER,
    snippet TEXT,
    files TEXT,
    FOREIGN KEY(review_id) REFERENCES reviews(id)
);
"""


def get_conn() -> sqlite3.Connection:
    TIRAMISU_HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def _connection():
    """Context manager that guarantees connection cleanup."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _safe(fn):
    """Decorator: swallow exceptions so memory failures never break the hook."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
            return None
    return wrapped


def diff_hash(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()[:16]


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()


# -------- Write helpers --------

@_safe
def log_review(repo_path: str | Path, diff: str, files: list[str], review: str, outcome: str) -> int | None:
    with _connection() as conn:
        cur = conn.execute(
            "INSERT INTO reviews (repo_path, diff_hash, files, diff_chars, review, blockers_found, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(repo_path),
                diff_hash(diff),
                ",".join(files)[:2000],
                len(diff),
                review[:4000],
                1 if "BLOCKER" in (review or "").upper() else 0,
                outcome,
            ),
        )
        return cur.lastrowid


@_safe
def log_override(review_id: int, snippet: str, files: list[str]) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO overrides (review_id, snippet, files) VALUES (?, ?, ?)",
            (review_id, (snippet or "")[:500], ",".join(files)[:1000]),
        )


@_safe
def log_commit_draft(repo_path: str | Path, files: list[str], draft: str) -> int | None:
    with _connection() as conn:
        cur = conn.execute(
            "INSERT INTO commit_drafts (repo_path, files, draft) VALUES (?, ?, ?)",
            (str(repo_path), ",".join(files)[:1000], draft[:2000]),
        )
        return cur.lastrowid


@_safe
def update_commit_final(repo_path: str | Path, final: str) -> int | None:
    """
    Find the most recent draft for this repo without a final yet, attach the final.
    Called from post-commit hook.
    """
    with _connection() as conn:
        row = conn.execute(
            "SELECT id, draft FROM commit_drafts "
            "WHERE repo_path = ? AND final IS NULL "
            "ORDER BY ts DESC LIMIT 1",
            (str(repo_path),),
        ).fetchone()
        if not row:
            return None
        rid, draft = row
        sim = similarity(draft, final)
        accepted = 1 if sim >= 0.85 else 0
        conn.execute(
            "UPDATE commit_drafts SET final = ?, similarity = ?, accepted = ? WHERE id = ?",
            (final[:2000], sim, accepted, rid),
        )
        return rid


@_safe
def log_token_usage(script: str, model: str, input_tokens: int, output_tokens: int,
                    cache_create_tokens: int = 0, cache_read_tokens: int = 0,
                    cost_usd: float = 0.0) -> None:
    """Fire-and-forget log of one API call's token usage."""
    with _connection() as conn:
        conn.execute(
            "INSERT INTO token_usage "
            "(script, model, input_tokens, output_tokens, cache_create_tokens, "
            " cache_read_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (script, model, int(input_tokens or 0), int(output_tokens or 0),
             int(cache_create_tokens or 0), int(cache_read_tokens or 0),
             float(cost_usd or 0.0)),
        )


@_safe
def log_task(description: str, plan: str, saved: bool) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO tasks (description, plan, saved) VALUES (?, ?, ?)",
            (description[:500], plan[:4000], 1 if saved else 0),
        )


@_safe
def add_preference(text: str, category: str | None = None, source: str = "manual") -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO preferences (text, category, source) VALUES (?, ?, ?)",
            (text.strip()[:500], category, source),
        )


@_safe
def deactivate_preference(pref_id: int) -> None:
    with _connection() as conn:
        conn.execute("UPDATE preferences SET active = 0 WHERE id = ?", (pref_id,))


# -------- Read helpers --------

def get_active_preferences(category: str | None = None) -> list[dict[str, Any]]:
    try:
        with _connection() as conn:
            if category:
                rows = conn.execute(
                    "SELECT id, text, category FROM preferences WHERE active = 1 AND category = ? ORDER BY ts DESC",
                    (category,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, text, category FROM preferences WHERE active = 1 ORDER BY ts DESC"
                ).fetchall()
            return [{"id": r[0], "text": r[1], "category": r[2]} for r in rows]
    except Exception as e:
        print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def get_recent_commit_messages(repo_path: str | Path | None = None, n: int = 5) -> list[str]:
    """Return the last N successful commit messages (the final, not the draft)."""
    try:
        with _connection() as conn:
            if repo_path:
                rows = conn.execute(
                    "SELECT final FROM commit_drafts "
                    "WHERE final IS NOT NULL AND repo_path = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (str(repo_path), n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT final FROM commit_drafts "
                    "WHERE final IS NOT NULL "
                    "ORDER BY ts DESC LIMIT ?",
                    (n,),
                ).fetchall()
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def get_recent_overrides(n: int = 10) -> list[dict[str, Any]]:
    """What has Cookie flagged that you've overridden?"""
    try:
        with _connection() as conn:
            rows = conn.execute(
                "SELECT snippet, files, ts FROM overrides ORDER BY ts DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [{"snippet": r[0], "files": r[1], "ts": r[2]} for r in rows]
    except Exception as e:
        print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def stats_since(days: int = 30) -> dict[str, Any]:
    """Aggregate stats for `t reflect`."""
    try:
        with _connection() as conn:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

            reviews = conn.execute(
                "SELECT outcome, COUNT(*) FROM reviews WHERE ts > ? GROUP BY outcome",
                (cutoff,),
            ).fetchall()

            drafts = conn.execute(
                "SELECT COUNT(*), AVG(similarity), SUM(accepted) FROM commit_drafts "
                "WHERE ts > ? AND final IS NOT NULL",
                (cutoff,),
            ).fetchone()

            overrides = conn.execute(
                "SELECT snippet FROM overrides WHERE ts > ? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()

            recent_finals = conn.execute(
                "SELECT final FROM commit_drafts WHERE ts > ? AND final IS NOT NULL ORDER BY ts DESC LIMIT 20",
                (cutoff,),
            ).fetchall()

            token_totals = conn.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cache_create_tokens), SUM(cache_read_tokens), SUM(cost_usd) "
                "FROM token_usage WHERE ts > ?",
                (cutoff,),
            ).fetchone()

            token_by_script = conn.execute(
                "SELECT script, COUNT(*), SUM(input_tokens + output_tokens), SUM(cost_usd) "
                "FROM token_usage WHERE ts > ? GROUP BY script ORDER BY SUM(cost_usd) DESC",
                (cutoff,),
            ).fetchall()

            token_by_model = conn.execute(
                "SELECT model, COUNT(*), SUM(cost_usd) "
                "FROM token_usage WHERE ts > ? GROUP BY model ORDER BY SUM(cost_usd) DESC",
                (cutoff,),
            ).fetchall()

            return {
                "days": days,
                "reviews": {row[0]: row[1] for row in reviews},
                "drafts": {
                    "total": drafts[0] or 0,
                    "avg_similarity": drafts[1] or 0.0,
                    "accepted_as_is": drafts[2] or 0,
                },
                "overrides": [r[0] for r in overrides],
                "recent_finals": [r[0] for r in recent_finals],
                "tokens": {
                    "calls":              token_totals[0] or 0,
                    "input_tokens":       token_totals[1] or 0,
                    "output_tokens":      token_totals[2] or 0,
                    "cache_create":       token_totals[3] or 0,
                    "cache_read":         token_totals[4] or 0,
                    "cost_usd":           token_totals[5] or 0.0,
                    "by_script":          [{"script": r[0], "calls": r[1],
                                            "tokens": r[2] or 0, "cost_usd": r[3] or 0.0}
                                           for r in token_by_script],
                    "by_model":           [{"model": r[0], "calls": r[1],
                                            "cost_usd": r[2] or 0.0}
                                           for r in token_by_model],
                },
            }
    except Exception as e:
        print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
        return {"error": str(e)}
