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

import memory


def main():
    try:
        final = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        sys.exit(0)

    if not final:
        sys.exit(0)

    memory.update_commit_final(Path.cwd(), final)
    # Silent on success -- no need to spam the terminal after every commit


if __name__ == "__main__":
    main()
