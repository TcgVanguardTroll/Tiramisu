#!/usr/bin/env python3
"""
Install Tiramisu git hooks into any project repo.

Usage:
    python3 G:/tiramisu-portable/scripts/install_hooks.py <path-to-your-repo>
    python3 G:/tiramisu-portable/scripts/install_hooks.py .   # current directory

Installs:
    pre-commit  →  Cookie reviews staged diff before every commit
"""
import sys
import os
import stat
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TIRAMISU_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE    = sys.executable  # the Python that's running this script right now


PRE_COMMIT_HOOK = """\
#!/bin/sh
# Tiramisu — Cookie pre-commit review
# Installed by: {installer}
exec "{python}" "{hook_script}"
"""


def install(repo_path: Path):
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        print(f"✗  {repo_path} is not a git repository (no .git dir found)")
        sys.exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    hook_file   = hooks_dir / "pre-commit"
    hook_script = TIRAMISU_ROOT / "hooks" / "cookie_review.py"

    # Warn if a pre-commit hook already exists
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8", errors="replace")
        if "cookie_review" in existing:
            print("✓  Cookie hook already installed — updating.")
        else:
            print(f"⚠  A pre-commit hook already exists at {hook_file}")
            try:
                answer = input("   Overwrite it? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                print("   Skipped.")
                return

    content = PRE_COMMIT_HOOK.format(
        installer=__file__,
        python=str(PYTHON_EXE).replace("\\", "/"),
        hook_script=str(hook_script).replace("\\", "/"),
    )
    hook_file.write_text(content, encoding="utf-8")

    # Make executable (no-op on Windows but good practice)
    current = os.stat(hook_file).st_mode
    os.chmod(hook_file, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"\n✓  Cookie pre-commit hook installed in {repo_path}")
    print(f"   Hook file : {hook_file}")
    print(f"   Reviewer  : {hook_script}")
    print(f"   Python    : {PYTHON_EXE}")
    print()
    print("   Cookie will review staged changes before every commit.")
    print("   To skip a review:  git commit --no-verify")
    print()


def main():
    if len(sys.argv) < 2:
        # Default to current directory
        target = Path.cwd()
    else:
        target = Path(sys.argv[1]).resolve()

    install(target)


if __name__ == "__main__":
    main()
