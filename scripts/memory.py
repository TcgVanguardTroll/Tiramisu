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
import re
import sqlite3
import sys
import hashlib
import difflib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


# --------------------------------------------------------------------------
# Schema versioning
# --------------------------------------------------------------------------
# Every connection runs the baseline SCHEMA (idempotent: every CREATE uses
# IF NOT EXISTS). On top of that we maintain an ordered list of migrations
# that change the schema beyond the baseline. Each migration records itself
# in `schema_migrations` so re-running the framework is a no-op.
#
# Rules for adding a migration
#   1. Pick the next integer version (current MIGRATIONS list + 1).
#   2. SQL must be SAFE on an already-migrated DB if accidentally re-run
#      (CREATE INDEX IF NOT EXISTS, ALTER TABLE ADD COLUMN with a default,
#      etc.). The framework protects against re-runs but defensive SQL is
#      free insurance.
#   3. Never EDIT an existing migration -- append a new one instead. Old
#      installs have already applied the original; rewriting it would
#      desync history.
#   4. Add a test in tests/test_memory.py that asserts the new column /
#      index / constraint actually exists after migration.
#
# See docs/INVARIANTS.md "Schema discipline" for the rationale.

# version 1 is the baseline (everything in SCHEMA above). Versions 2+ are
# changes applied on top.
MIGRATIONS = [
    (
        2,
        "Add commit_drafts.has_blockers to track Cookie BLOCKER overrides",
        # If Cookie's pre-commit review flagged a BLOCKER and the user
        # committed anyway (--no-verify or by fixing nothing), we want to
        # know about it. `t reflect` can then ask: how aggressive is Cookie
        # being, and how often does the user override? Default 0 means
        # "no blocker / not yet known."
        "ALTER TABLE commit_drafts ADD COLUMN has_blockers INTEGER DEFAULT 0",
    ),
    (
        3,
        "Add token_usage.repo_path for per-project cost breakdown",
        # reviews and commit_drafts already record repo_path; token_usage
        # was the only learning table without it, so `t reflect` could not
        # answer "which project is costing me money?". NULL means "logged
        # before this migration" -- old rows stay aggregate-only.
        "ALTER TABLE token_usage ADD COLUMN repo_path TEXT",
    ),
    (
        4,
        "Add routes table so reflect can audit the NL router",
        # Every `tiramisu <text>` routing decision lands here: the input,
        # the command chosen, and how it was chosen (fast/llm/fallback/
        # error). Reviews and drafts already feed the learning loop;
        # misroutes were the one signal that vanished. `t reflect` uses
        # the fallback/error rate to propose router-prompt example edits.
        """
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            input TEXT,
            command TEXT,
            via TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_routes_ts ON routes(ts);
        """,
    ),
    (
        5,
        "Add learnings_fts FTS5 index for `t learn search`",
        # A single full-text index over the free-text learnings (preferences,
        # Cookie reviews, committed messages, task plans). FTS5 keeps search
        # in structured SQLite -- NO vector store (CLAUDE.md §6). Backfill
        # existing rows so search covers history, not just new writes. If this
        # SQLite build lacks FTS5, _apply_migrations records the migration as
        # skipped and search degrades to empty (see the "no such module"
        # branch below).
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS learnings_fts USING fts5(
            kind, text, ref_id UNINDEXED
        );
        INSERT INTO learnings_fts (kind, text, ref_id)
            SELECT 'preference', text, id FROM preferences WHERE active = 1;
        INSERT INTO learnings_fts (kind, text, ref_id)
            SELECT 'review', review, id FROM reviews
            WHERE review IS NOT NULL AND review != '';
        INSERT INTO learnings_fts (kind, text, ref_id)
            SELECT 'commit', final, id FROM commit_drafts
            WHERE final IS NOT NULL AND final != '';
        INSERT INTO learnings_fts (kind, text, ref_id)
            SELECT 'task', plan, id FROM tasks
            WHERE plan IS NOT NULL AND plan != '';
        """,
    ),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Bring the connected DB up to the current schema version.

    Steps:
      1. Run the baseline SCHEMA (idempotent thanks to IF NOT EXISTS).
      2. Create the schema_migrations table if missing.
      3. If schema_migrations is empty, this is either a fresh DB or an
         old pre-versioning install. Mark v1 as applied either way -- the
         baseline SCHEMA is identical to what v1 represents, and we just
         ran it above.
      4. For each row in MIGRATIONS whose version isn't recorded, execute
         the SQL and record the version. Stop on the first failure (don't
         leave the DB in a partially-migrated state).
    """
    conn.executescript(SCHEMA)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    applied = {
        row[0] for row in
        conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    # Baseline: if nothing's recorded yet, mark v1 as applied. This handles
    # both fresh installs (schema just created above) and pre-versioning
    # installs (schema already existed). In both cases the on-disk state
    # matches v1.
    if not applied:
        conn.execute(
            "INSERT INTO schema_migrations (version, description) VALUES (1, ?)",
            ("baseline schema",),
        )
        applied.add(1)

    # Apply pending migrations in version order.
    for version, description, sql in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version in applied:
            continue
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, description) "
                "VALUES (?, ?)",
                (version, description),
            )
        except sqlite3.OperationalError as e:
            # Common case: ADD COLUMN failed because someone re-ran a
            # migration manually. If we can see the column already exists,
            # mark the migration as applied and continue. Otherwise re-raise.
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) "
                    "VALUES (?, ?)",
                    (version, description + " (detected as already applied)"),
                )
                continue
            if "no such module" in msg:
                # This SQLite build lacks a module the migration needs (e.g.
                # FTS5). Record it as applied so we don't retry every
                # connection; the dependent feature degrades gracefully.
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) "
                    "VALUES (?, ?)",
                    (version, description + " (skipped: module unavailable)"),
                )
                continue
            raise


