"""
Safety surface for Brioche's persona writer (onboard.py).

onboard.py is a file-WRITE surface driven by model output, so per
CLAUDE.md §8 these invariants exist before the feature ships:

  - persona files can only land inside agents/ (hostile names get
    slugified into harmless ones, never path-traversed)
  - an existing persona is NEVER overwritten (Brioche proposes, doesn't
    rewrite -- the §4.3 boundary)
  - empty / unusable names are rejected, not written
"""
import pytest

import onboard
from onboard import _slugify, extract_name, extract_persona_block, save_persona


# --------------------------------------------------------------------------
# Slug sanitization: hostile names cannot escape agents/
# --------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("Pretzel") == "pretzel"
    assert _slugify("Pain au Chocolat") == "pain-au-chocolat"


def test_slugify_strips_path_characters():
    assert "/" not in _slugify("../../etc/passwd")
    assert "\\" not in _slugify("..\\..\\windows")
    assert ".." not in _slugify("../../etc/passwd")


def test_slugify_empty_and_symbol_only_names():
    assert _slugify("") == ""
    assert _slugify("///..\\\\") == ""


def test_save_persona_rejects_unusable_name(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "AGENTS_DIR", tmp_path)
    with pytest.raises(ValueError):
        save_persona("///", "# x\ncontent")


def test_save_persona_stays_inside_agents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "AGENTS_DIR", tmp_path)
    path = save_persona("../../sneaky", "# Sneaky — test\ncontent")
    assert path.parent == tmp_path
    assert path.name == "sneaky.md"


# --------------------------------------------------------------------------
# Never overwrite: Brioche proposes, never rewrites
# --------------------------------------------------------------------------

def test_save_persona_refuses_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "AGENTS_DIR", tmp_path)
    (tmp_path / "cookie.md").write_text("# Cookie — reviewer\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_persona("Cookie", "# Cookie — impostor\n")


def test_save_persona_writes_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "AGENTS_DIR", tmp_path)
    path = save_persona("Pretzel", "# Pretzel — twisty\ncontent\n")
    assert path.read_text(encoding="utf-8").startswith("# Pretzel")


# --------------------------------------------------------------------------
# Draft parsing helpers
# --------------------------------------------------------------------------

def test_extract_name_from_heading():
    assert extract_name("# Pretzel — Infra Reviewer\n\nbody") == "Pretzel"
    assert extract_name("# Pretzel -- Infra Reviewer\n") == "Pretzel"


def test_extract_name_missing_returns_none():
    assert extract_name("no heading here") is None


def test_extract_persona_block_prefers_fenced_markdown():
    text = "Here you go!\n```markdown\n# Pretzel — test\nbody\n```\nanything after"
    assert extract_persona_block(text) == "# Pretzel — test\nbody"


def test_extract_persona_block_falls_back_to_heading():
    """No fence: take everything from the first H1 heading onward."""
    text = "Sure! Draft below.\n\n# Pretzel — test\nbody line"
    block = extract_persona_block(text)
    assert block.startswith("# Pretzel")
    assert "body line" in block


def test_extract_persona_block_none_when_no_persona():
    assert extract_persona_block("I couldn't draft anything.") is None
