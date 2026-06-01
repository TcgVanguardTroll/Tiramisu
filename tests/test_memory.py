"""
Memory layer tests for scripts/memory.py.

learnings.db is the only place Tiramisu keeps long-running state. If the
schema regresses, you lose all observed preferences and the commit-draft
similarity learning. If the @_safe decorator stops swallowing errors, a
DB hiccup will crash the git hook and block your commits.

The bar this file sets:
  - Fresh DB has every documented table + index
  - WAL mode enabled (concurrent hook writes don't block)
  - Each public write -> read pair round-trips
  - similarity() math behaves at extremes (identical, disjoint, empty)
  - @_safe catches exceptions instead of propagating them

Every test uses the tmp_tiramisu_home fixture so the user's real DB is
never touched.
"""
import sqlite3

import pytest


# --------------------------------------------------------------------------
# Schema invariants
# --------------------------------------------------------------------------

REQUIRED_TABLES = {
    "reviews",
    "commit_drafts",
    "tasks",
    "preferences",
    "token_usage",
    "overrides",
}


def test_fresh_db_has_all_required_tables(tmp_tiramisu_home):
    """If a table is dropped from SCHEMA, every code path that writes to it
    will break silently (via @_safe). This test catches schema drift."""
    import memory
    with memory._connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    tables = {r[0] for r in rows}
    missing = REQUIRED_TABLES - tables
    assert not missing, f"missing tables in fresh DB: {missing}"


def test_db_is_in_wal_mode(tmp_tiramisu_home):
    """WAL mode lets a hook write while a long `t reflect` query reads.
    Reverting to the default 'delete' mode would cause SQLITE_BUSY errors
    in git hooks."""
    import memory
    with memory._connection() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"expected WAL journal mode, got {mode!r}"


def test_preferences_table_has_active_column(tmp_tiramisu_home):
    """deactivate_preference() flips active=0 -- if the column goes missing
    preferences become un-deletable."""
    import memory
    with memory._connection() as conn:
        cols = conn.execute("PRAGMA table_info(preferences)").fetchall()
    col_names = {c[1] for c in cols}
    assert "active" in col_names
    assert "category" in col_names
    assert "text" in col_names


# --------------------------------------------------------------------------
# Schema versioning -- migrations are recorded, idempotent, contiguous
# --------------------------------------------------------------------------

def test_schema_migrations_table_exists(tmp_tiramisu_home):
    """The schema_migrations table is the ledger of what's been applied.
    Without it the framework can't decide whether to run a migration."""
    import memory
    with memory._connection() as conn:
        cols = conn.execute(
            "PRAGMA table_info(schema_migrations)"
        ).fetchall()
    col_names = {c[1] for c in cols}
    assert col_names >= {"version", "description", "applied_at"}


def test_fresh_db_records_baseline_migration(tmp_tiramisu_home):
    """A brand-new DB should have v1 (baseline) marked as applied."""
    import memory
    with memory._connection() as conn:
        versions = {row[0] for row in conn.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()}
    assert 1 in versions, (
        "baseline schema (v1) should be recorded on a fresh DB"
    )


def test_fresh_db_applies_all_migrations(tmp_tiramisu_home):
    """Every version in MIGRATIONS plus v1 baseline must be in the
    schema_migrations table after a fresh init."""
    import memory
    expected = {1} | {m[0] for m in memory.MIGRATIONS}
    with memory._connection() as conn:
        actual = {row[0] for row in conn.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()}
    missing = expected - actual
    assert not missing, f"migrations not applied to fresh DB: {missing}"


def test_migration_v2_added_has_blockers_column(tmp_tiramisu_home):
    """v2 must add commit_drafts.has_blockers. If this column goes missing
    `t reflect`'s blocker-override analysis will silently report zero."""
    import memory
    with memory._connection() as conn:
        cols = conn.execute("PRAGMA table_info(commit_drafts)").fetchall()
    col_names = {c[1] for c in cols}
    assert "has_blockers" in col_names, (
        "v2 migration should have added has_blockers to commit_drafts"
    )


def test_migrations_are_idempotent(tmp_tiramisu_home):
    """Connecting twice in a row must not re-apply migrations or duplicate
    rows in schema_migrations."""
    import memory
    # First connect applies migrations
    with memory._connection() as conn:
        first = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    # Second connect should be a no-op
    with memory._connection() as conn:
        second = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert first == second, (
        f"migrations applied twice: before={first}, after={second}"
    )


