#!/usr/bin/env python3
"""
Tiramisu — the orchestrator dispatch.

A single natural-language entry point. The user types `tiramisu "do this thing"`
and Tiramisu (the red tri Aussie Shepherd) picks the right agent and runs the
right command. If you already know which agent you want, use `t <cmd>` directly
to skip the routing step.

Usage:
    tiramisu "add a logout button to the header"
    tiramisu "scope a refactor of the auth layer"
    tiramisu "review my changes"
    tiramisu "look over the codebase"
    tiramisu "remember that I prefer guard clauses"
    tiramisu "show me what I've been working on"
"""
import os
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


# What the router can route to. Keys are the t.bat subcommand; values describe
# when to pick them. The prompt below renders these for the model.
ROUTES = {
    "task":      "define scope, acceptance criteria, IN/OUT, and risks BEFORE writing code",
    "implement": "write or edit code; create new files; refactor",
    "scan":      "read the current directory in full to look for issues; no commit involved",
    "review":    "review the currently staged diff right before a commit",
    "pr":        "review the whole current branch versus main (final pre-merge check)",
    "learn":     "record a new user preference for the agents to remember",
    "reflect":   "produce a weekly insights report from accumulated data",
    "help":      "show the command list / general help",
}

ROUTER_PROMPT = """\
You are Tiramisu, the orchestrator. The user has typed a natural-language request.
Pick the SINGLE best command to handle it from this list:

{routes}

Examples of correct routing:
  "add a logout button"               -> implement
  "scope a refactor of auth"          -> task
  "is my code clean"                  -> scan
  "review my staged diff"             -> review
  "check this PR"                     -> pr
  "remember I like guard clauses"     -> learn
  "show me my patterns"               -> reflect
  "what can you do"                   -> help

Rules:
- Output ONLY one word from the list. No quotes, no explanation, no punctuation.
- If genuinely ambiguous, prefer `task` (scoping first is the safest default).

User request: {input}

Command:"""


def route(user_input: str) -> str:
    """Ask FAST_MODEL which agent should handle this request."""
    routes_block = "\n".join(f"  - {k:10} -- {v}" for k, v in ROUTES.items())
    prompt = ROUTER_PROMPT.format(routes=routes_block, input=user_input)

    try:
        raw = invoke(prompt=prompt, model=FAST_MODEL, max_tokens=10, temperature=0.0)
    except Exception as e:
        print(f"[tiramisu] router failed ({type(e).__name__}: {e}); "
              f"falling back to `task`.", file=sys.stderr)
        return "task"

    # Strip punctuation, whitespace, take first token
    cmd = raw.strip().lower().split()[0].strip(".,;:'\"()[]") if raw.strip() else ""

    if cmd not in ROUTES:
        print(f"[tiramisu] router returned unknown command {cmd!r}; "
              f"falling back to `task`.", file=sys.stderr)
        return "task"

    return cmd


def run_subcommand(cmd: str, user_input: str) -> int:
    """Exec t.bat <cmd> [user_input]. Returns the subprocess returncode (does not exit)."""
    t_bat = ROOT / "t.bat"
    if not t_bat.exists():
        print(f"[tiramisu] missing dispatcher: {t_bat}", file=sys.stderr)
        return 1

    # Commands that take a free-text description as their argument
    takes_input = {"task", "implement", "learn"}

    if cmd in takes_input:
        args = [str(t_bat), cmd, user_input]
    else:
        # scan, review, pr, reflect, help — natural-language input doesn't help them
        args = [str(t_bat), cmd]

    # On Windows, .bat files need shell=True to be invoked through cmd.exe
    result = subprocess.run(args, shell=(os.name == "nt"))
    return result.returncode


REPL_BUILTINS = {
    "exit", "quit", "bye", "q", ":q",
}


def print_routes():
    print("\n  Routing table (Tiramisu picks one of these per input):")
    for k, v in ROUTES.items():
        print(f"    t {k:10}  --  {v}")
    print()


def repl():
    print("\n🐕  Tiramisu — interactive mode")
    print("    Type a request or question. The right agent will run.")
    print("    Built-ins: 'help' for routes, 'exit' / Ctrl+D to leave.\n")

    while True:
        try:
            user_input = input("tiramisu > ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not user_input:
            continue

        lower = user_input.lower()
        if lower in REPL_BUILTINS:
            break
        if lower in ("help", "?", "h"):
            print_routes()
            continue
        if lower in ("clear", "cls"):
            # ANSI clear; works in modern Windows terminals
            print("\033[2J\033[H", end="")
            continue

        try:
            cmd = route(user_input)
            print(f"  ->  t {cmd}\n")
            run_subcommand(cmd, user_input)
            print()  # spacer before next prompt
        except KeyboardInterrupt:
            print("\n[interrupted -- back at the prompt]\n")
            continue

    print("🐕  bye\n")


def main():
    args = sys.argv[1:]

    # No args: enter interactive REPL
    if not args:
        repl()
        return

    # One-shot: route, execute, exit with subcommand's code
    user_input = " ".join(args).strip()
    if not user_input:
        print("[tiramisu] empty input")
        sys.exit(1)

    cmd = route(user_input)
    print(f"\n🐕  Tiramisu  ->  t {cmd}    (\"{user_input[:60]}{'...' if len(user_input) > 60 else ''}\")\n")
    sys.exit(run_subcommand(cmd, user_input))


if __name__ == "__main__":
    main()
