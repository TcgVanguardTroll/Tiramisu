#!/usr/bin/env python3
"""
Cookie pre-commit review hook.
Runs Cookie (tortoiseshell cat code reviewer) on your staged diff before every commit.
Passes the full content of changed files so Cookie has real context.
Injects your learned preferences and recent override patterns so she stays calibrated.
Blocks the commit on BLOCKERs unless you override.

Install:
    t hook
"""
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream_markdown, FAST_MODEL
from steering import load_steering, detect_languages
import memory

MAX_DIFF_CHARS     = 6000
MAX_PER_FILE_CHARS = 4000
MAX_FILES          = 6


def get_staged_diff():
    stat = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        capture_output=True, text=True
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True
    ).stdout.strip()
    return stat, diff


def get_changed_files():
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True
    )
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def build_file_context(files):
    parts = []
    for name in files[:MAX_FILES]:
        p = Path(name)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > MAX_PER_FILE_CHARS:
            content = content[:MAX_PER_FILE_CHARS] + f"\n... [truncated -- {len(content)} chars total]"
        ext = p.suffix.lstrip(".")
        parts.append(f"### {name}\n```{ext}\n{content}\n```")
    return "\n\n".join(parts)


def build_override_context():
    """Cookie-specific calibration: things you flagged that got overridden."""
    overrides = memory.get_recent_overrides(n=10)
    if not overrides:
        return ""
    snippets = "\n".join(f"- {o['snippet'][:200]}" for o in overrides if o["snippet"])
    if not snippets:
        return ""
    return (
        "# RECENT OVERRIDES (you flagged these, the user dismissed them)\n\n"
        f"{snippets}\n\n"
        "Be more cautious about raising these again -- only flag if the issue is unambiguous "
        "or has a different shape than before."
    )


# Match a BLOCKER tag, not the word "blocker" in prose.
# Catches: [BLOCKER], BLOCKER:, **BLOCKER**, BLOCKER - ...
# Ignores: "this is not a blocker", "no blockers found"
BLOCKER_TAG = re.compile(
    r"(?:\[BLOCKER\]|\*\*BLOCKER\*\*|^BLOCKER[:\s\-–—])",
    re.MULTILINE,
)


def has_blockers(review):
    if not review:
        return False
    return bool(BLOCKER_TAG.search(review))


def extract_blocker_snippet(review):
    """Pull the first line containing a BLOCKER tag for logging."""
    for line in (review or "").splitlines():
        if BLOCKER_TAG.search(line):
            return line.strip()[:500]
    return None


def main():
    stat, diff = get_staged_diff()

    if not diff:
        sys.exit(0)

    files = get_changed_files()
    languages = detect_languages(files)

    system = load_steering(
        agent="cookie",
        languages=languages,
        include_engineering=True,
        include_universal_style=True,
        include_preferences=True,
    )

    overrides = build_override_context()
    if overrides:
        system = system + "\n\n" + overrides

    if not system.strip():
        print("[cookie] Steering empty -- skipping review")
        sys.exit(0)

    lang_label = ("/".join(languages)) if languages else "no language detected"
    print(f"\nCookie is reviewing your changes ({lang_label})...\n")
    print(f"  {stat}\n")
    print("-" * 60)

    file_context = build_file_context(files)

    prompt = (
        "Review this commit. Flag BLOCKERs and serious issues only -- "
        "bugs, broken callers, security problems, missing null checks. "
        "If nothing is seriously wrong, say LGTM.\n\n"
        f"## Staged diff\n```diff\n{diff[:MAX_DIFF_CHARS]}\n```"
    )
    if file_context:
        prompt += f"\n\n## Full file context\n\n{file_context}"

    review = invoke_stream_markdown(
        prompt=prompt,
        system=system,
        model=FAST_MODEL,
        max_tokens=1024,
    )

    print("-" * 60)

    repo = Path.cwd()
    blockers_present = has_blockers(review)

    if blockers_present:
        print("\nCookie found blockers. Commit anyway? [y/N] ", end="", flush=True)
        try:
            answer = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer != "y":
            memory.log_review(repo, diff, files, review, "blocked_aborted")
            print("Commit aborted. Fix the issues or run: git commit --no-verify\n")
            sys.exit(1)
        else:
            rid = memory.log_review(repo, diff, files, review, "blocked_overridden")
            snippet = extract_blocker_snippet(review)
            if rid and snippet:
                memory.log_override(rid, snippet, files)
            print("Proceeding despite blockers.\n")
    else:
        memory.log_review(repo, diff, files, review, "passed")
        print("\nCookie approves. Committing.\n")


if __name__ == "__main__":
    main()
