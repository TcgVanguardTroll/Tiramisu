#!/usr/bin/env python3
"""
Cookie pre-commit review hook.
Runs Cookie (tortoiseshell cat code reviewer) on your staged diff before every commit.
Passes the full content of changed files alongside the diff so Cookie has real context.
Blocks the commit if BLOCKERs are found and you don't override.

Install into a repo:
    t hook
"""
import subprocess
import sys
import os
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream, FAST_MODEL

MAX_DIFF_CHARS     = 6000
MAX_PER_FILE_CHARS = 4000
MAX_FILES          = 6


def get_staged_diff() -> tuple[str, str]:
    stat = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        capture_output=True, text=True
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True
    ).stdout.strip()
    return stat, diff


def get_changed_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True
    )
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def build_file_context(files: list[str]) -> str:
    """Read the full content of each changed file so Cookie sees surrounding code."""
    parts = []
    for name in files[:MAX_FILES]:
        p = Path(name)
        if not p.exists():
            continue  # deleted file — diff alone is enough
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > MAX_PER_FILE_CHARS:
            content = content[:MAX_PER_FILE_CHARS] + f"\n... [truncated — {len(content)} chars total]"
        ext = p.suffix.lstrip(".")
        parts.append(f"### {name}\n```{ext}\n{content}\n```")
    return "\n\n".join(parts)


def main():
    stat, diff = get_staged_diff()

    if not diff:
        sys.exit(0)

    agent_file = ROOT / "agents" / "cookie.md"
    if not agent_file.exists():
        print("[cookie] Agent file not found — skipping review")
        sys.exit(0)

    system = agent_file.read_text(encoding="utf-8")

    print(f"\nCookie is reviewing your changes...\n")
    print(f"  {stat}\n")
    print("-" * 60)

    # Build context: diff + full files
    files = get_changed_files()
    file_context = build_file_context(files)

    prompt = (
        "Review this commit. Flag BLOCKERs and serious issues only — "
        "bugs, broken callers, security problems, missing null checks. "
        "If nothing is seriously wrong, say LGTM.\n\n"
        f"## Staged diff\n```diff\n{diff[:MAX_DIFF_CHARS]}\n```"
    )
    if file_context:
        prompt += f"\n\n## Full file context (so you can see what the diff touches)\n\n{file_context}"

    review = invoke_stream(
        prompt=prompt,
        system=system,
        model=FAST_MODEL,
        max_tokens=1024,
    )

    print("-" * 60)

    if "BLOCKER" in review.upper():
        print("\nCookie found blockers. Commit anyway? [y/N] ", end="", flush=True)
        try:
            answer = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer != "y":
            print("Commit aborted. Fix the issues or run: git commit --no-verify\n")
            sys.exit(1)
        else:
            print("Proceeding despite blockers.\n")
    else:
        print("\nCookie approves. Committing.\n")


if __name__ == "__main__":
    main()
