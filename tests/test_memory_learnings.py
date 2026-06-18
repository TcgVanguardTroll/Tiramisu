"""
Tests for the "searchable, private, lean learnings" memory unit:

  P0b  dedup-on-write    -- a repeated preference is not stored twice
  P1   FTS5 search       -- `memory.search_learnings()` over learnings_fts
  P2   <private> redact  -- secrets wrapped in <private> never hit the DB

Safety-relevant (redaction gates what gets persisted), so per CLAUDE.md §8
the contract is pinned before the feature. All DB tests use the
tmp_tiramisu_home fixture so the real learnings.db is never touched.
"""
import sqlite3

import pytest

import memory


def _has_fts5() -> bool:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


HAS_FTS5 = _has_fts5()
needs_fts5 = pytest.mark.skipif(not HAS_FTS5, reason="SQLite built without FTS5")


# --------------------------------------------------------------------------
# P2 -- <private> redaction (pure function)
# --------------------------------------------------------------------------

def test_redact_closed_span():
    assert memory.redact_private("<private>secret</private>") == "[redacted]"


def test_redact_preserves_surrounding_text():
    out = memory.redact_private("keep <private>drop</private> this")
    assert "drop" not in out
    assert "keep" in out and "this" in out


def test_redact_unclosed_tag_redacts_to_end():
    # A dangling open tag must not leak the text after it.
    out = memory.redact_private("public <private>then a secret with no close")
    assert "secret" not in out
    assert out.startswith("public ")


def test_redact_multiple_spans():
    out = memory.redact_private("<private>a</private> mid <private>b</private>")
    assert "a" not in out.replace("redacted", "")
    assert "mid" in out
    assert out.count("[redacted]") == 2


def test_redact_is_case_insensitive():
    assert "secret" not in memory.redact_private("<PRIVATE>secret</PRIVATE>")


def test_redact_non_string_passes_through():
    assert memory.redact_private(None) is None
    assert memory.redact_private(12345) == 12345


def test_redact_plain_text_untouched():
    assert memory.redact_private("nothing to hide here") == "nothing to hide here"


# --------------------------------------------------------------------------
# P0b -- dedup on write
# --------------------------------------------------------------------------

def test_add_preference_reports_added(tmp_tiramisu_home):
    assert memory.add_preference("Prefer guard clauses") == "added"


def test_duplicate_preference_not_stored_twice(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    assert memory.add_preference("Prefer guard clauses") == "duplicate"
    prefs = memory.get_active_preferences()
    matching = [p for p in prefs if p["text"] == "Prefer guard clauses"]
    assert len(matching) == 1


def test_dedup_ignores_case_and_whitespace(tmp_tiramisu_home):
    memory.add_preference("Use type hints")
    assert memory.add_preference("  use TYPE hints  ") == "duplicate"
    assert len(memory.get_active_preferences()) == 1


def test_distinct_preferences_both_stored(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    memory.add_preference("Always type-annotate public functions")
    assert len(memory.get_active_preferences()) == 2


def test_reactivation_after_forget(tmp_tiramisu_home):
    # A forgotten (inactive) preference does not block re-adding it.
    memory.add_preference("Prefer guard clauses")
    pref_id = memory.get_active_preferences()[0]["id"]
    memory.deactivate_preference(pref_id)
    assert memory.add_preference("Prefer guard clauses") == "added"


# --------------------------------------------------------------------------
# P2 x P0b -- redaction applies to what gets stored
# --------------------------------------------------------------------------

def test_preference_stored_with_secret_redacted(tmp_tiramisu_home):
    memory.add_preference("api key is <private>sk-12345</private> ok")
    text = memory.get_active_preferences()[0]["text"]
    assert "sk-12345" not in text
    assert "[redacted]" in text


# --------------------------------------------------------------------------
# P1 -- FTS5 search
# --------------------------------------------------------------------------

@needs_fts5
def test_migration_v5_creates_fts_table(tmp_tiramisu_home):
    with memory._connection() as conn:
        versions = {r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        assert 5 in versions
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'learnings_fts'"
        ).fetchone()
        assert tbl is not None


@needs_fts5
def test_search_finds_preference(tmp_tiramisu_home):
    memory.add_preference("Prefer pathlib over os.path")
    results = memory.search_learnings("pathlib")
    assert any(r["kind"] == "preference" and "pathlib" in r["text"]
               for r in results)


@needs_fts5
def test_search_finds_review(tmp_tiramisu_home):
    memory.log_review("/repo", "diff", ["a.py"],
                      "This review mentions a flaky race condition", "noted")
    results = memory.search_learnings("race")
    assert any(r["kind"] == "review" for r in results)


@needs_fts5
def test_search_finds_commit_final(tmp_tiramisu_home):
    memory.log_commit_draft("/repo", ["a.py"], "draft msg")
    memory.update_commit_final("/repo", "fix(parser): handle trailing newline")
    results = memory.search_learnings("parser")
    assert any(r["kind"] == "commit" for r in results)


@needs_fts5
def test_search_no_match_returns_empty(tmp_tiramisu_home):
    memory.add_preference("Prefer pathlib over os.path")
    assert memory.search_learnings("zzzznotpresent") == []


@needs_fts5
def test_search_empty_query_returns_empty(tmp_tiramisu_home):
    memory.add_preference("Prefer pathlib over os.path")
    assert memory.search_learnings("") == []
    assert memory.search_learnings("   ") == []


@needs_fts5
def test_redacted_content_is_not_searchable(tmp_tiramisu_home):
    memory.add_preference("token <private>hunter2</private> placeholder")
    assert memory.search_learnings("hunter2") == []


@needs_fts5
def test_search_special_chars_do_not_crash(tmp_tiramisu_home):
    memory.add_preference("Prefer pathlib over os.path")
    # FTS5 MATCH would error on raw quotes/parens; the sanitizer must absorb it.
    for q in ['"', "(", "pathlib AND", "os.path)", "a OR b"]:
        assert isinstance(memory.search_learnings(q), list)


@needs_fts5
def test_search_backfills_existing_rows(tmp_tiramisu_home, monkeypatch):
    # Insert a preference WITHOUT going through the FTS-aware writer, then
    # force a fresh migration run: the v5 backfill should index it.
    with memory._connection() as conn:
        conn.execute("INSERT INTO preferences (text, active) VALUES (?, 1)",
                     ("backfilled distinctword", ))
        conn.execute("DROP TABLE learnings_fts")
        conn.execute("DELETE FROM schema_migrations WHERE version = 5")
    # Next connection re-applies v5 and backfills.
    results = memory.search_learnings("distinctword")
    assert any("distinctword" in r["text"] for r in results)
