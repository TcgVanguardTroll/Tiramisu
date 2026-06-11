#!/usr/bin/env python3
"""
Cannoli -- autonomous research.

Periodically fetches external sources (Anthropic API release notes,
Anthropic Cookbook, aider docs, Python release notes) and produces a markdown
findings file with proposed updates to the steering docs.

Architecture:
  - Background-kicked when the user runs `tiramisu` and last run is stale (>7d).
  - Writes findings to ~/.tiramisu/.research/findings_YYYY-MM-DD.md
  - The dispatcher surfaces a one-line notice on the next interactive session.
  - Findings are PROPOSED EDITS ONLY -- never auto-applied. The user copies the
    suggested patches into the steering files by hand (or ignores them).
  - This is the autonomous version of the "learn before mutate" rule.

CLI:
  t research          show the most recent unread findings, mark as read
  t research run      force a research run now (foreground)
  t research mute     mark all pending findings as read without showing
  t research list     list all findings files chronologically
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL

# Config + HTTP plumbing live in research_common (shared with the discovery
# and library modules below). The other Cannoli surfaces are imported here
# so `t research <action>` stays a single CLI entry point.
from research_common import (
    TIRAMISU_HOME, RESEARCH_DIR, CACHE_DIR, READ_MARKER,
    STALE_DAYS, MAX_SRC_CHARS, _fetch,
)
from research_discovery import discover, grab_paper, grab_all_from_latest
from research_library import (
    ingest_library, ingest_path_cli, library_list,
    scout_library, show_latest_scout,
)


# -------- config --------

# Default sources -- shipped as a fallback so first-run always has something
# to scan. Users can override via ~/.tiramisu/sources.json or extend per-repo
# via <repo>/.tiramisu/sources.json. See load_sources() below.
DEFAULT_SOURCES = [
    {
        "name":  "Anthropic API release notes",
        "url":   "https://docs.anthropic.com/en/release-notes/api",
        "focus": "New models, deprecated model names, pricing changes, new SDK "
                 "features (tool use, extended thinking, prompt caching, batches).",
    },
    {
        "name":  "Anthropic Cookbook",
        "url":   "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md",
        "focus": "New patterns for tool use, agent loops, prompt caching, "
                 "and streaming. Skip examples already in use.",
    },
    {
        "name":  "aider docs",
        "url":   "https://aider.chat/docs/",
        "focus": "Features Tiramisu doesn't have. Worth borrowing or explicitly "
                 "deciding not to borrow. Coding-style conventions.",
    },
    {
        "name":  "Python release notes",
        "url":   "https://docs.python.org/3/whatsnew/3.14.html",
        "focus": "Deprecations, new idioms relevant to code-style.md (pathlib, "
                 "typing, async, error handling).",
    },
]


def user_sources_path() -> Path:
    return TIRAMISU_HOME / "sources.json"


def repo_sources_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".tiramisu" / "sources.json"


def _load_json_list(path: Path) -> list[dict] | None:
    """Read a JSON list of source dicts from a file. Returns None on any error
    (file missing, malformed JSON, wrong shape) so the caller can fall back."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cannoli] couldn't parse {path}: {e}", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(f"[cannoli] {path} must be a JSON list; ignoring.", file=sys.stderr)
        return None
    valid = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "name" not in item or "url" not in item:
            continue
        item.setdefault("focus", "general updates")
        valid.append(item)
    return valid


def load_sources() -> list[dict]:
    """
    Return the active source list, layered:
      1. If ~/.tiramisu/sources.json exists -> REPLACES defaults entirely.
         Otherwise the hardcoded DEFAULT_SOURCES is the base.
      2. Plus any sources in <cwd>/.tiramisu/sources.json -- additive.

    Same precedence model as steering.py's load_steering().
    """
    base = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)
    repo_extra = _load_json_list(repo_sources_path())
    if repo_extra:
        base = base + repo_extra
    return base


# -------- helpers --------

def _last_run_file() -> Path:
    return RESEARCH_DIR / "last_run"


def is_stale() -> bool:
    """True if the last research run was more than STALE_DAYS ago."""
    lr = _last_run_file()
    if not lr.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(lr.stat().st_mtime)
    return age > timedelta(days=STALE_DAYS)


def _is_research_output(path: Path) -> bool:
    """Either findings_*.md (delta on watched source) or candidates_*.md
    (new source proposals from discovery layer)."""
    name = path.name
    return name.startswith("findings_") or name.startswith("candidates_")


def _all_research_files() -> list[Path]:
    if not RESEARCH_DIR.exists():
        return []
    return [f for f in RESEARCH_DIR.glob("*.md") if _is_research_output(f)]