def test_migrations_versions_are_contiguous(tmp_tiramisu_home):
    """No gaps allowed. If MIGRATIONS jumps from v2 to v4, someone deleted
    v3 -- which is the cardinal rule violation (never edit/delete an
    applied migration). Add a new migration instead."""
    import memory
    versions = [m[0] for m in memory.MIGRATIONS]
    if not versions:
        return  # nothing to check; future-proof
    versions_sorted = sorted(versions)
    expected = list(range(versions_sorted[0], versions_sorted[-1] + 1))
    assert versions_sorted == expected, (
        f"MIGRATIONS has gaps in version numbers: {versions_sorted}; "
        f"expected contiguous {expected}"
    )
    # Lowest migration version must be >= 2 (v1 is the baseline)
    assert versions_sorted[0] >= 2, (
        f"MIGRATIONS list should start at v2 or higher; v1 is the baseline. "
        f"Got {versions_sorted}"
    )


def test_legacy_db_without_version_table_gets_baselined(
    tmp_tiramisu_home, monkeypatch
):
    """Simulate upgrading from a pre-versioning install: an existing
    learnings.db that has the baseline schema but no schema_migrations
    table. The framework must NOT re-apply v1 (which would be safe but
    wasteful); it must mark v1 as already applied and then run v2+."""
    import memory
    import sqlite3

    # Create a DB that looks like a pre-versioning install: baseline schema
    # only, NO schema_migrations table.
    db = tmp_tiramisu_home / "learnings.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(memory.SCHEMA)
    # Add a row in preferences so we can prove no data was lost
    conn.execute(
        "INSERT INTO preferences (text, category) VALUES (?, ?)",
        ("legacy preference", "test"),
    )
    conn.commit()
    conn.close()

    # Now connect via the migration-aware path
    with memory._connection() as conn:
        versions = {row[0] for row in conn.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()}
        prefs = conn.execute(
            "SELECT text FROM preferences"
        ).fetchall()
        cols = {c[1] for c in conn.execute(
            "PRAGMA table_info(commit_drafts)"
        ).fetchall()}

    assert 1 in versions, "legacy DB must be baseline-marked at v1"
    assert 2 in versions, "v2 migration must run on legacy DB"
    assert ("legacy preference",) in prefs, (
        "legacy data was wiped during migration -- this is a data-loss bug"
    )
    assert "has_blockers" in cols, (
        "v2 column should exist after migrating legacy DB"
    )


