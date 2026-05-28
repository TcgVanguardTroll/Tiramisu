#!/usr/bin/env python3
"""
Install Tiramisu git hooks into any project repo.

Usage:
    t hook                   # install into current directory
    t hook <path-to-repo>    # install into a specific repo

Installs:
    pre-commit          ->  Cookie reviews staged diff before every commit
    prepare-commit-msg  ->  Eclair drafts the commit message from the staged diff
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
PYTHON_EXE    = sys.executable


HOOKS = {
    "pre-commit": {
        "script": TIRAMISU_ROOT / "hooks" / "cookie_review.py",
        "template": """\
#!/bin/sh
# Tiramisu -- Cookie pre-commit review
# Installed by: {installer}
exec "{python}" "{hook_script}"
""",
        "description": "Cookie pre-commit reviewer",
        "marker": "cookie_review",
    },
    "prepare-commit-msg": {
        "script": TIRAMISU_ROOT / "hooks" / "eclair_commit_msg.py",
        "template": """\
#!/bin/sh
# Tiramisu -- Eclair commit message drafter
# Installed by: {installer}
exec "{python}" "{hook_script}" "$@"
""",
        "description": "Eclair commit message drafter",
        "marker": "eclair_commit_msg",
    },
    "post-commit": {
        "script": TIRAMISU_ROOT / "hooks" / "eclair_post_commit.py",
        "template": """\
#!/bin/sh
# Tiramisu -- Eclair post-commit (captures final message for learning)
# Installed by: {installer}
exec "{python}" "{hook_script}"
""",
        "description": "Eclair commit-outcome capture (learning loop)",
        "marker": "eclair_post_commit",
    },
}


def install_hook(hooks_dir: Path, hook_name: str, hook_def: dict):
    hook_file   = hooks_dir / hook_name
    hook_script = hook_def["script"]
    marker      = hook_def["marker"]
    description = hook_def["description"]

    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8", errors="replace")
        if marker in existing:
            print(f"  [update] {hook_name} ({description}) already installed — refreshing.")
        else:
            print(f"  [warn]   A {hook_name} hook already exists at {hook_file}")
            try:
                answer = input(f"           Overwrite it? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                print(f"           Skipped {hook_name}.")
                return False

    content = hook_def["template"].format(
        installer=__file__,
        python=str(PYTHON_EXE).replace("\\", "/"),
        hook_script=str(hook_script).replace("\\", "/"),
    )
    hook_file.write_text(content, encoding="utf-8")

    current = os.stat(hook_file).st_mode
    os.chmod(hook_file, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def install(repo_path: Path):
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        print(f"  [error] {repo_path} is not a git repository (no .git dir found)")
        sys.exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    print(f"\nInstalling Tiramisu hooks into: {repo_path}\n")

    results = {}
    for hook_name, hook_def in HOOKS.items():
        results[hook_name] = install_hook(hooks_dir, hook_name, hook_def)

    print()
    for hook_name, ok in results.items():
        status = "installed" if ok else "skipped"
        print(f"  {'ok' if ok else '--'}  {hook_name:<24} {status}")

    print(f"\n  Python : {PYTHON_EXE}")
    print()
    print("  Every commit will:")
    print("    1. Eclair drafts the commit message from your diff (edit freely)")
    print("    2. Cookie reviews the staged diff and blocks on BLOCKERs")
    print()
    print("  To skip hooks for one commit:  git commit --no-verify")
    print()


def main():
    if len(sys.argv) < 2:
        target = Path.cwd()
    else:
        target = Path(sys.argv[1]).resolve()

    install(target)


if __name__ == "__main__":
    main()
