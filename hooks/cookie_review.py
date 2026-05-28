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
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream, FAST_MODEL
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


def build_learned_context():
    """Inject preferences + recent overrides as calibration."""
    parts = []

    prefs = memory.get_active_preferences("review")
    if prefs:
        bullets = "\n".join(f"- {p['text']}" for p in prefs[:15])
        parts.append(f"## User preferences (respect these)\n{bullets}")

    overrides = memory.get_recent_overrides(n=10)
    if overrides:
        snippets = "\n".join(f"- {o['snippet'][:200]}" for o in overrides if o["snippet"])
        if snippets:
            parts.append(
                "## Things you flagged that the user has overridden recently\n"
                f"{snippets}\n\n"
                "Be more cautious about raising these again -- only flag if the issue is unambiguous."
            )

    return "\n\n".join(parts)


def extract_blocker_snippet(review):
    """Pull the first BLOCKER line from the review for logging."""
    for line in review.splitlines():
        if "BLOCKER" in line.upper():
            return line.strip()[:500]
    return None


def main():
    stat, diff = get_staged_diff()

    if not diff:
        sys.exit(0)

    agent_file = ROOT / "agents" / "cookie.md"
    if not agent_file.exists():
        print("[cookie] Agent file not found -- skipping review")
        sys.exit(0)

    system = agent_file.read_text(encoding="utf-8")
    learned = build_learned_context()
    if learned:
        system = system + "\n\n" + learned

    print("\nCookie is reviewing your changes...\n")
    print(f"  {stat}\n")
    print("-" * 60)

    files = get_changed_files()
    file_context = build_file_context(files)

    prompt = (
        "Review this commit. Flag BLOCKERs and serious issues only -- "
        "bugs, broken callers, security problems, missing null checks. "
        "If nothing is seriously wrong, say LGTM.\n\n"
        f"## Staged diff\n```diff\n{diff[:MAX_DIFF_CHARS]}\n```"
    )
    if file_context:
        prompt += f"\n\n## Full file context\n\n{file_context}"

    review = invoke_stream(
        prompt=prompt,
        system=system,
        model=FAST_MODEL,
        max_tokens=1024,
    )

    print("-" * 60)

    repo = Path.cwd()
    blockers_present = "BLOCKER" in (review or "").upper()

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
