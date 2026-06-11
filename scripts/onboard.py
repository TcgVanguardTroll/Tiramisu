#!/usr/bin/env python3
"""
Onboard a new agent with Brioche — draft a persona for an unmet need.

Brioche takes a description of a job the crew can't cover, drafts an
`agents/<name>.md` persona following the house template, and shows it for
approval. Nothing is written without confirmation, existing personas are
never overwritten, and the CLI wiring (personas.py entry, README row,
`t` command) stays a manual follow-up — Brioche builds people, not plumbing.

Usage:
    t onboard "we need an agent that reviews infra / Terraform changes"
    t onboard                     # interactive prompt
"""
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream_markdown, DEFAULT_MODEL
from steering import load_steering
from personas import pair as persona_pair, PERSONAS

AGENTS_DIR = ROOT / "agents"

PROMPT_TEMPLATE = """\
We have an unmet need on the crew: {need}

Draft the persona file for a new agent that covers it. Follow the house
template EXACTLY (below). Before drafting, decide: is this genuinely a
distinct job, or should an existing agent be extended instead? If extending
is right, say so and propose that instead of a new persona.

--- HOUSE TEMPLATE (from agents/README.md) ---
{template}
--- END TEMPLATE ---

The existing crew (do not overlap their jobs, do not reuse their names or
pastry emoji):
{crew}

Rules:
- Persona only: WHO the agent is and WHAT they do. No tool instructions,
  no code, no "how it works" — that lives in scripts/.
- Pastry-pet themed name, unique pastry emoji suggestion.
- Include a "Voice examples" section with at least one pushing-back line.
- End the file with: > Status: planned -- not yet wired into the t CLI
- Output the COMPLETE persona file in ONE fenced ```markdown block.
- After the block, suggest (outside the fence): the scripts/personas.py
  entry line and the README crew-table row.
"""


def _slugify(name: str) -> str:
    """Lowercase, alphanumerics and single hyphens only. Hostile input
    ('../../etc/passwd') degrades to a harmless slug, never a path."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def extract_name(draft: str) -> str | None:
    """Agent name from the persona's H1: '# <Name> — <role>' (or '--')."""
    m = re.search(r"^#\s+([^\n—-]+?)\s*(?:—|--)", draft, re.MULTILINE)
    return m.group(1).strip() if m else None


def extract_persona_block(text: str) -> str | None:
    """The persona file content from the model's output: prefer the first
    fenced block; fall back to everything from the first H1 onward."""
    fence = re.search(r"```(?:markdown|md)?\n(.*?)```", text, re.DOTALL)
    if fence:
        block = fence.group(1).strip()
        return block or None
    heading = re.search(r"^#\s+.+$", text, re.MULTILINE)
    if heading:
        return text[heading.start():].strip()
    return None


def save_persona(name: str, content: str) -> Path:
    """Write agents/<slug>.md. Raises ValueError on unusable names and
    FileExistsError rather than ever overwriting an existing persona."""
    slug = _slugify(name)
    if not slug:
        raise ValueError(f"could not derive a filename from {name!r}")
    path = (AGENTS_DIR / f"{slug}.md").resolve()
    if path.parent != AGENTS_DIR.resolve():
        raise ValueError(f"refusing to write outside agents/: {path}")
    if path.exists():
        raise FileExistsError(
            f"{path.name} already exists -- Brioche proposes edits to "
            f"existing agents, never rewrites them."
        )
    path.write_text(content if content.endswith("\n") else content + "\n",
                    encoding="utf-8")
    return path


def _crew_summary() -> str:
    """Name + emoji pair per existing agent, so the draft avoids collisions."""
    lines = []
    for name, p in PERSONAS.items():
        lines.append(f"  - {name} {p['pet']}{p['pastry']}")
    return "\n".join(lines)


def _house_template() -> str:
    readme = AGENTS_DIR / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return "(template unavailable -- follow the shape of cookie.md)"
    m = re.search(r"```markdown\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text[:2000]


def main():
    system = load_steering(
        agent="brioche",
        languages=None,
        include_engineering=False,
        include_universal_style=False,
        include_preferences=True,
    )

    print("=" * 60)
    print(f"{persona_pair('brioche')}  Brioche — New Agent Onboarding")
    print("=" * 60)
    print()

    if len(sys.argv) > 1:
        need = " ".join(sys.argv[1:]).strip()
        print(f"Unmet need: {need}\n")
    else:
        print("Describe the unmet need. What job can't the current crew do?\n")
        try:
            need = input("Need: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

    if not need:
        print("No need described. Exiting.")
        sys.exit(0)

    print(f"\n[brioche] {persona_pair('brioche')} Drafting the persona...\n")
    print("-" * 60)

    raw = invoke_stream_markdown(
        prompt=PROMPT_TEMPLATE.format(
            need=need,
            template=_house_template(),
            crew=_crew_summary(),
        ),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=8000,
        thinking=True,
    )

    print("-" * 60)

    draft = extract_persona_block(raw)
    if not draft:
        print("\nBrioche didn't produce a persona file (maybe she recommended "
              "extending an existing agent instead). Nothing written.\n")
        return

    name = extract_name(draft)
    if not name:
        print("\nCouldn't find the agent's name in the draft heading. "
              "Nothing written -- copy what you want by hand.\n")
        return

    print()
    try:
        save = input(f"Save as agents/{_slugify(name)}.md? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        save = "n"

    if save != "y":
        print("Not saved. Copy what you want from the draft above.\n")
        return

    try:
        path = save_persona(name, draft)
    except (ValueError, FileExistsError) as e:
        print(f"\n[onboard] not saved: {e}\n")
        return

    print(f"\n✓ Saved → {path}\n")
    print("Follow-ups (manual, on purpose -- see agents/README.md):")
    print("  1. Add the agent to scripts/personas.py (pet + pastry + color)")
    print("  2. Add the row to the README crew table")
    print("  3. If it needs a CLI surface: CLAUDE.md §5 'Add a new t <command>'")
    print(f"\n{persona_pair('brioche')}  Welcome aboard, {name}!\n")


if __name__ == "__main__":
    main()
