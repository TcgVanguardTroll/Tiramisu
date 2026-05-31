#!/usr/bin/env python3
"""
Eclair post-commit hook.
After every commit, read the final commit message and pair it with the draft
we logged in prepare-commit-msg. This lets us measure how close Eclair's
drafts are to what you actually committed -- the core signal for learning.
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

from gitutil import run_git
import memory


def main():
    # gitutil.run_git() handles git-not-found by raising RuntimeError with a
    # clear message; we still don't want to block the commit hook on it.
    try:
        result = run_git("log", "-1", "--pretty=%B", check=False)
    except (RuntimeError, OSError) as e:
        print(f"[eclair] post-commit: could not invoke git ({e})", file=sys.stderr)
        sys.exit(0)

    final = (result.stdout or "").strip()
    if not final:
        # Empty commit message is unusual (detached HEAD, broken repo). Log and
        # exit cleanly -- a learning hook should never block git.
        print("[eclair] post-commit: empty commit message; nothing to learn from",
              file=sys.stderr)
        sys.exit(0)

    memory.update_commit_final(Path.cwd(), final)
    # Silent on success -- no need to spam the terminal after every commit


if __name__ == "__main__":
    main()
