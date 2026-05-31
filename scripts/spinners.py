"""
Custom rich spinners for Tiramisu's wait indicators.

Importing this module registers a handful of animal/pastry-themed spinners
with rich.spinner.SPINNERS. Use them by name in console.status() calls:

    from rich.console import Console
    import spinners  # registers
    console = Console()
    with console.status("thinking...", spinner="paws"):
        do_thing()

Selectable via TIRAMISU_SPINNER env var (defaults to "paws"):
    paws      walking paw prints with a fading trail
    chase     puppy running across the terminal
    pastries  pastries rotating like a bakery wheel
    naptime   cat sleeping with zzz building
    sniff     puppy sniffing left-to-right and back
"""
import os
from rich.spinner import SPINNERS


SPINNERS["paws"] = {
    "interval": 120,
    "frames": [
        "🐾",
        "🐾 ·",
        "🐾 · ·",
        "🐾 · · ·",
        "🐾 · · · ·",
        " 🐾 · · ·",
        "  🐾 · ·",
        "   🐾 ·",
        "    🐾",
        "     ",
    ],
}

SPINNERS["chase"] = {
    "interval": 100,
    "frames": [
        "🐶 · · · · · ·",
        " 🐶 · · · · ·",
        " · 🐶 · · · ·",
        " · · 🐶 · · ·",
        " · · · 🐶 · ·",
        " · · · · 🐶 ·",
        " · · · · · 🐶",
        " · · · · ·  ",
    ],
}

SPINNERS["pastries"] = {
    "interval": 180,
    "frames": ["🍮", "🥐", "🍪", "🧁", "🍩", "🍡", "🍞", "🍫"],
}

SPINNERS["naptime"] = {
    "interval": 280,
    "frames": [
        "🐱 z",
        "🐱 zz",
        "🐱 zzz",
        "🐱 zzz·",
        "🐱 zz · ",
        "🐱 z  · ",
    ],
}

SPINNERS["sniff"] = {
    "interval": 110,
    "frames": [
        "🐶 · · · · ·",
        " 🐶 · · · ·",
        "  🐶 · · ·",
        "   🐶 · ·",
        "    🐶 ·",
        "     🐶",
        "    🐶 ·",
        "   🐶 · ·",
        "  🐶 · · ·",
        " 🐶 · · · ·",
    ],
}


# Names that exist after this module imports
AVAILABLE = ("paws", "chase", "pastries", "naptime", "sniff")
DEFAULT = "paws"


def chosen() -> str:
    """Return the spinner the user wants, falling back to DEFAULT.

    Respects TIRAMISU_SPINNER env var. Validates against the registered set
    so a typo doesn't silently fall through to rich's built-in defaults.
    """
    name = (os.environ.get("TIRAMISU_SPINNER") or "").strip().lower()
    if name in AVAILABLE:
        return name
    # Allow rich's built-ins to be selected too (dots, dots2, line, ...) --
    # we don't gate those; if the user wants the boring dots, fine.
    if name and name in SPINNERS:
        return name
    return DEFAULT
