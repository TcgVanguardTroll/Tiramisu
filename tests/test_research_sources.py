"""
Research source loader precedence tests.

scripts/research.py supports a 3-layer source configuration:

  1. DEFAULT_SOURCES                       (hardcoded, ships with Tiramisu)
  2. ~/.tiramisu/sources.json              (user override -- REPLACES defaults)
  3. <repo>/.tiramisu/sources.json         (per-repo additive layer)

Same precedence model as steering. The "user replaces defaults but repo
is additive" rule is non-obvious: it lets a user nuke the defaults they
don't care about (e.g., removing aider when they don't use it) while
still letting per-project sources stack on top.

If this precedence flips, users will be confused about why their override
isn't taking effect, OR worse: a per-repo source list will silently wipe
out their global config.

These tests use tmp_tiramisu_home (isolates user file) and tmp_workspace
(isolates the cwd that repo_sources_path() reads from).
"""
import json

import pytest


# --------------------------------------------------------------------------
# Layer 1: defaults
# --------------------------------------------------------------------------

def test_no_user_no_repo_returns_defaults(tmp_tiramisu_home, tmp_workspace):
    """With neither override file present, load_sources() returns the
    hardcoded DEFAULT_SOURCES list."""
    import research
    sources = research.load_sources()
    assert len(sources) == len(research.DEFAULT_SOURCES)
    assert {s["name"] for s in sources} == {
        s["name"] for s in research.DEFAULT_SOURCES
    }


# --------------------------------------------------------------------------
# Layer 2: user file REPLACES defaults
# --------------------------------------------------------------------------

def test_user_file_replaces_defaults(tmp_tiramisu_home, tmp_workspace):
    """User-defined sources.json wins over DEFAULT_SOURCES entirely.
    This is deliberate -- users who curate their own list don't want
    the built-in noise mixed in."""
    import research

    user_sources = [
        {"name": "My Custom Source", "url": "https://example.com/feed",
         "focus": "anything"},
    ]
    user_file = tmp_tiramisu_home / "sources.json"
    user_file.write_text(json.dumps(user_sources), encoding="utf-8")

    sources = research.load_sources()
    names = [s["name"] for s in sources]
    assert "My Custom Source" in names
    # NONE of the defaults should appear
    for default in research.DEFAULT_SOURCES:
        assert default["name"] not in names, (
            f"default source {default['name']!r} leaked through when user "
            f"file should have replaced everything"
        )


# --------------------------------------------------------------------------
# Layer 3: repo file is ADDITIVE
# --------------------------------------------------------------------------

def test_repo_file_extends_defaults(tmp_tiramisu_home, tmp_workspace):
    """No user file, but a per-repo file present -> defaults + repo sources."""
    import research

    repo_dir = tmp_workspace / ".tiramisu"
    repo_dir.mkdir()
    repo_sources = [
        {"name": "Project ADR feed", "url": "https://example.com/adr",
         "focus": "architecture decisions"},
    ]
    (repo_dir / "sources.json").write_text(
        json.dumps(repo_sources), encoding="utf-8"
    )

    sources = research.load_sources()
    names = [s["name"] for s in sources]
    # Project source must appear
    assert "Project ADR feed" in names
    # Defaults still there
    assert "Anthropic Cookbook" in names
    # Count: defaults + 1
    assert len(sources) == len(research.DEFAULT_SOURCES) + 1


def test_repo_file_stacks_on_user_file(tmp_tiramisu_home, tmp_workspace):
    """Both files present -> user list (replacing defaults) + repo list."""
    import research

    user_sources = [
        {"name": "User A", "url": "https://a.example.com", "focus": "x"},
        {"name": "User B", "url": "https://b.example.com", "focus": "y"},
    ]
    (tmp_tiramisu_home / "sources.json").write_text(
        json.dumps(user_sources), encoding="utf-8"
    )

    repo_dir = tmp_workspace / ".tiramisu"
    repo_dir.mkdir()
    repo_sources = [
        {"name": "Repo C", "url": "https://c.example.com", "focus": "z"},
    ]
    (repo_dir / "sources.json").write_text(
        json.dumps(repo_sources), encoding="utf-8"
    )

    sources = research.load_sources()
    names = [s["name"] for s in sources]
    # User sources present
    assert "User A" in names
    assert "User B" in names
    # Repo source present
    assert "Repo C" in names
    # NO defaults
    for default in research.DEFAULT_SOURCES:
        assert default["name"] not in names
    # Total = 3 (2 user + 1 repo)
    assert len(sources) == 3


# --------------------------------------------------------------------------
# Malformed input handling: never crash, fall back gracefully
# --------------------------------------------------------------------------

def test_malformed_user_json_falls_back_to_defaults(
    tmp_tiramisu_home, tmp_workspace, capsys
):
    """Invalid JSON in the user file should NOT crash load_sources();
    it should warn and fall back to defaults."""
    import research
    (tmp_tiramisu_home / "sources.json").write_text(
        "{ this is not valid json", encoding="utf-8"
    )

    sources = research.load_sources()
    # Should fall back to defaults rather than empty / crash
    assert len(sources) == len(research.DEFAULT_SOURCES)


def test_user_json_not_a_list_falls_back(tmp_tiramisu_home, tmp_workspace):
    """If user file is a JSON object instead of a list, ignore it."""
    import research
    (tmp_tiramisu_home / "sources.json").write_text(
        json.dumps({"oops": "this is an object not a list"}),
        encoding="utf-8",
    )
    sources = research.load_sources()
    assert len(sources) == len(research.DEFAULT_SOURCES)


def test_items_missing_url_are_filtered_out(tmp_tiramisu_home, tmp_workspace):
    """Each item must have name + url to be valid. Drop incomplete entries
    rather than crashing later when we try to fetch them."""
    import research
    user_sources = [
        {"name": "Valid", "url": "https://valid.example.com", "focus": "x"},
        {"name": "Missing URL"},                            # incomplete
        {"url": "https://no-name.example.com"},             # incomplete
        "not even a dict",                                  # wrong type
    ]
    (tmp_tiramisu_home / "sources.json").write_text(
        json.dumps(user_sources), encoding="utf-8"
    )

    sources = research.load_sources()
    names = [s["name"] for s in sources]
    assert names == ["Valid"], (
        f"expected only the valid item to survive filtering; got {names}"
    )


def test_focus_field_defaults_when_missing(tmp_tiramisu_home, tmp_workspace):
    """A user can omit `focus` -- _load_json_list() fills in a default
    so downstream code doesn't have to handle the missing-key case."""
    import research
    user_sources = [
        {"name": "Bare", "url": "https://bare.example.com"},
    ]
    (tmp_tiramisu_home / "sources.json").write_text(
        json.dumps(user_sources), encoding="utf-8"
    )
    sources = research.load_sources()
    assert len(sources) == 1
    assert "focus" in sources[0]
    assert sources[0]["focus"], "focus must be non-empty after default fill"
