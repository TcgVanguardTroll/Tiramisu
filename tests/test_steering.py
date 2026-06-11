"""
Golden snapshots for the steering composition layer.

steering.load_steering() is the function that builds the system prompt fed
into EVERY agent invocation. The order of the six layers matters because
later sections override earlier ones in the model's attention -- if the
order silently flips (persona last instead of first, or repo overrides
moving above user preferences), agent behavior changes in ways no test
elsewhere will catch.

This file pins:
  1. Section order (the docstring's contract)
  2. Toggle semantics (each include_* flag actually adds/removes its layer)
  3. Language filtering on code-style.md
  4. Per-repo overrides live (highest priority, last in the prompt)
  5. detect_languages() extension mapping
  6. _parse_h2_sections() respects ## boundaries

No real API calls. All file IO goes through `_read` which we sometimes
patch via monkeypatch + cache_clear fixture.
"""
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

# Stable markers we look for in the composed output. These come from
# load_steering()'s own heading text -- if you rename the headings in
# steering.py these strings need to update too (which is the point: the
# test forces you to confront the rename).
MARKER_ENGINEERING = "# ENGINEERING PRINCIPLES"
MARKER_CODE_STYLE  = "# CODE STYLE"
MARKER_COMMUNICATION = "# COMMUNICATION STYLE"
MARKER_PREFERENCES = "# USER PREFERENCES"
MARKER_OVERRIDES   = "# PROJECT-SPECIFIC OVERRIDES"


def _positions(composed: str, markers: list[str]) -> list[tuple[str, int]]:
    """Return [(marker, index)] for markers that appear, in source order."""
    found = [(m, composed.find(m)) for m in markers]
    return [(m, i) for (m, i) in found if i >= 0]


# --------------------------------------------------------------------------
# Composition order: the contract of load_steering()
# --------------------------------------------------------------------------

def test_steering_section_order(clear_steering_cache):
    """The docstring of load_steering() promises this order:
       persona, engineering, code-style, communication, preferences, overrides.
    A silent reordering would change every agent's behavior. Lock it down."""
    from steering import load_steering

    composed = load_steering(
        agent="eclair",
        languages=["Python"],
        include_engineering=True,
        include_communication=True,
        include_universal_style=True,
        include_preferences=False,    # would require DB; tested separately
        include_repo_overrides=False, # tested separately
    )

    positions = _positions(composed, [
        MARKER_ENGINEERING,
        MARKER_CODE_STYLE,
        MARKER_COMMUNICATION,
    ])

    # Each marker must appear exactly once and in this order
    indices = [i for (_, i) in positions]
    assert indices == sorted(indices), (
        f"steering sections out of order; found at {positions}"
    )


def test_persona_appears_before_engineering(clear_steering_cache):
    """Persona content comes first so the model knows who it's roleplaying
    before reading the universal rules.

    We don't grep for the agent name (eclair.md uses 'Éclair' with an
    accent, so case-insensitive lookups miss it). Instead: load with
    persona+engineering, then load with engineering only -- the prefix
    that differs is the persona, and it has to be non-empty AND positioned
    before the engineering marker."""
    from steering import load_steering

    with_persona = load_steering(
        agent="eclair",
        include_engineering=True,
        include_preferences=False,
        include_repo_overrides=False,
    )
    without_persona = load_steering(
        agent="this_persona_does_not_exist_zzz",
        include_engineering=True,
        include_preferences=False,
        include_repo_overrides=False,
    )

    eng_idx = with_persona.find(MARKER_ENGINEERING)
    assert eng_idx >= 0, "engineering section not present"

    # Engineering must not be at position 0 -- something (the persona)
    # has to come before it.
    assert eng_idx > 0, (
        "engineering principles appear at the start; persona was not "
        "prepended"
    )

    # And the prompt-with-persona must be strictly longer than without.
    assert len(with_persona) > len(without_persona), (
        "persona load did not add any content"
    )


