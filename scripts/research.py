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
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL


# -------- config --------

TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
RESEARCH_DIR  = TIRAMISU_HOME / ".research"
CACHE_DIR     = RESEARCH_DIR / "cache"      # last-fetched copies
READ_MARKER   = ".read"                     # suffix appended once user has seen findings
STALE_DAYS    = 7
HTTP_TIMEOUT  = 20                          # seconds per source
MAX_SRC_CHARS = 30000                       # truncate huge pages

# Default sources. Add / remove freely -- this is a sensible starting point.
SOURCES = [
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
        "url":   "https://docs.python.org/3/whatsnew/3.13.html",
        "focus": "Deprecations, new idioms relevant to code-style.md (pathlib, "
                 "typing, async, error handling).",
    },
]


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


def pending_count() -> int:
    """Count of findings files the user has not yet seen."""
    if not RESEARCH_DIR.exists():
        return 0
    return sum(
        1 for f in RESEARCH_DIR.glob("findings_*.md")
        if not (f.with_suffix(f.suffix + READ_MARKER)).exists()
    )


def latest_pending() -> Path | None:
    """The newest unread findings file, if any."""
    candidates = sorted(
        (f for f in RESEARCH_DIR.glob("findings_*.md")
         if not (f.with_suffix(f.suffix + READ_MARKER)).exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


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

        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "research.py"), "run", "--quiet"],
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

def _fetch(url: str) -> str:
    """Download a URL with a user agent, return text (truncated to MAX_SRC_CHARS)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Tiramisu-Cannoli/1.0 (+research)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return f"[error fetching {url}: {e}]"
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_SRC_CHARS:
        text = text[:MAX_SRC_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text


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
  - DEFAULT_MODEL = claude-sonnet-4-5, FAST_MODEL = claude-haiku-4-5
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

    log("\n🐶 Cannoli is scanning external sources...\n")

    findings_sections: list[str] = []

    for src in SOURCES:
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
    if not RESEARCH_DIR.exists():
        print("Nothing to mute.")
        return
    n = 0
    for f in RESEARCH_DIR.glob("findings_*.md"):
        marker = f.with_suffix(f.suffix + READ_MARKER)
        if not marker.exists():
            marker.touch()
            n += 1
    print(f"Marked {n} pending finding(s) as read.")


def list_all() -> None:
    if not RESEARCH_DIR.exists():
        print("No research history yet. Run `t research run` to start.")
        return
    files = sorted(RESEARCH_DIR.glob("findings_*.md"), key=lambda p: p.stat().st_mtime)
    if not files:
        print("No research history yet.")
        return
    print("\nResearch history:\n")
    for f in files:
        marker = f.with_suffix(f.suffix + READ_MARKER)
        status = "read" if marker.exists() else "UNREAD"
        ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  [{status:6}]  {f.name}   ({ts})")
    print()


# -------- CLI --------

def main():
    parser = argparse.ArgumentParser(description="Cannoli -- autonomous research")
    parser.add_argument("action", nargs="?", default="show",
                        choices=["show", "run", "mute", "list"])
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output (for background runs)")
    args = parser.parse_args()

    if args.action == "show":
        show_latest()
    elif args.action == "run":
        run_research(quiet=args.quiet)
    elif args.action == "mute":
        mute_all_pending()
    elif args.action == "list":
        list_all()


if __name__ == "__main__":
    main()