def pending_count() -> int:
    """Count of unread findings + candidates files."""
    return sum(
        1 for f in _all_research_files()
        if not (f.with_suffix(f.suffix + READ_MARKER)).exists()
    )


def latest_pending() -> Path | None:
    """The newest unread research output (findings OR candidates)."""
    unread = [
        f for f in _all_research_files()
        if not (f.with_suffix(f.suffix + READ_MARKER)).exists()
    ]
    unread.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return unread[0] if unread else None


def mark_read(findings_file: Path) -> None:
    """Touch a .read sibling so this file no longer counts as pending."""
    marker = findings_file.with_suffix(findings_file.suffix + READ_MARKER)
    marker.touch(exist_ok=True)


def kick_off_background_if_stale() -> None:
    """
    Called by dispatch.py at start of every `tiramisu` invocation. If research
    is stale, spawn the run in a detached background process and return
    immediately. Best-effort -- never raises.
    """
    if not is_stale():
        return
    try:
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        # Touch the marker BEFORE spawning so a second invocation in the same
        # minute doesn't trigger a second background run.
        _last_run_file().touch()

        creationflags = 0
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            creationflags = 0x00000008 | 0x00000200

        # "all" runs both: deltas on watched sources + discovery scouting
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "research.py"), "all", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception:
        # Never block the user's main command on a research-spawn failure
        pass


def print_pending_notice(console=None) -> None:
    """Surface a one-liner if there are unread findings."""
    n = pending_count()
    if n == 0:
        return
    msg = (f"🐶 Cannoli has {n} new finding(s). "
           f"Run `t research` to see them, or `t research mute` to skip.")
    if console is not None:
        console.print(f"\n[dim]{msg}[/dim]\n")
    else:
        print(f"\n{msg}\n")


# -------- the actual research --------

def _cache_path(source_name: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in source_name)
    return CACHE_DIR / f"{safe}.txt"


def _delta_vs_cached(source_name: str, fresh: str) -> str | None:
    """
    Compare fresh content to the cached version. Returns:
      - None if no cache existed (treat all as new)
      - "" if no change since last run (skip)
      - A small diff-ish snippet otherwise (the new/changed lines)
    """
    cache = _cache_path(source_name)
    if not cache.exists():
        return None
    try:
        old = cache.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if old.strip() == fresh.strip():
        return ""
    # Naive delta: lines in fresh that aren't in old. Good enough for changelogs.
    old_lines = set(old.splitlines())
    new_lines = [ln for ln in fresh.splitlines() if ln.strip() and ln not in old_lines]
    if not new_lines:
        return ""
    return "\n".join(new_lines[:200])


