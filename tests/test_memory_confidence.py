"""
P4 -- confidence scoring on preferences (from ECC's "instincts").

A preference carries a `confidence` count. Re-teaching the same preference
(via `t learn`, or a research-apply re-proposal) does not add a duplicate
row -- it BUMPS the existing row's confidence. get_active_preferences()
returns confidence and orders by it, so the most-affirmed rules sort to the
top of every composed prompt.

§4.3 boundary (pinned implicitly here): confidence only ever changes from a
human-initiated re-teach. Nothing in the agent loop auto-adjusts it, and it
never rewrites a persona -- it only re-orders/annotates user-authored
preferences. `t reflect` reports confidence; it does not mutate it.
"""
import pytest

import memory


def test_migration_v6_adds_confidence_default_1(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    with memory._connection() as conn:
        versions = {r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        assert 6 in versions
        conf = conn.execute(
            "SELECT confidence FROM preferences WHERE text = ?",
            ("Prefer guard clauses",),
        ).fetchone()[0]
        assert conf == 1


def test_get_active_preferences_exposes_confidence(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    assert memory.get_active_preferences()[0]["confidence"] == 1


def test_reteaching_bumps_confidence_not_rows(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    assert memory.add_preference("Prefer guard clauses") == "duplicate"
    assert memory.add_preference("Prefer guard clauses") == "duplicate"
    prefs = memory.get_active_preferences()
    assert len(prefs) == 1
    assert prefs[0]["confidence"] == 3


def test_reinforcement_is_case_and_whitespace_insensitive(tmp_tiramisu_home):
    memory.add_preference("Use type hints")
    memory.add_preference("  USE type hints ")  # same up to case + trim
    prefs = memory.get_active_preferences()
    assert len(prefs) == 1
    assert prefs[0]["confidence"] == 2


def test_active_preferences_sorted_by_confidence_desc(tmp_tiramisu_home):
    memory.add_preference("Low signal rule")
    memory.add_preference("High signal rule")
    memory.add_preference("High signal rule")   # bump to confidence 2
    prefs = memory.get_active_preferences()
    assert prefs[0]["text"] == "High signal rule"
    assert prefs[0]["confidence"] == 2


def test_forgotten_preference_restarts_at_confidence_1(tmp_tiramisu_home):
    memory.add_preference("Prefer guard clauses")
    memory.add_preference("Prefer guard clauses")          # confidence 2
    pid = memory.get_active_preferences()[0]["id"]
    memory.deactivate_preference(pid)
    assert memory.add_preference("Prefer guard clauses") == "added"
    assert memory.get_active_preferences()[0]["confidence"] == 1


def test_reinforced_preference_emphasized_in_steering(tmp_tiramisu_home,
                                                      clear_steering_cache):
    import steering
    memory.add_preference("Always type-annotate public functions")
    memory.add_preference("Always type-annotate public functions")  # confidence 2
    composed = steering.load_steering("eclair", include_preferences=True)
    assert "Always type-annotate public functions" in composed
    # A reinforced preference carries a salience marker the model can see.
    assert "reinforced" in composed.lower()
