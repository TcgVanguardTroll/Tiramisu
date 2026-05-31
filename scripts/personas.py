"""
Single source of truth for agent emoji + colors.

All dogs share 🐶 (puppy). All cats share 🐱 (cat face). Each agent gets a
UNIQUE pastry emoji -- that's what differentiates them visually. Banners and
titles use the pair (pet + pastry); inline streaming uses just the pet.

If you want to retheme an agent, change it here -- every script imports from
this module.
"""

PERSONAS = {
    # name      pet     pastry  color (rich style name)
    "tiramisu":  {"pet": "🐶",  "pastry": "🍮",  "color": "cyan"},
    "eclair":    {"pet": "🦡",  "pastry": "🍫",  "color": "magenta"},
    "cookie":    {"pet": "🐱",  "pastry": "🍪",  "color": "yellow"},
    "croissant": {"pet": "🐶",  "pastry": "🥐",  "color": "bright_yellow"},
    "madeleine": {"pet": "🐱",  "pastry": "🧁",  "color": "orange3"},
    "cannoli":   {"pet": "🐶",  "pastry": "🍩",  "color": "blue"},
    "mochi":     {"pet": "🐰",  "pastry": "🍡",  "color": "white"},
    "brioche":   {"pet": "🐶",  "pastry": "🍞",  "color": "green"},
}


def pair(agent: str) -> str:
    """Pet + pastry for banners and titles, e.g. '🐶🍮'."""
    p = PERSONAS.get(agent.lower())
    return p["pet"] + p["pastry"] if p else ""


def pet(agent: str) -> str:
    """Pet only for inline / signature use, e.g. '🐶'."""
    p = PERSONAS.get(agent.lower())
    return p["pet"] if p else ""


def color(agent: str) -> str:
    """Rich color style name for this agent's accents."""
    p = PERSONAS.get(agent.lower())
    return p["color"] if p else "cyan"