def get_conn() -> sqlite3.Connection:
    TIRAMISU_HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    _apply_migrations(conn)
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


# --------------------------------------------------------------------------
# <private> redaction (P2)
# --------------------------------------------------------------------------
# Anything wrapped in <private>...</private> is stripped before it is written
# to learnings.db (and therefore before it is indexed for search). Borrowed
# from claude-mem: the user controls what the crew is allowed to remember. A
# dangling, unclosed <private> redacts to end-of-string so a secret typed
# after an opening tag can never leak.
_PRIVATE_CLOSED = re.compile(r"<private>.*?</private>", re.IGNORECASE | re.DOTALL)
_PRIVATE_OPEN = re.compile(r"<private>.*", re.IGNORECASE | re.DOTALL)
_REDACTED = "[redacted]"


def redact_private(text: Any) -> Any:
    """Replace <private>...</private> spans with [redacted]. Non-strings and
    text without the tag pass through untouched (fail-soft)."""
    if not isinstance(text, str) or "<private>" not in text.lower():
        return text
    text = _PRIVATE_CLOSED.sub(_REDACTED, text)
    text = _PRIVATE_OPEN.sub(_REDACTED, text)  # mop up any unclosed tag
    return text


# --------------------------------------------------------------------------
# FTS5 search index (P1)
# --------------------------------------------------------------------------
_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _fts_query(raw: str) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression: each token
    quoted as a literal (so quotes/parens/operators in user input can't
    produce an FTS5 syntax error), joined by implicit AND. Empty if no
    usable tokens."""
    tokens = _FTS_TOKEN.findall(raw or "")
    return " ".join(f'"{t}"' for t in tokens)


def _fts_index(conn: sqlite3.Connection, kind: str, ref_id: int | None,
               text: str | None) -> None:
    """Best-effort: add one row to learnings_fts. Swallows OperationalError
    (e.g. SQLite compiled without FTS5) so indexing never breaks the primary
    write that just happened in the same transaction."""
    if not text:
        return
    try:
        conn.execute(
            "INSERT INTO learnings_fts (kind, text, ref_id) VALUES (?, ?, ?)",
            (kind, text, ref_id),
        )
    except sqlite3.OperationalError:
        pass


def diff_hash(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()[:16]


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()


# -------- Write helpers --------

@_safe
def log_review(repo_path: str | Path, diff: str, files: list[str], review: str, outcome: str) -> int | None:
    clean_review = redact_private(review or "")[:4000]
    with _connection() as conn:
        cur = conn.execute(
            "INSERT INTO reviews (repo_path, diff_hash, files, diff_chars, review, blockers_found, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(repo_path),
                diff_hash(diff),
                ",".join(files)[:2000],
                len(diff),
                clean_review,
                1 if "BLOCKER" in (review or "").upper() else 0,
                outcome,
            ),
        )
        _fts_index(conn, "review", cur.lastrowid, clean_review)
        return cur.lastrowid


@_safe
def log_override(review_id: int, snippet: str, files: list[str]) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO overrides (review_id, snippet, files) VALUES (?, ?, ?)",
            (review_id, redact_private(snippet or "")[:500], ",".join(files)[:1000]),
        )


@_safe
def log_commit_draft(repo_path: str | Path, files: list[str], draft: str) -> int | None:
    with _connection() as conn:
        cur = conn.execute(
            "INSERT INTO commit_drafts (repo_path, files, draft) VALUES (?, ?, ?)",
            (str(repo_path), ",".join(files)[:1000], redact_private(draft or "")[:2000]),
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
        clean_final = redact_private(final or "")[:2000]
        sim = similarity(draft, clean_final)
        accepted = 1 if sim >= 0.85 else 0
        conn.execute(
            "UPDATE commit_drafts SET final = ?, similarity = ?, accepted = ? WHERE id = ?",
            (clean_final, sim, accepted, rid),
        )
        _fts_index(conn, "commit", rid, clean_final)
        return rid


@_safe
def log_token_usage(script: str, model: str, input_tokens: int, output_tokens: int,
                    cache_create_tokens: int = 0, cache_read_tokens: int = 0,
                    cost_usd: float = 0.0, repo_path: str | None = None) -> None:
    """Fire-and-forget log of one API call's token usage."""
    with _connection() as conn:
        conn.execute(
            "INSERT INTO token_usage "
            "(script, model, input_tokens, output_tokens, cache_create_tokens, "
            " cache_read_tokens, cost_usd, repo_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (script, model, int(input_tokens or 0), int(output_tokens or 0),
             int(cache_create_tokens or 0), int(cache_read_tokens or 0),
             float(cost_usd or 0.0), repo_path),
        )


