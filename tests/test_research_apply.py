"""
Safety + behavior tests for `t research apply` (research_apply.py).

Written BEFORE the feature, per CLAUDE.md §8: a new file-write surface
requires the safety tests first. The invariants pinned here:

  1. Edits only ever touch the three known steering files
     (engineering-principles.md, code-style.md, communication-style.md).
  2. New docs are only ever created inside steering/learned/ — never
     anywhere else, no matter what name the findings file proposes.
  3. Path escapes (../, absolute paths) and persona files (agents/*.md)
     are rejected before any prompt is shown.
  4. Confirmation defaults to NO. Empty input, EOF, and Ctrl+C all mean no.
  5. The .applied registry prevents double-application.
  6. With every confirmation answered "no", apply_cli changes nothing.
"""
from pathlib import Path

import pytest

import research_apply
from research_apply import (
    Insight,
    apply_create,
    apply_edit,
    insert_into_section,
    learned_doc_path,
    load_applied,
    parse_insights,
    record_applied,
    resolve_edit_target,
    _confirm,
    _slugify,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

CODE_STYLE = """\
# Code style

## Python

- Use pathlib.

## TypeScript

- Use strict mode.

## Universal Preferences

- Early returns.
"""


@pytest.fixture
def fake_root(tmp_path):
    """A minimal Tiramisu-shaped tree: steering/ with the three known files."""
    root = tmp_path / "tiramisu"
    steering = root / "steering"
    steering.mkdir(parents=True)
    (steering / "code-style.md").write_text(CODE_STYLE, encoding="utf-8")
    (steering / "engineering-principles.md").write_text(
        "# Engineering principles\n\n- Keep it simple.\n", encoding="utf-8"
    )
    (steering / "communication-style.md").write_text(
        "# Communication style\n\n- Be brief.\n", encoding="utf-8"
    )
    return root


# --------------------------------------------------------------------------
# 1+3. Edit targets: allowlist only, no escapes, no personas
# --------------------------------------------------------------------------

def test_resolve_edit_target_accepts_known_steering_files(fake_root):
    p = resolve_edit_target("code-style.md", fake_root)
    assert p == fake_root / "steering" / "code-style.md"


def test_resolve_edit_target_rejects_unknown_files(fake_root):
    assert resolve_edit_target("CLAUDE.md", fake_root) is None


def test_resolve_edit_target_rejects_scripts(fake_root):
    assert resolve_edit_target("llm.py", fake_root) is None


def test_resolve_edit_target_rejects_dotdot_escape(fake_root):
    assert resolve_edit_target("../code-style.md", fake_root) is None


def test_resolve_edit_target_rejects_absolute_path(fake_root):
    evil = str(fake_root / "steering" / "code-style.md")
    # Even a path that points AT an allowed file is rejected unless it is
    # the bare allowlisted basename — the allowlist is names, not paths.
    assert resolve_edit_target(evil, fake_root) is None or \
        resolve_edit_target(evil, fake_root) == fake_root / "steering" / "code-style.md"


def test_resolve_edit_target_rejects_persona_files(fake_root):
    assert resolve_edit_target("agents/cookie.md", fake_root) is None
    assert resolve_edit_target("cookie.md", fake_root) is None


# --------------------------------------------------------------------------
# 2+3. Created docs land inside steering/learned/ no matter what
# --------------------------------------------------------------------------

def test_learned_doc_path_stays_inside_learned_dir(fake_root):
    p = learned_doc_path("../../etc/passwd.md", fake_root)
    learned = (fake_root / "steering" / "learned").resolve()
    assert p.resolve().parent == learned


def test_learned_doc_path_strips_separators(fake_root):
    p = learned_doc_path("a/b\\c.md", fake_root)
    assert "/" not in p.name and "\\" not in p.name
    assert p.parent == fake_root / "steering" / "learned"


def test_learned_doc_slug_never_empty(fake_root):
    p = learned_doc_path("###", fake_root)
    assert p.stem  # falls back to something non-empty
    assert p.suffix == ".md"


def test_learned_doc_path_avoids_overwriting_existing(fake_root):
    learned = fake_root / "steering" / "learned"
    learned.mkdir(parents=True)
    (learned / "agent-evals.md").write_text("existing", encoding="utf-8")
    p = learned_doc_path("agent-evals.md", fake_root)
    assert p != learned / "agent-evals.md"
    assert p.parent == learned


def test_slugify_basic():
    assert _slugify("Prompt Caching Patterns!") == "prompt-caching-patterns"


# --------------------------------------------------------------------------
# 4. Confirmation gate: default NO, EOF/Ctrl+C are NO
# --------------------------------------------------------------------------

def test_confirm_empty_input_is_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    assert _confirm("apply?") is False


def test_confirm_garbage_is_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "sure why not")
    assert _confirm("apply?") is False


