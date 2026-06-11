#!/usr/bin/env python3
"""
Cannoli -- apply research findings to the steering docs, one y/n at a time.

`t research apply` walks the latest findings file and, for each proposed
update, shows it and asks for confirmation. This is the gated half of the
"learn before mutate" rule (CLAUDE.md §4.3): Cannoli PROPOSES, the user
APPLIES -- here the application is a keystroke instead of a copy-paste,
but a human still approves every edit individually.

Hard safety rules (pinned by tests/test_research_apply.py):
  - Edits may only touch the three shared steering files
    (engineering-principles.md, code-style.md, communication-style.md).
  - New docs may only be created inside steering/learned/ -- that
    directory is composed into every agent prompt by steering.py.
  - Persona files (agents/*.md) are NEVER touched: persona edits stay
    hand-applied (CLAUDE.md §4.1).
  - Confirmation defaults to NO; empty input, EOF, and Ctrl+C all mean no.
  - An .applied sidecar records what was accepted so re-runs skip it.

CLI:
  t research apply           walk the latest findings file
  t research apply <path>    walk a specific findings file
"""
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from research_common import RESEARCH_DIR, READ_MARKER

# The only files an accepted edit may modify. Names, not paths -- anything
# else a findings file mentions is either a new-doc proposal (routed to
# steering/learned/) or skipped.
EDITABLE = (
    "engineering-principles.md",
    "code-style.md",
    "communication-style.md",
)

# H2 sections of code-style.md that steering._code_style_for() composes.
# Accepted code-style edits must land INSIDE one of these or they would be
# invisible to every agent.
LANG_SECTIONS = ("Python", "Java", "Rust", "TypeScript")
UNIVERSAL_SECTION = "Universal Preferences"

APPLIED_MARKER = ".applied"

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_MD_MENTION_RE = re.compile(r"[\w./\\-]*[\w-]+\.md")
_NO_ACTION_RE = re.compile(r"no action recommended", re.IGNORECASE)


@dataclass
class Insight:
    title: str
    body: str
    action: str            # "edit" | "create" | "skip"
    target: str | None = None   # basename to edit, or doc name to create
    proposed: str | None = None
    reason: str = field(default="")


# -------- parsing --------

def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "finding"


def _classify(title: str, body: str) -> Insight:
    """Decide what one findings section is asking for."""
    fence = _FENCE_RE.search(body)
    if _NO_ACTION_RE.search(body) or fence is None:
        return Insight(title, body, "skip", reason="no actionable proposal")

    proposed = fence.group(1).strip()
    prose = body[: fence.start()]

    mentions = _MD_MENTION_RE.findall(prose)
    for m in mentions:
        norm = m.replace("\\", "/")
        if norm.startswith("agents/") or f"agents/{norm}" in prose.replace("\\", "/"):
            return Insight(title, body, "skip",
                           reason="targets a persona file -- personas are "
                                  "hand-edited (CLAUDE.md §4.1)")
        base = Path(norm).name
        if base in EDITABLE:
            return Insight(title, body, "edit", target=base, proposed=proposed)

    # Explicit new-doc proposal: learned/<name>.md
    for m in mentions:
        norm = m.replace("\\", "/")
        if "learned/" in norm:
            return Insight(title, body, "create",
                           target=Path(norm).name, proposed=proposed)

    # No recognizable target: offer it as a new learned doc named after
    # the insight itself.
    return Insight(title, body, "create",
                   target=_slugify(title) + ".md", proposed=proposed)


def parse_insights(text: str) -> list[Insight]:
    """Split a findings file on H2 headings and classify each section."""
    insights: list[Insight] = []
    current_title: str | None = None
    current_lines: list[str] = []

    def flush():
        if current_title is not None:
            insights.append(_classify(current_title, "\n".join(current_lines)))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)
    flush()
    return insights


# -------- target resolution (the sandbox) --------

def resolve_edit_target(name: str, root: Path) -> Path | None:
    """Map a proposed edit target to a real path. Allowlist of bare names:
    the name must BE one of the three steering files (an optional steering/
    prefix is tolerated). Anything path-like — ../ escapes, absolute paths,
    agents/ — is rejected, not normalized."""
    norm = name.replace("\\", "/").strip()
    if norm.startswith("steering/"):
        norm = norm[len("steering/"):]
    if norm not in EDITABLE:
        return None
    base = norm
    target = (root / "steering" / base).resolve()
    if (root / "steering").resolve() not in target.parents:
        return None
    return target


def learned_doc_path(name: str, root: Path) -> Path:
    """A safe, non-clobbering path inside steering/learned/ for a new doc.
    The proposed name is reduced to a slug, so separators and ../ cannot
    survive into the final path."""
    stem = _slugify(Path(name.replace("\\", "/")).stem)
    learned = root / "steering" / "learned"
    candidate = learned / f"{stem}.md"
    n = 2
    while candidate.exists():
        candidate = learned / f"{stem}-{n}.md"
        n += 1
    assert candidate.resolve().parent == learned.resolve()
    return candidate


# -------- applying --------