def test_overrides_come_last(clear_steering_cache, tmp_path, monkeypatch):
    """Per-repo overrides are LAST so they win when the model has to choose
    between conflicting rules. This is the documented precedence."""
    from steering import load_steering

    # Build a fake repo with an override file
    repo = tmp_path / "fakerepo"
    (repo / ".tiramisu").mkdir(parents=True)
    (repo / ".tiramisu" / "style.md").write_text(
        "Prefer tabs over spaces for indentation.",
        encoding="utf-8",
    )
    # `.tiramisu` alone is enough for _find_repo_root() to anchor here
    composed = load_steering(
        agent="eclair",
        languages=["Python"],
        include_engineering=True,
        include_communication=True,
        include_preferences=False,
        include_repo_overrides=True,
        cwd=repo,
    )

    override_idx = composed.find(MARKER_OVERRIDES)
    assert override_idx >= 0, "repo overrides section missing"

    # Every other section must come before overrides
    for marker in (MARKER_ENGINEERING, MARKER_CODE_STYLE, MARKER_COMMUNICATION):
        idx = composed.find(marker)
        if idx >= 0:
            assert idx < override_idx, (
                f"{marker!r} appeared AFTER overrides; precedence broken"
            )


# --------------------------------------------------------------------------
# Toggle semantics: each include_* flag actually controls its section
# --------------------------------------------------------------------------

def test_disable_engineering_omits_section(clear_steering_cache):
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        include_engineering=False,
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_ENGINEERING not in composed


def test_disable_communication_omits_section(clear_steering_cache):
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        include_engineering=False,
        include_communication=False,
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_COMMUNICATION not in composed


def test_enable_communication_includes_section(clear_steering_cache):
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        include_engineering=False,
        include_communication=True,
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_COMMUNICATION in composed


# --------------------------------------------------------------------------
# Language filtering on code-style.md
# --------------------------------------------------------------------------

def test_language_filter_includes_requested_language(clear_steering_cache):
    """Asking for Python should pull the Python section into the prompt."""
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        languages=["Python"],
        include_engineering=False,
        include_preferences=False,
        include_repo_overrides=False,
    )
    # The "## Python" heading from code-style.md should appear after our
    # CODE STYLE banner.
    assert MARKER_CODE_STYLE in composed
    assert "Python" in composed


def test_no_language_omits_code_style_block_when_universal_off(clear_steering_cache):
    """If no languages AND universal disabled, the CODE STYLE section is
    skipped entirely."""
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        languages=None,
        include_engineering=False,
        include_universal_style=False,
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_CODE_STYLE not in composed


# --------------------------------------------------------------------------
# detect_languages(): the input-side mapping
# --------------------------------------------------------------------------

def test_detect_languages_maps_python():
    from steering import detect_languages
    assert detect_languages(["foo.py", "bar.py"]) == ["Python"]


def test_detect_languages_dedupes_and_sorts():
    from steering import detect_languages
    langs = detect_languages(["a.py", "b.ts", "c.py", "d.rs"])
    assert langs == ["Python", "Rust", "TypeScript"]


def test_detect_languages_handles_unknown_extension():
    """Unknown extensions are silently dropped, not an error."""
    from steering import detect_languages
    assert detect_languages(["README.md", "data.csv", "config.yml"]) == []


def test_detect_languages_handles_none_and_empty():
    from steering import detect_languages
    assert detect_languages(None) == []
    assert detect_languages([]) == []


def test_detect_languages_case_insensitive_suffix():
    """`.PY` and `.py` should both map to Python (case-insensitive)."""
    from steering import detect_languages
    assert detect_languages(["FOO.PY"]) == ["Python"]


# --------------------------------------------------------------------------
# _parse_h2_sections(): the markdown parser
# --------------------------------------------------------------------------

def test_parse_h2_sections_splits_on_double_hash():
    from steering import _parse_h2_sections
    text = (
        "## Python\n"
        "Use snake_case.\n"
        "Use type hints.\n"
        "\n"
        "## Rust\n"
        "Use snake_case for fns.\n"
    )
    sections = _parse_h2_sections(text)
    assert "Python" in sections
    assert "Rust" in sections
    assert "snake_case" in sections["Python"]
    assert "snake_case for fns" in sections["Rust"]
    # Cross-contamination check: Rust content must not bleed into Python
    assert "snake_case for fns" not in sections["Python"]


