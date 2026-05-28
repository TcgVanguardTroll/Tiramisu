"""
Steering composition layer for Tiramisu agents.

Every agent invocation should build its system prompt from:
  1. The agent's persona file (agents/<name>.md)
  2. engineering-principles.md (universal rules) -- optional
  3. The relevant language sections of code-style.md -- optional, filtered
  4. communication-style.md -- optional (for commit/review tone)
  5. Active user preferences from learnings.db

The point: each agent gets a 5x stronger system prompt assembled from the
high-quality steering docs the user already wrote, rather than a generic
persona alone.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Map file extensions to code-style.md section names
EXT_TO_LANG = {
    ".py":   "Python",
    ".java": "Java",
    ".kt":   "Java",       # close enough for style
    ".rs":   "Rust",
    ".ts":   "TypeScript",
    ".tsx":  "TypeScript",
    ".js":   "TypeScript",
    ".jsx":  "TypeScript",
}


def detect_languages(files):
    """From a list of file paths, return sorted list of language section names."""
    langs = set()
    for f in files or []:
        suffix = Path(f).suffix.lower()
        if suffix in EXT_TO_LANG:
            langs.add(EXT_TO_LANG[suffix])
    return sorted(langs)


def _parse_h2_sections(text):
    """
    Parse a markdown file by `## ` headings. Returns {heading_name: section_text}.
    The section_text includes the heading line.
    """
    sections = {}
    current_name = None
    current_lines = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).rstrip()
            current_name = line[3:].strip()
            current_lines = [line]
        elif line.startswith("# "):
            # Top-level H1 -- flush and skip
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).rstrip()
                current_name = None
                current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).rstrip()

    return sections


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _code_style_for(languages, include_universal=True):
    """Extract the relevant language sections from code-style.md."""
    if not languages and not include_universal:
        return ""

    text = _read(ROOT / "code-style.md")
    if not text:
        return ""

    sections = _parse_h2_sections(text)

    chunks = []
    for lang in languages or []:
        if lang in sections:
            chunks.append(sections[lang])

    if include_universal and "Universal Preferences" in sections:
        chunks.append(sections["Universal Preferences"])

    return "\n\n".join(chunks)


def _load_preferences():
    """Pull active preferences from learnings.db. Fail-soft."""
    try:
        import memory
        prefs = memory.get_active_preferences()
        if not prefs:
            return ""
        lines = [f"- [{p['category'] or 'general'}] {p['text']}" for p in prefs]
        return "\n".join(lines)
    except Exception:
        return ""


def load_steering(
    agent,
    languages=None,
    include_engineering=True,
    include_communication=False,
    include_universal_style=True,
    include_preferences=True,
):
    """
    Build a composed system prompt for an agent.

    Args:
      agent: name of the agent file in agents/ (without .md)
      languages: list of languages to filter code-style.md by, e.g. ["Python"]
                 if None, no language-specific style is loaded
      include_engineering: include engineering-principles.md
      include_communication: include communication-style.md
      include_universal_style: include the "Universal Preferences" section of code-style.md
      include_preferences: append learned preferences from learnings.db

    Returns:
      A single string ready to be passed as `system=` to the LLM.
    """
    parts = []

    # 1. Persona
    persona = _read(ROOT / "agents" / f"{agent}.md")
    if persona:
        parts.append(persona.strip())

    # 2. Engineering principles
    if include_engineering:
        eng = _read(ROOT / "engineering-principles.md")
        if eng:
            parts.append("\n# ENGINEERING PRINCIPLES\n\n" + eng.strip())

    # 3. Code style (filtered by language)
    style = _code_style_for(languages, include_universal=include_universal_style)
    if style:
        scope = f" ({', '.join(languages)})" if languages else ""
        parts.append(f"\n# CODE STYLE{scope}\n\n" + style.strip())

    # 4. Communication style
    if include_communication:
        comm = _read(ROOT / "communication-style.md")
        if comm:
            parts.append("\n# COMMUNICATION STYLE\n\n" + comm.strip())

    # 5. Active preferences
    if include_preferences:
        prefs = _load_preferences()
        if prefs:
            parts.append(
                "\n# USER PREFERENCES (learned over time -- respect these)\n\n" + prefs
            )

    return "\n\n".join(parts)