@_safe
def log_route(user_input: str, command: str, via: str) -> None:
    """Record one NL routing decision. via: fast | llm | fallback | error."""
    with _connection() as conn:
        conn.execute(
            "INSERT INTO routes (input, command, via) VALUES (?, ?, ?)",
            (user_input[:200], command, via),
        )


@_safe
def log_task(description: str, plan: str, saved: bool) -> None:
    clean_plan = redact_private(plan or "")[:4000]
    with _connection() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (description, plan, saved) VALUES (?, ?, ?)",
            (redact_private(description or "")[:500], clean_plan, 1 if saved else 0),
        )
        _fts_index(conn, "task", cur.lastrowid, clean_plan)


@_safe
def add_preference(text: str, category: str | None = None,
                   source: str = "manual") -> str | None:
    """Store a preference, redacting <private> spans and skipping duplicates.
    Returns "added", "duplicate", or None (on failure, via @_safe). Dedup is
    case/whitespace-insensitive over ACTIVE preferences, so re-teaching a rule
    you already have -- or that `t research apply` re-proposes -- doesn't let
    learnings.db accumulate endlessly (borrowed from DeerFlow's dedup-at-apply)."""
    clean = redact_private(text or "").strip()[:500]
    with _connection() as conn:
        existing = conn.execute(
            "SELECT id FROM preferences "
            "WHERE active = 1 AND lower(trim(text)) = lower(trim(?))",
            (clean,),
        ).fetchone()
        if existing:
            return "duplicate"
        cur = conn.execute(
            "INSERT INTO preferences (text, category, source) VALUES (?, ?, ?)",
            (clean, category, source),
        )
        _fts_index(conn, "preference", cur.lastrowid, clean)
        return "added"


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


def search_learnings(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search across stored learnings (preferences, reviews,
    committed messages, task plans) via the FTS5 index. Returns ranked
    matches as {kind, text, ref_id}. Empty list on empty query, no match,
    or any FTS unavailability -- never raises (fail-soft like the other
    reads). No vectors: this is plain SQLite FTS5 (CLAUDE.md §6)."""
    match = _fts_query(query)
    if not match:
        return []
    try:
        with _connection() as conn:
            rows = conn.execute(
                "SELECT kind, text, ref_id FROM learnings_fts "
                "WHERE learnings_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
            return [{"kind": r[0], "text": r[1], "ref_id": r[2]} for r in rows]
    except sqlite3.OperationalError:
        # FTS5 not compiled in, or index missing -- search is unavailable.
        return []
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
            # tz-naive isoformat to match SQLite's CURRENT_TIMESTAMP column
            # which is also stored as a naive UTC string. datetime.utcnow()
            # was deprecated in Python 3.12; this is the supported equivalent.
            cutoff_dt = (datetime.now(timezone.utc).replace(tzinfo=None)
                         - timedelta(days=days))
            cutoff = cutoff_dt.isoformat()

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

            # repo_path is NULL on rows logged before migration v3; those
            # land in a single "(unknown)" bucket rather than disappearing.
            token_by_repo = conn.execute(
                "SELECT COALESCE(repo_path, '(unknown)'), COUNT(*), SUM(cost_usd) "
                "FROM token_usage WHERE ts > ? GROUP BY repo_path "
                "ORDER BY SUM(cost_usd) DESC",
                (cutoff,),
            ).fetchall()

            routes_by_via = conn.execute(
                "SELECT via, COUNT(*) FROM routes WHERE ts > ? GROUP BY via",
                (cutoff,),
            ).fetchall()

            # The inputs the router couldn't place -- raw material for new
            # router-prompt examples.
            route_fallbacks = conn.execute(
                "SELECT input FROM routes WHERE ts > ? "
                "AND via IN ('fallback', 'error') ORDER BY ts DESC LIMIT 20",
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
                    "by_repo":            [{"repo": r[0], "calls": r[1],
                                            "cost_usd": r[2] or 0.0}
                                           for r in token_by_repo],
                },
                "routing": {
                    "by_via":    {row[0]: row[1] for row in routes_by_via},
                    "fallbacks": [r[0] for r in route_fallbacks],
                },
            }
    except Exception as e:
        print(f"[tiramisu] memory warning: {type(e).__name__}: {e}", file=sys.stderr)
        return {"error": str(e)}