def test_log_commit_draft_works_after_v2_migration(tmp_tiramisu_home):
    """End-to-end: log_commit_draft still inserts cleanly after the new
    column is added (should be tolerant of the DEFAULT on has_blockers)."""
    import memory
    memory.log_commit_draft("/repo", ["a.py"], "draft message")
    with memory._connection() as conn:
        row = conn.execute(
            "SELECT draft, has_blockers FROM commit_drafts "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    assert row[0] == "draft message"
    # has_blockers default is 0; insert didn't specify a value
    assert row[1] == 0


# --------------------------------------------------------------------------
# Preferences: add / list / deactivate round-trip
# --------------------------------------------------------------------------

def test_add_and_get_preference_round_trip(tmp_tiramisu_home):
    import memory
    memory.add_preference("Prefer pathlib over os.path", category="python")
    prefs = memory.get_active_preferences()
    assert len(prefs) == 1
    assert prefs[0]["text"] == "Prefer pathlib over os.path"
    assert prefs[0]["category"] == "python"


def test_get_active_preferences_filters_by_category(tmp_tiramisu_home):
    import memory
    memory.add_preference("Use type hints",       category="python")
    memory.add_preference("Use let over var",     category="javascript")
    memory.add_preference("No abbreviations",     category=None)

    py = memory.get_active_preferences(category="python")
    assert len(py) == 1
    assert "type hints" in py[0]["text"]

    js = memory.get_active_preferences(category="javascript")
    assert len(js) == 1
    assert "let over var" in js[0]["text"]


def test_deactivate_preference_removes_from_active_list(tmp_tiramisu_home):
    import memory
    memory.add_preference("Temporary preference", category="test")
    prefs = memory.get_active_preferences()
    assert len(prefs) == 1
    pref_id = prefs[0]["id"]

    memory.deactivate_preference(pref_id)
    after = memory.get_active_preferences()
    assert len(after) == 0, "deactivated preference still appears in active list"


# --------------------------------------------------------------------------
# Commit drafts: similarity computation
# --------------------------------------------------------------------------

def test_similarity_identical_strings_is_one():
    from memory import similarity
    s = "feat: add login button"
    assert similarity(s, s) == 1.0


def test_similarity_empty_strings_are_zero():
    """Don't crash on empty inputs -- they can come from a failed draft."""
    from memory import similarity
    assert similarity("", "anything") == 0.0
    assert similarity("anything", "") == 0.0
    assert similarity("", "") == 0.0


def test_similarity_disjoint_strings_below_threshold():
    """Acceptance threshold in update_commit_final() is 0.85.
    Disjoint inputs must fall well under it."""
    from memory import similarity
    s = similarity("xxxxxxxxxxxxxxxx", "yyyyyyyyyyyyyyyy")
    assert s < 0.5


def test_log_commit_draft_then_update_final_records_similarity(tmp_tiramisu_home):
    """log -> update -> read back similarity column. This is the core
    learning loop for `t commit`'s self-evaluation."""
    import memory
    repo = "/some/repo"
    draft = "feat: add login button"
    memory.log_commit_draft(repo, ["src/auth.py"], draft)

    memory.update_commit_final(repo, draft)  # final == draft -> sim = 1.0

    with memory._connection() as conn:
        row = conn.execute(
            "SELECT draft, final, similarity, accepted FROM commit_drafts "
            "WHERE repo_path = ? ORDER BY ts DESC LIMIT 1",
            (repo,),
        ).fetchone()

    assert row is not None
    assert row[0] == draft
    assert row[1] == draft
    assert row[2] == pytest.approx(1.0)
    assert row[3] == 1, "identical final should be marked accepted"


def test_update_commit_final_low_similarity_marks_not_accepted(tmp_tiramisu_home):
    """If user rewrites the commit message entirely, accepted=0."""
    import memory
    repo = "/some/repo"
    memory.log_commit_draft(repo, ["src/x.py"], "draft message AAA")
    memory.update_commit_final(repo, "completely different final BBB")

    with memory._connection() as conn:
        row = conn.execute(
            "SELECT similarity, accepted FROM commit_drafts "
            "WHERE repo_path = ? ORDER BY ts DESC LIMIT 1",
            (repo,),
        ).fetchone()

    assert row[0] < 0.85
    assert row[1] == 0


# --------------------------------------------------------------------------
# Token usage: cost ledger round-trip
# --------------------------------------------------------------------------

def test_log_token_usage_persists(tmp_tiramisu_home):
    """`t reflect` reads token_usage to show cost breakdown. If this stops
    persisting, we lose the cost telemetry that justifies model choices."""
    import memory
    memory.log_token_usage(
        script="dispatch", model="claude-haiku-4-5",
        input_tokens=100, output_tokens=50,
        cache_create_tokens=0, cache_read_tokens=20,
        cost_usd=0.0023,
    )
    with memory._connection() as conn:
        row = conn.execute(
            "SELECT script, model, input_tokens, output_tokens, "
            "cache_read_tokens, cost_usd FROM token_usage "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    assert row[0] == "dispatch"
    assert row[1] == "claude-haiku-4-5"
    assert row[2] == 100
    assert row[3] == 50
    assert row[4] == 20
    assert row[5] == pytest.approx(0.0023)


# --------------------------------------------------------------------------
# stats_since(): the aggregate shape consumed by `t reflect`
# --------------------------------------------------------------------------

def test_stats_since_returns_documented_shape(tmp_tiramisu_home):
    """`t reflect` indexes into specific keys. If a key disappears the
    reflection command crashes."""
    import memory
    # Seed with a tiny amount of data so each branch has something to count
    memory.add_preference("test pref")
    memory.log_token_usage("dispatch", "haiku", 10, 20, 0, 0, 0.001)
    memory.log_commit_draft("/r", ["a.py"], "draft")
    memory.update_commit_final("/r", "draft")

    stats = memory.stats_since(days=30)
    assert isinstance(stats, dict)
    for key in ("days", "reviews", "drafts", "overrides",
                "recent_finals", "tokens"):
        assert key in stats, f"stats_since missing key {key!r}"
    for tkey in ("calls", "input_tokens", "output_tokens",
                 "cost_usd", "by_script", "by_model"):
        assert tkey in stats["tokens"], f"tokens missing key {tkey!r}"


# --------------------------------------------------------------------------
# @_safe: failure isolation
# --------------------------------------------------------------------------

def test_safe_decorator_swallows_exceptions():
    """@_safe is the reason a borked DB doesn't break the git hook. If it
    stops swallowing, every memory failure cascades to user-facing crashes."""
    from memory import _safe

    @_safe
    def explode():
        raise RuntimeError("simulated DB corruption")

    # Should NOT raise; should return None
    result = explode()
    assert result is None


def test_safe_decorator_returns_value_on_success():
    from memory import _safe

    @_safe
    def succeed():
        return 42

    assert succeed() == 42


# --------------------------------------------------------------------------
# diff_hash: stable + truncated
# --------------------------------------------------------------------------

def test_diff_hash_is_deterministic():
    """Used to dedupe reviews of identical diffs."""
    from memory import diff_hash
    s = "@@ -1,3 +1,4 @@ blah blah blah"
    assert diff_hash(s) == diff_hash(s)


def test_diff_hash_returns_16_hex_chars():
    """Truncation is deliberate -- 16 hex chars is plenty for our cardinality
    and keeps the column narrow."""
    from memory import diff_hash
    h = diff_hash("any input")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