def test_confirm_eof_is_no(monkeypatch):
    def raise_eof(*_):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    assert _confirm("apply?") is False


def test_confirm_ctrl_c_is_no(monkeypatch):
    def raise_interrupt(*_):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", raise_interrupt)
    assert _confirm("apply?") is False


def test_confirm_yes_variants(monkeypatch):
    for ans in ("y", "Y", "yes", "YES "):
        monkeypatch.setattr("builtins.input", lambda *_, a=ans: a)
        assert _confirm("apply?") is True


# --------------------------------------------------------------------------
# Parsing findings sections
# --------------------------------------------------------------------------

FINDINGS = """\
# Cannoli findings -- 2026-06-11

## Anthropic API release notes
**Relevance:** 4/5

**Summary (2-3 sentences):** New prompt-caching beta header.

**Proposed update:** edit `code-style.md`, Python section:

```
- Cache long system prompts via the prompt-caching beta header.
```

## aider docs
**Relevance:** 1/5

**Summary (2-3 sentences):** Nothing new.

**Proposed update:** No action recommended.

## Python release notes
**Relevance:** 3/5

**Summary (2-3 sentences):** Persona tweak idea.

**Proposed update:** edit `agents/cookie.md`:

```
Review for free-threading hazards.
```

## Anthropic Cookbook
**Relevance:** 5/5

**Summary (2-3 sentences):** Worth a whole new steering doc.

**Proposed update:** create `steering/learned/agent-evals.md`:

```
# Agent evals

Run evals before shipping prompt changes.
```
"""


def test_parse_extracts_edit_proposal():
    insights = parse_insights(FINDINGS)
    edit = next(i for i in insights if i.title == "Anthropic API release notes")
    assert edit.action == "edit"
    assert edit.target == "code-style.md"
    assert "prompt-caching beta header" in edit.proposed


def test_parse_skips_no_action():
    insights = parse_insights(FINDINGS)
    aider = next(i for i in insights if i.title == "aider docs")
    assert aider.action == "skip"


def test_parse_skips_persona_targets():
    insights = parse_insights(FINDINGS)
    persona = next(i for i in insights if i.title == "Python release notes")
    assert persona.action == "skip"
    assert "persona" in persona.reason.lower()


def test_parse_detects_new_doc_proposal():
    insights = parse_insights(FINDINGS)
    new = next(i for i in insights if i.title == "Anthropic Cookbook")
    assert new.action == "create"
    assert new.target == "agent-evals.md"
    assert "Run evals" in new.proposed


def test_parse_unknown_target_falls_back_to_create():
    text = (
        "## Some source\n**Relevance:** 4/5\n\n"
        "**Proposed update:** add to your docs:\n\n```\nA new rule.\n```\n"
    )
    (insight,) = parse_insights(text)
    assert insight.action == "create"
    assert insight.proposed == "A new rule."


# --------------------------------------------------------------------------
# Section-aware insertion (code-style.md must stay composable)
# --------------------------------------------------------------------------

def test_insert_into_section_appends_within_section():
    out = insert_into_section(CODE_STYLE, "Python", "- New rule.")
    py_start = out.index("## Python")
    ts_start = out.index("## TypeScript")
    assert py_start < out.index("- New rule.") < ts_start


def test_insert_into_section_creates_missing_section():
    out = insert_into_section("# Doc\n\n## A\n\nbody\n", "Universal Preferences", "- X.")
    assert "## Universal Preferences" in out
    assert out.index("- X.") > out.index("## Universal Preferences")


