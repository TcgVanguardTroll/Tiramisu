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
import hashlib
import difflib
from datetime import datetime, timedelta
from pathlib import Path

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

CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    review_id INTEGER,
    snippet TEXT,
    files TEXT,
    FOREIGN KEY(review_id) REFERENCES reviews(id)
);
"""


def get_conn():
    TIRAMISU_HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.executescript(SCHEMA)
    return conn


def _safe(fn):
    """Decorator: swallow exceptions so memory failures never break the hook."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            # Optional: write to a debug log if you want to inspect failures
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
def log_review(repo_path, diff, files, review, outcome):
    conn = get_conn()
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
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


@_safe
def log_override(review_id, snippet, files):
    conn = get_conn()
    conn.execute(
        "INSERT INTO overrides (review_id, snippet, files) VALUES (?, ?, ?)",
        (review_id, (snippet or "")[:500], ",".join(files)[:1000]),
    )
    conn.commit()
    conn.close()


@_safe
def log_commit_draft(repo_path, files, draft):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO commit_drafts (repo_path, files, draft) VALUES (?, ?, ?)",
        (str(repo_path), ",".join(files)[:1000], draft[:2000]),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


@_safe
def update_commit_final(repo_path, final):
    """
    Find the most recent draft for this repo without a final yet, attach the final.
    Called from post-commit hook.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, draft FROM commit_drafts "
        "WHERE repo_path = ? AND final IS NULL "
        "ORDER BY ts DESC LIMIT 1",
        (str(repo_path),),
    ).fetchone()
    if not row:
        conn.close()
        return None
    rid, draft = row
    sim = similarity(draft, final)
    accepted = 1 if sim >= 0.85 else 0
    conn.execute(
        "UPDATE commit_drafts SET final = ?, similarity = ?, accepted = ? WHERE id = ?",
        (final[:2000], sim, accepted, rid),
    )
    conn.commit()
    conn.close()
    return rid


@_safe
def log_task(description, plan, saved):
    conn = get_conn()
    conn.execute(
        "INSERT INTO tasks (description, plan, saved) VALUES (?, ?, ?)",
        (description[:500], plan[:4000], 1 if saved else 0),
    )
    conn.commit()
    conn.close()


@_safe
def add_preference(text, category=None, source="manual"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO preferences (text, category, source) VALUES (?, ?, ?)",
        (text.strip()[:500], category, source),
    )
    conn.commit()
    conn.close()


@_safe
def deactivate_preference(pref_id):
    conn = get_conn()
    conn.execute("UPDATE preferences SET active = 0 WHERE id = ?", (pref_id,))
    conn.commit()
    conn.close()


# -------- Read helpers --------

def get_active_preferences(category=None):
    try:
        conn = get_conn()
        if category:
            rows = conn.execute(
                "SELECT id, text, category FROM preferences WHERE active = 1 AND category = ? ORDER BY ts DESC",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, text, category FROM preferences WHERE active = 1 ORDER BY ts DESC"
            ).fetchall()
        conn.close()
        return [{"id": r[0], "text": r[1], "category": r[2]} for r in rows]
    except Exception:
        return []


def get_recent_commit_messages(repo_path=None, n=5):
    """Return the last N successful commit messages (the final, not the draft)."""
    try:
        conn = get_conn()
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
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def get_recent_overrides(n=10):
    """What has Cookie flagged that you've overridden?"""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT snippet, files, ts FROM overrides ORDER BY ts DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
        return [{"snippet": r[0], "files": r[1], "ts": r[2]} for r in rows]
    except Exception:
        return []


def stats_since(days=30):
    """Aggregate stats for `t reflect`."""
    try:
        conn = get_conn()
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

        conn.close()

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
        }
    except Exception as e:
        return {"error": str(e)}
