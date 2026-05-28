#!/usr/bin/env python3
"""
Cookie PR review — reviews everything between your branch and main.
Reads the full diff, commit log, and changed file contents.

Usage:
    t pr              # auto-detects main or master
    t pr develop      # compare against a specific base branch
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

from llm import invoke_stream, DEFAULT_MODEL
from steering import load_steering, detect_languages

MAX_DIFF_CHARS     = 12000
MAX_PER_FILE_CHARS = 4000
MAX_FILES          = 8


def find_base_branch(specified: str | None) -> str:
    if specified:
        return specified
    for candidate in ("origin/main", "origin/master", "main", "master"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            capture_output=True
        )
        if result.returncode == 0:
            return candidate
    return "main"


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def get_pr_info(base: str) -> tuple[str, str, list[str], str]:
    diff    = run(["git", "diff", f"{base}...HEAD"])
    log     = run(["git", "log", f"{base}...HEAD", "--oneline"])
    stat    = run(["git", "diff", f"{base}...HEAD", "--stat"])
    files   = [
        f.strip()
        for f in run(["git", "diff", f"{base}...HEAD", "--name-only"]).splitlines()
        if f.strip()
    ]
    return diff, log, files, stat


def build_file_context(files: list[str]) -> str:
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
            content = content[:MAX_PER_FILE_CHARS] + f"\n... [truncated — {len(content)} chars total]"
        ext = p.suffix.lstrip(".")
        parts.append(f"### {name}\n```{ext}\n{content}\n```")
    return "\n\n".join(parts)


PROMPT = """\
This is a full PR review — not a quick commit check. Be thorough.

## Commits in this PR
{log}

## Full diff
```diff
{diff}
```

## Current state of changed files
{file_context}

Review for:
- Correctness — logic bugs, broken callers, wrong assumptions
- Edge cases — None/null, empty collections, concurrency, error paths
- Design — wrong abstraction, unnecessary coupling, missing separation
- Security — injection, leaked credentials, unvalidated input
- Test coverage — what's untested that should be
- Anything you would send back before merging

Be specific: file and line number. No praise needed.
"""


def main():
    base = find_base_branch(sys.argv[1] if len(sys.argv) > 1 else None)
    diff, log, files, stat = get_pr_info(base)

    if not diff:
        print(f"\n[pr] No changes between HEAD and {base}.")
        sys.exit(0)

    if not log:
        print(f"\n[pr] Already up to date with {base}.")
        sys.exit(0)

    file_context = build_file_context(files)

    languages = detect_languages(files)
    system = load_steering(
        agent="cookie",
        languages=languages,
        include_engineering=True,
        include_communication=True,   # PR review = include commit/comment style
        include_universal_style=True,
        include_preferences=True,
    )

    lang_label = ("/".join(languages)) if languages else "no language detected"
    print(f"\nCookie is reviewing your PR (vs {base}) [{lang_label}]\n")
    for line in stat.splitlines()[-5:]:  # last few lines of stat (summary)
        print(f"  {line}")
    print(f"\n  Commits:")
    for line in log.splitlines()[:8]:
        print(f"    {line}")
    if len(log.splitlines()) > 8:
        print(f"    ... and {len(log.splitlines()) - 8} more")
    print()
    print("-" * 60)

    invoke_stream(
        prompt=PROMPT.format(
            log=log[:2000],
            diff=diff[:MAX_DIFF_CHARS],
            file_context=file_context,
        ),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=3000,
    )

    print("-" * 60)
    print()


if __name__ == "__main__":
    main()