def insert_into_section(text: str, section: str, addition: str) -> str:
    """Insert `addition` at the end of the named H2 section. If the section
    doesn't exist, append it. Keeps code-style.md composable: content outside
    a recognized H2 would never reach an agent prompt."""
    lines = text.splitlines()
    heading = f"## {section}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == heading), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n{heading}\n\n{addition}\n"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    # Trim trailing blanks inside the section, then add the new lines
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    new_lines = lines[:end] + ["", addition] + lines[end:]
    return "\n".join(new_lines).rstrip("\n") + "\n"


def _pick_code_style_section(insight: Insight) -> str:
    haystack = f"{insight.title}\n{insight.body}"
    for lang in LANG_SECTIONS:
        if re.search(rf"\b{lang}\b", haystack, re.IGNORECASE):
            return lang
    return UNIVERSAL_SECTION


def apply_edit(insight: Insight, root: Path, date: str | None = None) -> Path:
    """Apply an accepted edit to its steering file. code-style.md edits go
    inside the matching composed section; the whole-file docs get a dated
    heading appended (git history carries the provenance)."""
    target = resolve_edit_target(insight.target, root)
    if target is None:
        raise ValueError(f"refusing to edit {insight.target!r}: not an allowed steering file")
    date = date or datetime.now().strftime("%Y-%m-%d")
    text = target.read_text(encoding="utf-8")

    if target.name == "code-style.md":
        section = _pick_code_style_section(insight)
        new_text = insert_into_section(text, section, insight.proposed)
    else:
        new_text = (
            text.rstrip("\n")
            + f"\n\n## Adopted from research ({date}): {insight.title}\n\n"
            + insight.proposed + "\n"
        )
    target.write_text(new_text, encoding="utf-8")
    return target


def apply_create(insight: Insight, root: Path, source_name: str,
                 date: str | None = None) -> Path:
    """Create a new steering doc under steering/learned/."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    path = learned_doc_path(insight.target or insight.title, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    title = insight.title if insight.title else path.stem.replace("-", " ").title()
    path.write_text(
        f"# {title}\n\n"
        f"> Adopted from Cannoli research ({source_name}, applied {date}).\n\n"
        f"{insight.proposed}\n",
        encoding="utf-8",
    )
    return path


# -------- confirmation + registry --------

def _confirm(prompt: str) -> bool:
    """Default NO. EOF and Ctrl+C are NO. Only an explicit y/yes applies."""
    try:
        ans = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def _applied_path(findings: Path) -> Path:
    return findings.with_suffix(findings.suffix + APPLIED_MARKER)


def load_applied(findings: Path) -> set[str]:
    path = _applied_path(findings)
    if not path.exists():
        return set()
    try:
        return {ln.strip() for ln in
                path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def record_applied(findings: Path, title: str) -> None:
    path = _applied_path(findings)
    with path.open("a", encoding="utf-8") as f:
        f.write(title + "\n")


# -------- CLI --------

def _latest_findings() -> Path | None:
    if not RESEARCH_DIR.exists():
        return None
    files = sorted(RESEARCH_DIR.glob("findings_*.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _excerpt(body: str, limit: int = 400) -> str:
    text = body.strip()
    return text if len(text) <= limit else text[:limit] + " ..."


def apply_cli(rest: list[str]) -> None:
    findings = Path(rest[0]).expanduser() if rest else _latest_findings()
    if findings is None or not findings.exists():
        print("\n🐶 No findings file to apply. Run `t research run` first.\n")
        return

    insights = parse_insights(findings.read_text(encoding="utf-8"))
    applied = load_applied(findings)
    actionable = [i for i in insights if i.action != "skip"]

    print(f"\n🐶 Cannoli — applying findings from {findings.name}")
    print(f"   {len(actionable)} actionable proposal(s), "
          f"{len(applied)} already applied.\n")

    n_applied = 0
    for insight in insights:
        if insight.title in applied:
            continue
        if insight.action == "skip":
            if insight.reason and "persona" in insight.reason:
                print(f"  · {insight.title}: skipped ({insight.reason})")
            continue

        print(f"\n--- {insight.title} ---")
        print(_excerpt(insight.body))
        if insight.action == "edit":
            ok = _confirm(f"\nApply to steering/{insight.target}?")
            if ok:
                path = apply_edit(insight, ROOT)
                record_applied(findings, insight.title)
                n_applied += 1
                print(f"  ✓ applied to {path.relative_to(ROOT)}")
        else:  # create
            ok = _confirm(f"\nCreate new steering doc steering/learned/{insight.target}?")
            if ok:
                path = apply_create(insight, ROOT, source_name=findings.name)
                record_applied(findings, insight.title)
                n_applied += 1
                print(f"  ✓ created {path.relative_to(ROOT)}")

    marker = findings.with_suffix(findings.suffix + READ_MARKER)
    marker.touch(exist_ok=True)
    print(f"\n✓ Done. {n_applied} change(s) applied this run.")
    if n_applied:
        print("  Review with `git diff` in the Tiramisu repo — every accepted "
              "edit is a normal file change you can amend or revert.\n")
    else:
        print()


if __name__ == "__main__":
    apply_cli(sys.argv[1:])
