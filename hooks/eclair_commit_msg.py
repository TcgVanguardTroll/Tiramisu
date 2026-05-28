#!/usr/bin/env python3
"""
Éclair prepare-commit-msg hook.
Reads the staged diff and drafts a conventional commit message before your editor opens.
You can edit the draft freely — it's just a starting point.

Skips automatically when:
  - The user already supplied a message (git commit -m "...")
  - It's a merge, squash, or amend commit
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

from llm import invoke, FAST_MODEL

PROMPT = """\
Write a git commit message for this staged diff.

Rules:
- First line: conventional commits format — type(scope): subject (under 72 chars)
  Types: feat / fix / refactor / test / docs / chore / style / perf
- Optional: blank line then 1-3 tight bullet points if the change is non-trivial
- No filler words. No "this commit". Be direct and specific about what changed and why.
- Output ONLY the commit message text, nothing else.

```diff
{diff}
```
"""


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    commit_msg_file = Path(sys.argv[1])
    source = sys.argv[2] if len(sys.argv) > 2 else ""

    # Don't clobber a message the user already wrote
    if source in ("message", "merge", "squash", "commit"):
        sys.exit(0)

    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True
    )
    diff = result.stdout.strip()

    if not diff:
        sys.exit(0)

    print("\n✏️  Eclair is drafting your commit message...", flush=True)

    try:
        msg = invoke(
            prompt=PROMPT.format(diff=diff[:6000]),
            model=FAST_MODEL,
            max_tokens=256,
        ).strip()
    except Exception as e:
        print(f"[eclair] Could not draft message: {e}", flush=True)
        sys.exit(0)  # Non-fatal — fall through to blank editor

    commit_msg_file.write_text(msg + "\n", encoding="utf-8")
    first_line = msg.splitlines()[0] if msg else ""
    print(f"   -> {first_line}\n", flush=True)


if __name__ == "__main__":
    main()
