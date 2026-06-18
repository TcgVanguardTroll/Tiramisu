"""
Steering composition layer for Tiramisu agents.

Every agent invocation should build its system prompt from:
  1. The agent's persona file (agents/<name>.md)
  2. steering/engineering-principles.md (universal rules) -- optional
  3. The relevant language sections of steering/code-style.md -- optional, filtered
  4. steering/communication-style.md -- optional (for commit/review tone)
  5. steering/learned/*.md -- docs adopted via `t research apply` (user-approved)
  6. Active user preferences from learnings.db

The point: each agent gets a 5x stronger system prompt assembled from the
high-quality steering docs the user already wrote, rather than a generic
persona alone.
"""
import sys
from functools import lru_cache
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


def detect_languages(files: list[str] | None) -> list[str]:
    """From a list of file paths, return sorted list of language section names."""
    langs = set()
    for f in files or []:
        suffix = Path(f).suffix.lower()
        if suffix in EXT_TO_LANG:
            langs.add(EXT_TO_LANG[suffix])
    return sorted(langs)


def _parse_h2_sections(text: str) -> dict[str, str]:
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


@lru_cache(maxsize=16)
def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[tiramisu] steering: could not read {path}: {e}", file=sys.stderr)
        return ""


def _code_style_for(languages: list[str] | None, include_universal: bool = True) -> str:
    """Extract the relevant language sections from code-style.md."""
    if not languages and not include_universal:
        return ""

    text = _read(ROOT / "steering" / "code-style.md")
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


def _load_learned() -> str:
    """Concatenate steering/learned/*.md -- docs the user adopted from
    Cannoli's research via `t research apply`. Sorted for a stable prompt
    cache. Missing dir (the common case) returns ""."""
    learned_dir = ROOT / "steering" / "learned"
    if not learned_dir.is_dir():
        return ""
    chunks = [
        _read(f).strip()
        for f in sorted(learned_dir.glob("*.md"))
        if _read(f).strip()
    ]
    return "\n\n".join(chunks)


def _load_preferences() -> str:
    """Pull active preferences from learnings.db. Fail-soft."""
    try:
        import memory
        prefs = memory.get_active_preferences()
        if not prefs:
            return ""
        lines = []
        for p in prefs:
            line = f"- [{p['category'] or 'general'}] {p['text']}"
            # Preferences the user re-taught carry more weight; mark them so
            # the model treats them as higher priority (P4). Ordering already
            # puts these first; the tag makes the salience explicit.
            conf = p.get("confidence", 1) or 1
            if conf > 1:
                line += f"  (reinforced x{conf})"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        print(f"[tiramisu] steering: could not load preferences: {e}", file=sys.stderr)
        return ""


def _find_repo_root(start: Path | str) -> Path:
    """Walk up from `start` looking for a .git directory or .tiramisu directory.
    Returns the directory containing whichever we hit first, or `start` itself
    if neither is found within 10 levels."""
    cur = start.resolve()
    for _ in range(10):
        if (cur / ".tiramisu").is_dir() or (cur / ".git").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


def _load_repo_overrides(cwd: Path | str) -> str:
    """
    Look for a .tiramisu/ directory in the current repo and load its files
    as the LAST steering layer (highest priority).

    Recognized files inside <repo>/.tiramisu/:
      - style.md          general style rules specific to this project
      - preferences.md    project-specific preferences
      - context.md        free-form project context (architecture, glossary, etc.)
      - any other .md     loaded with its filename as the heading

    Empty / missing dir returns "".
    """
    repo = _find_repo_root(cwd)
    override_dir = repo / ".tiramisu"
    if not override_dir.is_dir():
        return ""

    # Stable order so the prompt cache stays hot across calls
    md_files = sorted(override_dir.glob("*.md"))
    if not md_files:
        return ""

    chunks = []
    for f in md_files:
        text = _read(f)
        if not text.strip():
            continue
        # Strip a leading H1 from the file (we add our own heading)
        body = text.strip()
        heading = f.stem.replace("_", " ").replace("-", " ").title()
        chunks.append(f"## {heading}  ({f.name})\n\n{body}")

    if not chunks:
        return ""

    return f"# PROJECT-SPECIFIC OVERRIDES (from {override_dir})\n\n" + "\n\n".join(chunks)


def load_steering(
    agent: str,
    languages: list[str] | None = None,
    include_engineering: bool = True,
    include_communication: bool = False,
    include_universal_style: bool = True,
    include_learned: bool = True,
    include_preferences: bool = True,
    include_repo_overrides: bool = True,
    cwd: str | Path | None = None,
) -> str:
    """
    Build a composed system prompt for an agent.

    Args:
      agent: name of the agent file in agents/ (without .md)
      languages: list of languages to filter code-style.md by, e.g. ["Python"]
                 if None, no language-specific style is loaded
      include_engineering: include engineering-principles.md
      include_communication: include communication-style.md
      include_universal_style: include the "Universal Preferences" section of code-style.md
      include_learned: include steering/learned/*.md (docs adopted from research)
      include_preferences: append learned preferences from learnings.db
      include_repo_overrides: append per-repo .tiramisu/*.md files (highest priority)
      cwd: directory to search for .tiramisu/ overrides (defaults to process cwd)

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
        eng = _read(ROOT / "steering" / "engineering-principles.md")
        if eng:
            parts.append("\n# ENGINEERING PRINCIPLES\n\n" + eng.strip())

    # 3. Code style (filtered by language)
    style = _code_style_for(languages, include_universal=include_universal_style)
    if style:
        scope = f" ({', '.join(languages)})" if languages else ""
        parts.append(f"\n# CODE STYLE{scope}\n\n" + style.strip())

    # 4. Communication style
    if include_communication:
        comm = _read(ROOT / "steering" / "communication-style.md")
        if comm:
            parts.append("\n# COMMUNICATION STYLE\n\n" + comm.strip())

    # 5. Learned docs (adopted from research via `t research apply`).
    #    Before preferences and repo overrides so those more specific
    #    layers still win on conflict.
    if include_learned:
        learned = _load_learned()
        if learned:
            parts.append("\n# LEARNED FROM RESEARCH (user-approved)\n\n" + learned)

    # 6. Active preferences (from learnings.db)
    if include_preferences:
        prefs = _load_preferences()
        if prefs:
            parts.append(
                "\n# USER PREFERENCES (learned over time -- respect these)\n\n" + prefs
            )

    # 7. Per-repo overrides (highest priority -- last in the prompt so they
    #    override anything that came before)
    if include_repo_overrides:
        from pathlib import Path as _Path
        override_cwd = _Path(cwd) if cwd else _Path.cwd()
        overrides = _load_repo_overrides(override_cwd)
        if overrides:
            parts.append("\n" + overrides)

    return "\n\n".join(parts)