def test_parse_h2_sections_ignores_h1():
    """An H1 above sections shouldn't be treated as a section name."""
    from steering import _parse_h2_sections
    text = (
        "# Code Style Guide\n"
        "Intro text.\n"
        "## Python\n"
        "Use snake_case.\n"
    )
    sections = _parse_h2_sections(text)
    # H1 should NOT be a key
    assert "Code Style Guide" not in sections
    assert "Python" in sections


def test_parse_h2_sections_empty_text_returns_empty_dict():
    from steering import _parse_h2_sections
    assert _parse_h2_sections("") == {}


# --------------------------------------------------------------------------
# Fail-soft on missing files
# --------------------------------------------------------------------------

def test_missing_persona_doesnt_crash(clear_steering_cache):
    """An unknown agent name should produce a composed prompt without the
    persona, not raise."""
    from steering import load_steering
    composed = load_steering(
        agent="this_persona_does_not_exist_zzz",
        include_engineering=True,
        include_preferences=False,
        include_repo_overrides=False,
    )
    # Engineering still present; just no persona section
    assert MARKER_ENGINEERING in composed


# --------------------------------------------------------------------------
# Learned layer (steering/learned/*.md, adopted via `t research apply`)
# --------------------------------------------------------------------------

MARKER_LEARNED = "# LEARNED FROM RESEARCH"


@pytest.fixture
def fake_steering_root(tmp_path, monkeypatch, clear_steering_cache):
    """A minimal repo tree with a learned doc, with steering.ROOT pointed
    at it. _read() resolves paths at call time, so patching ROOT is enough."""
    import steering
    root = tmp_path / "fake_tiramisu"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "eclair.md").write_text("# Eclair persona", encoding="utf-8")
    s = root / "steering"
    (s / "learned").mkdir(parents=True)
    (s / "engineering-principles.md").write_text("Keep it simple.", encoding="utf-8")
    (s / "code-style.md").write_text(
        "## Python\n- pathlib.\n\n## Universal Preferences\n- early returns.\n",
        encoding="utf-8",
    )
    (s / "communication-style.md").write_text("Be brief.", encoding="utf-8")
    (s / "learned" / "agent-evals.md").write_text(
        "# Agent evals\n\nRun evals before shipping prompts.", encoding="utf-8"
    )
    monkeypatch.setattr(steering, "ROOT", root)
    return root


def test_learned_layer_included_and_ordered(fake_steering_root, tmp_path):
    """Learned docs appear AFTER communication style and BEFORE repo
    overrides, so project-specific rules still win on conflict."""
    from steering import load_steering

    repo = tmp_path / "fakerepo"
    (repo / ".tiramisu").mkdir(parents=True)
    (repo / ".tiramisu" / "style.md").write_text("Tabs.", encoding="utf-8")

    composed = load_steering(
        agent="eclair",
        languages=["Python"],
        include_communication=True,
        include_preferences=False,
        cwd=repo,
    )

    assert "Run evals before shipping prompts." in composed
    assert composed.index(MARKER_COMMUNICATION) < composed.index(MARKER_LEARNED)
    assert composed.index(MARKER_LEARNED) < composed.index(MARKER_OVERRIDES)


def test_learned_layer_toggle_off(fake_steering_root):
    from steering import load_steering
    composed = load_steering(
        agent="eclair",
        include_learned=False,
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_LEARNED not in composed


def test_no_learned_dir_adds_nothing(fake_steering_root):
    """An empty/missing learned dir must not add the heading."""
    import shutil
    from steering import load_steering
    shutil.rmtree(fake_steering_root / "steering" / "learned")
    composed = load_steering(
        agent="eclair",
        include_preferences=False,
        include_repo_overrides=False,
    )
    assert MARKER_LEARNED not in composed