def _save_cache(source_name: str, content: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(source_name).write_text(content, encoding="utf-8")


SUMMARY_PROMPT = """\
You are Cannoli, the Tiramisu researcher. Your job is to scan an external
source for changes relevant to Tiramisu and produce ONE concise section
with proposed updates to its steering docs.

SOURCE: {name}
SOURCE URL: {url}
WHAT TO LOOK FOR: {focus}

Tiramisu context (so you know what's already covered):
  - Anthropic SDK, Python 3.10+
  - DEFAULT_MODEL = claude-sonnet-4-6, FAST_MODEL = claude-haiku-4-5
  - Prompt caching, tool use, streaming already in use
  - Steering docs: engineering-principles.md, code-style.md, communication-style.md
  - Per-language code style for Python/Java/Rust/TS
  - Learn-before-mutate rule: agents must never auto-edit their own prompts

CONTENT (raw, possibly HTML):
{content}

Output format (markdown):

## {name}
**Relevance:** <1-5, where 5 = ship this change today, 1 = ignore>

**Summary (2-3 sentences):** what's new and why it matters to Tiramisu.

**Proposed update:** the exact file + section to edit, with the proposed
text in a fenced block. If no actionable update, write "No action recommended."

Rules:
- ONLY propose updates that meaningfully improve Tiramisu. Ignore tangential changes.
- ALWAYS cite the specific bit of the source you're acting on.
- If relevance is 1 or 2, say so honestly and recommend no action.
- Be concise. The user reads this on a Monday morning.
"""


def run_research(quiet: bool = False) -> Path:
    """
    Fetch each source, summarize via FAST_MODEL, write a findings file.
    Returns the findings file path.
    """
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    sources = load_sources()
    log(f"\n🐶 Cannoli is scanning {len(sources)} external source(s)...\n")

    findings_sections: list[str] = []

    for src in sources:
        name, url, focus = src["name"], src["url"], src["focus"]
        log(f"  ↳ fetching: {name}")

        fresh = _fetch(url)
        if fresh.startswith("[error fetching"):
            findings_sections.append(
                f"## {name}\n**Relevance:** n/a\n\n{fresh}\n"
            )
            continue

        delta = _delta_vs_cached(name, fresh)
        _save_cache(name, fresh)

        if delta == "":
            # No change since last run
            log(f"     (no delta -- skipping)")
            continue

        # Summarize via the LLM. Pass either the delta (if we have one) or the
        # full fresh content (first time we see this source).
        content_for_prompt = delta if delta is not None else fresh

        try:
            section = invoke(
                prompt=SUMMARY_PROMPT.format(
                    name=name, url=url, focus=focus,
                    content=content_for_prompt[:MAX_SRC_CHARS],
                ),
                model=FAST_MODEL,
                max_tokens=600,
                temperature=0.2,
            ).strip()
            findings_sections.append(section)
            log(f"     done")
        except Exception as e:
            findings_sections.append(
                f"## {name}\n**Relevance:** n/a\n\n[summarization failed: {e}]\n"
            )

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"findings_{today}.md"
    body = (
        f"# Cannoli findings -- {today}\n\n"
        "Proposed updates from external sources. **None of these are applied "
        "automatically.** Review, paste what's worth keeping into the steering "
        "files, ignore the rest.\n\n"
        + "\n\n".join(findings_sections)
        + "\n"
    )
    out_path.write_text(body, encoding="utf-8")
    _last_run_file().touch()

    log(f"\n✓ Findings written to: {out_path}")
    log(f"  ({pending_count()} unread now)\n")
    return out_path


# -------- show / mute --------

def show_latest() -> None:
    f = latest_pending()
    if f is None:
        print("\n🐶 Cannoli has no unread findings.")
        if is_stale():
            print("  Run `t research run` to scan sources now.\n")
        else:
            most_recent = sorted(
                RESEARCH_DIR.glob("findings_*.md") if RESEARCH_DIR.exists() else [],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if most_recent:
                print(f"  Last run: {most_recent[0].name}\n")
            else:
                print()
        return

    text = f.read_text(encoding="utf-8")

    # Use rich for nice rendering if available; fall back to plain print.
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console().print(Markdown(text))
    except Exception:
        print(text)

    mark_read(f)
    print(f"\n[marked as read: {f.name}]\n")


def mute_all_pending() -> None:
    n = 0
    for f in _all_research_files():
        marker = f.with_suffix(f.suffix + READ_MARKER)
        if not marker.exists():
            marker.touch()
            n += 1
    print(f"Marked {n} pending finding(s) / candidate(s) as read.")


def list_all() -> None:
    files = sorted(_all_research_files(), key=lambda p: p.stat().st_mtime)
    if not files:
        print("No research history yet. Run `t research run` (or `t research discover`) to start.")
        return
    print("\nResearch history:\n")
    for f in files:
        marker = f.with_suffix(f.suffix + READ_MARKER)
        status = "read" if marker.exists() else "UNREAD"
        kind   = "candidates" if f.name.startswith("candidates_") else "findings  "
        ts     = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  [{status:6}]  {kind}  {f.name}   ({ts})")
    print()


# -------- sources management --------

def _write_user_sources(sources: list[dict]) -> None:
    """Save the user's sources.json. Creates parent dir if needed."""
    path = user_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, indent=2), encoding="utf-8")


def sources_list() -> None:
    """Print the active sources for this run, with their origin."""
    user_path = user_sources_path()
    repo_path = repo_sources_path()

    if user_path.exists():
        origin = f"user file ({user_path})"
    else:
        origin = "hardcoded defaults (no user file yet)"

    base_sources = _load_json_list(user_path) or list(DEFAULT_SOURCES)
    repo_extra   = _load_json_list(repo_path) or []

    print(f"\nActive sources ({len(base_sources) + len(repo_extra)} total)\n")

    if base_sources:
        print(f"From {origin}:")
        for i, s in enumerate(base_sources, 1):
            print(f"  {i}. {s['name']}")
            print(f"     url:   {s['url']}")
            focus = s.get("focus", "")
            if focus:
                wrapped = (focus[:140] + "...") if len(focus) > 140 else focus
                print(f"     focus: {wrapped}")
        print()

    if repo_extra:
        print(f"From repo file ({repo_path}):")
        for i, s in enumerate(repo_extra, 1):
            print(f"  +{i}. {s['name']}  ({s['url']})")
        print()

    if not user_path.exists():
        print("To customize: `t research sources reset` writes the defaults to "
              "your user file so you can edit them.\n")


def sources_add(url: str, name: str | None, focus: str | None) -> None:
    """Append a source to the user's sources.json. Creates from defaults if missing."""
    if not url:
        print("Usage: t research sources add <url> [name] [focus]")
        sys.exit(1)

    name = name or url
    focus = focus or "general updates worth watching"

    current = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)

    # Don't add duplicates by URL
    if any(s["url"] == url for s in current):
        print(f"  source with url={url!r} already present; not added.")
        return

    current.append({"name": name, "url": url, "focus": focus})
    _write_user_sources(current)
    print(f"\n  added: {name}")
    print(f"          {url}")
    print(f"  total: {len(current)} source(s) in {user_sources_path()}\n")


