#!/usr/bin/env python3
"""
Cookie ad-hoc code scan — review any file or directory without committing.

Usage:
    t scan                  # scan current directory
    t scan src/auth/        # scan a specific directory
    t scan src/auth/oauth.py  # scan a single file
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream_markdown, DEFAULT_MODEL
from steering import load_steering, detect_languages

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".go", ".rs", ".cs",
    ".cpp", ".c", ".h", ".rb", ".swift",
    ".scala", ".sh", ".bash",
}

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", "target", ".idea",
    ".next", "coverage", ".mypy_cache", ".pytest_cache",
}

MAX_TOTAL_CHARS  = 24000
MAX_PER_FILE     = 6000


def collect_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix in SOURCE_EXTENSIONS else []

    found = []
    for p in sorted(path.rglob("*")):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        if p.is_file() and p.suffix in SOURCE_EXTENSIONS:
            found.append(p)

    # Sort smallest-first so we pack more files into the budget
    return sorted(found, key=lambda f: f.stat().st_size)


def build_context(files: list[Path], base: Path) -> tuple[str, int]:
    parts = []
    total = 0
    for f in files:
        if total >= MAX_TOTAL_CHARS:
            break
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > MAX_PER_FILE:
            content = content[:MAX_PER_FILE] + f"\n... [truncated — {len(content)} chars total]"
        try:
            label = f.relative_to(base)
        except ValueError:
            label = f
        ext = f.suffix.lstrip(".")
        block = f"### {label}\n```{ext}\n{content}\n```"
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts), len(parts)


PROMPT = """\
Do a thorough review of this code. This is a full scan, not a quick commit check.

Look for:
- Bugs and logic errors
- Broken edge cases (None/null handling, empty inputs, off-by-one)
- Design problems — wrong abstractions, tight coupling, missing separation
- Security issues — injection, leaked secrets, unvalidated input
- Anything you would send back in a real code review

Be specific: file name and line number where possible.
Skip praise. If something is genuinely clean, a single "no issues found" is enough.

{context}
"""


def main():
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()

    if not target.exists():
        print(f"[scan] Path not found: {target}")
        sys.exit(1)

    files = collect_files(target)
    if not files:
        print(f"[scan] No source files found in: {target}")
        sys.exit(0)

    base = target if target.is_dir() else target.parent
    context, count = build_context(files, base)

    languages = detect_languages([str(f) for f in files[:count]])
    system = load_steering(
        agent="cookie",
        languages=languages,
        include_engineering=True,
        include_universal_style=True,
        include_preferences=True,
    )

    skipped = len(files) - count
    lang_label = ("/".join(languages)) if languages else "no language detected"
    print(f"\nCookie is scanning {count} file(s) ({lang_label})", end="")
    if skipped:
        print(f" -- {skipped} skipped over budget", end="")
    print(f"\n  {target}\n")
    print("-" * 60)

    invoke_stream_markdown(
        prompt=PROMPT.format(context=context),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=8000,
        thinking=True,
    )

    print("-" * 60)
    print()


if __name__ == "__main__":
    main()