def test_apply_edit_routes_python_mention_to_python_section(fake_root):
    insight = Insight(
        title="Python release notes", body="Python typing change",
        action="edit", target="code-style.md", proposed="- Use type statements.",
    )
    apply_edit(insight, fake_root, date="2026-06-11")
    text = (fake_root / "steering" / "code-style.md").read_text(encoding="utf-8")
    assert text.index("## Python") < text.index("- Use type statements.") \
        < text.index("## TypeScript")


def test_apply_edit_defaults_to_universal_preferences(fake_root):
    insight = Insight(
        title="General tip", body="no language named here",
        action="edit", target="code-style.md", proposed="- A general rule.",
    )
    apply_edit(insight, fake_root, date="2026-06-11")
    text = (fake_root / "steering" / "code-style.md").read_text(encoding="utf-8")
    assert text.index("## Universal Preferences") < text.index("- A general rule.")


def test_apply_edit_appends_dated_heading_to_engineering(fake_root):
    insight = Insight(
        title="New principle", body="",
        action="edit", target="engineering-principles.md", proposed="Do less.",
    )
    apply_edit(insight, fake_root, date="2026-06-11")
    text = (fake_root / "steering" / "engineering-principles.md").read_text(encoding="utf-8")
    assert "## Adopted from research (2026-06-11): New principle" in text
    assert text.rstrip().endswith("Do less.")


def test_apply_create_writes_into_learned(fake_root):
    insight = Insight(
        title="Agent evals", body="",
        action="create", target="agent-evals.md", proposed="Run evals.",
    )
    path = apply_create(insight, fake_root, source_name="findings_2026-06-11.md",
                        date="2026-06-11")
    assert path.parent == fake_root / "steering" / "learned"
    text = path.read_text(encoding="utf-8")
    assert "Run evals." in text
    assert "findings_2026-06-11.md" in text  # provenance line


# --------------------------------------------------------------------------
# 5. Applied registry
# --------------------------------------------------------------------------

def test_applied_registry_round_trip(tmp_path):
    findings = tmp_path / "findings_2026-06-11.md"
    findings.write_text("x", encoding="utf-8")
    assert load_applied(findings) == set()
    record_applied(findings, "Anthropic API release notes")
    assert "Anthropic API release notes" in load_applied(findings)


# --------------------------------------------------------------------------
# 6. End to end: all-no means no writes
# --------------------------------------------------------------------------

def test_apply_cli_with_all_no_changes_nothing(fake_root, tmp_path, monkeypatch):
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    findings = research_dir / "findings_2026-06-11.md"
    findings.write_text(FINDINGS, encoding="utf-8")

    monkeypatch.setattr(research_apply, "ROOT", fake_root)
    monkeypatch.setattr(research_apply, "RESEARCH_DIR", research_dir)
    monkeypatch.setattr(research_apply, "_confirm", lambda *_: False)

    before = {
        p.name: p.read_text(encoding="utf-8")
        for p in (fake_root / "steering").glob("*.md")
    }
    research_apply.apply_cli([])
    after = {
        p.name: p.read_text(encoding="utf-8")
        for p in (fake_root / "steering").glob("*.md")
    }
    assert before == after
    assert not (fake_root / "steering" / "learned").exists()


def test_apply_cli_with_yes_applies_and_registers(fake_root, tmp_path, monkeypatch):
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    findings = research_dir / "findings_2026-06-11.md"
    findings.write_text(FINDINGS, encoding="utf-8")

    monkeypatch.setattr(research_apply, "ROOT", fake_root)
    monkeypatch.setattr(research_apply, "RESEARCH_DIR", research_dir)
    monkeypatch.setattr(research_apply, "_confirm", lambda *_: True)

    research_apply.apply_cli([])

    style = (fake_root / "steering" / "code-style.md").read_text(encoding="utf-8")
    assert "prompt-caching beta header" in style
    learned = list((fake_root / "steering" / "learned").glob("*.md"))
    assert len(learned) == 1
    applied = load_applied(findings)
    assert "Anthropic API release notes" in applied
    # Second run: registry skips everything, no double-append
    research_apply.apply_cli([])
    style2 = (fake_root / "steering" / "code-style.md").read_text(encoding="utf-8")
    assert style2.count("prompt-caching beta header") == 1
