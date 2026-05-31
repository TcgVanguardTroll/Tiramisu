#!/usr/bin/env python3
"""
Start a task with Croissant — define scope, acceptance criteria, and explicit out-of-scope
BEFORE touching any code. Prevents scope creep by making boundaries explicit up front.

Usage:
    python3 G:/tiramisu-portable/scripts/start_task.py
    python3 G:/tiramisu-portable/scripts/start_task.py "add dark mode to settings page"
"""
import sys
import os
import re
from pathlib import Path

# Windows cp1252 consoles can't print emoji — reconfigure to UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream, DEFAULT_MODEL
from steering import load_steering
from personas import pair as persona_pair

WORKSPACE = ROOT / "shared_workspace" / "tasks"

PROMPT_TEMPLATE = """\
I'm about to start this task: {task}

Please give me:

1. **Acceptance criteria** — WHEN/THEN/SHALL format. What does "done" look like exactly?
2. **Explicitly OUT of scope** — what am I NOT doing in this task? Be specific.
3. **Task breakdown** — ordered steps, one PR per step if non-trivial.
4. **Risks / scope traps** — what might tempt me to go beyond scope?

Keep it tight. This is a contract I'll hold myself to before writing a single line.
"""

SCOPE_CHECK_TEMPLATE = """\
I'm working on this task (defined below) and I'm wondering if something is in scope.

--- ORIGINAL TASK DEFINITION ---
{plan}
--- END TASK DEFINITION ---

New thing I'm considering: {question}

Is this in scope? Answer with IN SCOPE, OUT OF SCOPE, or NEW TASK, then a one-sentence reason.
"""


def save_plan(task: str, plan: str) -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", task[:50]).strip("_")
    out = WORKSPACE / f"{safe}.md"
    out.write_text(
        f"# Task: {task}\n\n{plan}\n",
        encoding="utf-8"
    )
    return out


def scope_check(plan: str, system: str):
    print("\n💬 Ask Croissant if something is in scope.")
    print("   (Enter a blank line to exit scope-check mode)\n")
    while True:
        try:
            question = input("Is this in scope? › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print()
        invoke_stream(
            prompt=SCOPE_CHECK_TEMPLATE.format(plan=plan, question=question),
            system=system,
            model=DEFAULT_MODEL,
            max_tokens=256,
        )
        print()


def main():
    system = load_steering(
        agent="croissant",
        languages=None,
        include_engineering=False,      # Croissant plans scope, doesn't enforce style
        include_universal_style=False,
        include_preferences=True,
    )

    print("=" * 60)
    print(f"{persona_pair('croissant')}  Croissant — Task Scope Session")
    print("=" * 60)
    print()

    # Task description from arg or prompt
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:]).strip()
        print(f"Task: {task}\n")
    else:
        print("Describe the task you're about to start.")
        print("Be specific — vague input = vague scope.\n")
        try:
            task = input("Task: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

    if not task:
        print("No task provided. Exiting.")
        sys.exit(0)

    print(f"\n[croissant] {persona_pair('croissant')} Defining scope...\n")
    print("-" * 60)

    plan = invoke_stream(
        prompt=PROMPT_TEMPLATE.format(task=task),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=2048,
    )

    print("-" * 60)

    # Save option
    print()
    try:
        save = input("Save this plan? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        save = "n"

    saved_path = None
    if save != "n":
        saved_path = save_plan(task, plan)
        print(f"✓ Saved → {saved_path}\n")

    # Scope check loop
    try:
        check = input("Want to check if something is in scope? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        check = "n"

    if check == "y":
        scope_check(plan, system)

    print(f"\n{persona_pair('croissant')}  Scope defined. Stick to it. Good luck.\n")


if __name__ == "__main__":
    main()
