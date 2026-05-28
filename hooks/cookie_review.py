#!/usr/bin/env python3
"""
Cookie pre-commit review hook.
Runs Cookie (tortoiseshell cat code reviewer) on your staged diff before every commit.
Blocks the commit if BLOCKERs are found and you don't override.

Install into a repo:
    python3 G:/tiramisu-portable/scripts/install_hooks.py <path-to-your-repo>
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


def get_staged_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        capture_output=True, text=True
    )
    stat = result.stdout.strip()

    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True
    )
    return stat, result.stdout.strip()


def main():
    stat, diff = get_staged_diff()

    if not diff:
        sys.exit(0)  # Nothing staged, nothing to review

    agent_file = ROOT / "agents" / "cookie.md"
    if not agent_file.exists():
        print("[cookie] Agent file not found — skipping review")
        sys.exit(0)

    system = agent_file.read_text(encoding="utf-8")

    print(f"\n🐱 Cookie is reviewing your changes...\n")
    print(f"  {stat}\n")
    print("-" * 60)

    review = invoke_stream(
        prompt=(
            f"Review this staged diff. Be concise — flag BLOCKERs and serious issues only. "
            f"If nothing is seriously wrong, say LGTM.\n\n"
            f"```diff\n{diff[:8000]}\n```"
        ),
        system=system,
        model=FAST_MODEL,
        max_tokens=1024,
    )

    print("-" * 60)

    if "BLOCKER" in review.upper():
        print("\n⛔  Cookie found blockers. Commit anyway? [y/N] ", end="", flush=True)
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
        print("\n✓ Cookie approves. Committing.\n")


if __name__ == "__main__":
    main()
