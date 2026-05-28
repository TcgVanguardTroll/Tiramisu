#!/usr/bin/env python3
"""
Eclair prepare-commit-msg hook.
Drafts a commit message from the staged diff before your editor opens.
Uses your last 5 commits as few-shot examples so the draft matches YOUR voice.

Skips when:
  - You already passed -m
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
import memory


def get_recent_git_messages(n=5):
    """Fall back to git log if learnings.db is empty."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{n}", "--pretty=%B%n---ENDMSG---"],
            capture_output=True, text=True
        )
        chunks = [c.strip() for c in result.stdout.split("---ENDMSG---") if c.strip()]
        return chunks[:n]
    except Exception:
        return []


def get_few_shot_examples(repo_path):
    """Prefer learnings.db, fall back to git log."""
    examples = memory.get_recent_commit_messages(repo_path=repo_path, n=5)
    if not examples:
        examples = get_recent_git_messages(n=5)
    return [e for e in examples if e and not e.startswith("Merge ")][:5]


BASE_PROMPT = """\
Write a git commit message for this staged diff.

Rules:
- First line: conventional commits format -- type(scope): subject (under 72 chars)
  Types: feat / fix / refactor / test / docs / chore / style / perf
- Optional: blank line then 1-3 tight bullet points if non-trivial
- No filler. No "this commit". Be direct and specific.
- Output ONLY the commit message text.
"""

FEW_SHOT_BLOCK = """\

## Your recent commit messages (match this style)
{examples}
"""

DIFF_BLOCK = """\

## This commit's diff
```diff
{diff}
```
"""


def build_prompt(diff, repo_path):
    parts = [BASE_PROMPT]

    examples = get_few_shot_examples(repo_path)
    if examples:
        formatted = "\n\n---\n".join(examples)
        parts.append(FEW_SHOT_BLOCK.format(examples=formatted))

    parts.append(DIFF_BLOCK.format(diff=diff[:6000]))
    return "".join(parts)


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    commit_msg_file = Path(sys.argv[1])
    source = sys.argv[2] if len(sys.argv) > 2 else ""

    if source in ("message", "merge", "squash", "commit"):
        sys.exit(0)

    diff = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True
    ).stdout.strip()

    if not diff:
        sys.exit(0)

    files = [
        f.strip() for f in subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True
        ).stdout.strip().splitlines() if f.strip()
    ]

    repo_path = Path.cwd()
    print("\nEclair is drafting your commit message...", flush=True)

    try:
        msg = invoke(
            prompt=build_prompt(diff, repo_path),
            model=FAST_MODEL,
            max_tokens=256,
        ).strip()
    except Exception as e:
        print(f"[eclair] Could not draft message: {e}", flush=True)
        sys.exit(0)

    # Strip stray code fences if Claude wrapped the message
    if msg.startswith("```"):
        msg = "\n".join(msg.splitlines()[1:-1] if msg.endswith("```") else msg.splitlines()[1:])

    commit_msg_file.write_text(msg + "\n", encoding="utf-8")
    memory.log_commit_draft(repo_path, files, msg)

    first_line = msg.splitlines()[0] if msg else ""
    print(f"   -> {first_line}\n", flush=True)


if __name__ == "__main__":
    main()
