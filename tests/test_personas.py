"""
Persona invariants. Cheap to test, load-bearing if they regress.

These are checked at import time elsewhere via duck-typing, but a duplicate
pastry emoji would break the visual differentiator (see README "The Crew")
and a missing `pet` key would crash dispatch.py's banner. Both worth
locking down.
"""
from personas import PERSONAS, pair, pet, color


def test_every_persona_has_required_fields():
    for name, p in PERSONAS.items():
        assert "pet" in p,    f"{name} missing 'pet'"
        assert "pastry" in p, f"{name} missing 'pastry'"
        assert "color" in p,  f"{name} missing 'color'"


def test_pastries_are_unique():
    """The pastry emoji is the visual differentiator across agents.
    Duplication would break the README's promise of uniqueness."""
    pastries = [p["pastry"] for p in PERSONAS.values()]
    assert len(pastries) == len(set(pastries)), (
        f"duplicate pastry emoji found: {sorted(pastries)}"
    )


def test_pet_emoji_groups_make_sense():
    """All dogs share 🐶 and all cats share 🐱 by design; that's a deliberate
    grouping, not an accident. If someone removes that rule, fail loudly."""
    pets = [p["pet"] for p in PERSONAS.values()]
    n_dogs = pets.count("🐶")
    n_cats = pets.count("🐱")
    assert n_dogs >= 2, f"expected multiple dog agents, got {n_dogs}"
    assert n_cats >= 2, f"expected multiple cat agents, got {n_cats}"


def test_pair_returns_pet_concatenated_with_pastry():
    for name, p in PERSONAS.items():
        assert pair(name) == p["pet"] + p["pastry"]


def test_pair_unknown_agent_returns_empty_string():
    """An unknown agent should not raise -- callers concatenate this into
    output strings and a KeyError would crash banners."""
    assert pair("nonexistent_agent") == ""


def test_pet_returns_just_the_pet():
    for name, p in PERSONAS.items():
        assert pet(name) == p["pet"]


def test_pet_unknown_agent_returns_empty_string():
    assert pet("nonexistent_agent") == ""


def test_color_returns_a_color_for_every_persona():
    for name in PERSONAS:
        assert color(name), f"{name} has empty color"


def test_color_unknown_agent_returns_default():
    """Unknown agent should fall back to the documented default (cyan)
    so rich.print() never crashes on an empty style name."""
    assert color("unknown") == "cyan"