def sources_remove(name_or_url: str) -> None:
    """Remove a source by name or URL match."""
    if not name_or_url:
        print("Usage: t research sources remove <name-or-url>")
        sys.exit(1)

    current = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)
    needle = name_or_url.lower()

    remaining = [
        s for s in current
        if needle not in s["name"].lower() and needle not in s["url"].lower()
    ]

    if len(remaining) == len(current):
        print(f"  no source matched {name_or_url!r}; nothing removed.")
        return

    _write_user_sources(remaining)
    print(f"\n  removed {len(current) - len(remaining)} source(s) "
          f"matching {name_or_url!r}.")
    print(f"  total now: {len(remaining)} source(s) in {user_sources_path()}\n")


def sources_reset() -> None:
    """Write DEFAULT_SOURCES to the user file so they can edit from a clean baseline."""
    _write_user_sources(list(DEFAULT_SOURCES))
    print(f"\n  wrote {len(DEFAULT_SOURCES)} default source(s) to "
          f"{user_sources_path()}.")
    print("  Edit that file freely; or run `t research sources add <url>` to extend.\n")


def sources_show() -> None:
    """Dump the raw user sources.json (for grep / copy)."""
    path = user_sources_path()
    if path.exists():
        print(path.read_text(encoding="utf-8"))
    else:
        print("# No user sources.json yet -- using hardcoded defaults.")
        print(json.dumps(DEFAULT_SOURCES, indent=2))


# -------- CLI --------

def _sources_subcmd(rest: list[str]) -> None:
    """Dispatch `t research sources ...` subcommands."""
    if not rest or rest[0] == "list":
        sources_list()
    elif rest[0] == "add":
        url   = rest[1] if len(rest) > 1 else None
        name  = rest[2] if len(rest) > 2 else None
        focus = rest[3] if len(rest) > 3 else None
        sources_add(url, name, focus)
    elif rest[0] == "remove":
        sources_remove(rest[1] if len(rest) > 1 else None)
    elif rest[0] == "reset":
        sources_reset()
    elif rest[0] == "show":
        sources_show()
    else:
        print(f"Unknown sources action: {rest[0]!r}")
        print("Valid: list | add <url> [name] [focus] | remove <name-or-url> | reset | show")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Cannoli -- autonomous research")
    parser.add_argument("action", nargs="?", default="show")
    parser.add_argument("rest", nargs=argparse.REMAINDER,
                        help="Trailing args for subcommands (e.g. sources add <url>)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output (for background runs)")
    args = parser.parse_args()

    if args.action == "show":
        show_latest()
    elif args.action == "run":
        run_research(quiet=args.quiet)
    elif args.action == "discover":
        discover(quiet=args.quiet)
    elif args.action == "ingest":
        if not args.rest:
            print("Usage: t research ingest <file-or-directory>")
            sys.exit(1)
        ingest_path_cli(args.rest[0], quiet=args.quiet)
    elif args.action == "grab":
        if not args.rest:
            print("Usage:")
            print("  t research grab <arxiv-id>    -- pull one paper")
            print("  t research grab --all         -- pull every paper from")
            print("                                   the latest candidates file")
            sys.exit(1)
        target = args.rest[0]
        if target in ("--all", "-a", "all"):
            grab_all_from_latest()
        else:
            grab_paper(target)
    elif args.action == "library":
        library_list()
    elif args.action == "scout":
        if not args.rest:
            print("Usage: t research scout <path-to-library>")
            print("  Scans the directory for PDFs, ranks each by relevance.")
            print("  Cheap (filename-only scoring). ~$0.01 per 50 books.")
            sys.exit(1)
        scout_library(Path(args.rest[0]).expanduser(), quiet=args.quiet)
    elif args.action in ("show-scout", "scout-show"):
        show_latest_scout()
    elif args.action == "all":
        run_research(quiet=args.quiet)
        discover(quiet=args.quiet)
        ingest_library(quiet=args.quiet)
    elif args.action == "mute":
        mute_all_pending()
    elif args.action == "list":
        list_all()
    elif args.action == "sources":
        _sources_subcmd(args.rest)
    else:
        print(f"Unknown action: {args.action!r}")
        print("Valid: show | run | discover | grab <id|--all> | "
              "ingest <path> | scout <path> | show-scout | library | "
              "all | mute | list | sources [...]")
        sys.exit(1)


if __name__ == "__main__":
    main()
