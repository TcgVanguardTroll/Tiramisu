#!/usr/bin/env python3
"""
Eclair, the implementation agent.

Writes code with full codebase access. Uses Anthropic's tool-use API to
iteratively read files, search the codebase, edit code, and verify changes.

Usage:
    t implement "add a logout button to the header"
    t implement --auto "fix the failing test in test_auth.py"
    t implement --yes "..."         # skip ALL confirmations (use with care)
    t implement --max 100 "..."     # raise iteration cap

What you get:
    1. Steering composition (Eclair persona + engineering principles + code style + your preferences)
    2. Tools: read_file, write_file, edit_file, list_files, glob, grep, run_shell, done
    3. Confirmation prompts before any file write or shell command (unless --auto/--yes)
    4. A streaming view of Eclair's reasoning as she works
    5. After she finishes, run `git diff` to see what changed.
       Run `git commit` to trigger Cookie's review automatically.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import _client, DEFAULT_MODEL
from steering import load_steering
import memory


MAX_GREP_RESULTS  = 100
MAX_LIST_RESULTS  = 200
MAX_SHELL_OUTPUT  = 5000
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".idea", ".next", "coverage",
    ".mypy_cache", ".pytest_cache",
}


# ----- Tool definitions -----

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Use this to understand existing code before editing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file, relative or absolute."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file. OVERWRITES the file if it exists, creates it otherwise. "
            "Use for new files or when you need to fully rewrite a file. For modifying existing code, prefer edit_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a surgical edit by replacing exact text in a file. "
            "old_string must match EXACTLY, including all whitespace and indentation, "
            "and must be unique in the file (or the call will fail). Prefer this over write_file for modifications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory. Use to explore codebase structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path. Defaults to current working directory."},
                "recursive": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern like '**/*.py' or 'src/**/auth*.ts'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents for a regex pattern. Returns 'path:line: matching content'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Directory to search. Defaults to current dir."},
                "glob": {"type": "string", "description": "Optional file filter, e.g. '*.py'."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Run a shell command. Use for tests, linters, builds. USER WILL CONFIRM unless --yes was passed. "
            "Returns exit code and combined stdout+stderr."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "done",
        "description": "Call this when implementation is complete. Provide a clear summary of what was changed and why.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
]


# ----- Tool execution -----

class AgentState:
    def __init__(self, auto_writes=False, auto_shell=False):
        self.auto_writes = auto_writes
        self.auto_shell = auto_shell
        self.files_changed = set()
        self.done_summary = None

    def confirm(self, message, default_yes=True):
        prompt = "[Y/n]" if default_yes else "[y/N]"
        try:
            ans = input(f"    {message} {prompt} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if not ans:
            return default_yes
        return ans == "y"


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def execute_tool(name, tool_input, state: AgentState):
    """Execute a tool call. Returns string result for the model."""
    try:
        if name == "read_file":
            p = Path(tool_input["path"])
            if not p.exists():
                return f"Error: file not found: {p}"
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > 30000:
                return text[:30000] + f"\n... [truncated -- file is {len(text)} chars total]"
            return text

        elif name == "write_file":
            p = Path(tool_input["path"])
            content = tool_input["content"]
            is_new = not p.exists()
            label = "create" if is_new else "overwrite"

            print(f"\n  [{label}] {p}  ({len(content)} chars)")
            if not is_new:
                old = p.read_text(encoding="utf-8", errors="replace")
                print(f"           replacing {len(old)} existing chars")
            preview = content[:200].replace("\n", "\n           ")
            print(f"           preview: {preview}{'...' if len(content) > 200 else ''}")

            if not state.auto_writes:
                if not state.confirm("apply?"):
                    return "User declined the write."

            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            state.files_changed.add(str(p))
            return f"Wrote {len(content)} chars to {p}."

        elif name == "edit_file":
            p = Path(tool_input["path"])
            old = tool_input["old_string"]
            new = tool_input["new_string"]
            if not p.exists():
                return f"Error: file not found: {p}"
            content = p.read_text(encoding="utf-8", errors="replace")
            if old not in content:
                return f"Error: old_string not found in {p}. Re-read the file and try again with exact whitespace."
            count = content.count(old)
            if count > 1:
                return (
                    f"Error: old_string appears {count} times in {p} -- not unique. "
                    "Include more surrounding context to make it unique."
                )

            print(f"\n  [edit] {p}")
            for line in old.splitlines()[:6]:
                print(f"         - {line}")
            if len(old.splitlines()) > 6:
                print(f"         - ... ({len(old.splitlines()) - 6} more lines)")
            for line in new.splitlines()[:6]:
                print(f"         + {line}")
            if len(new.splitlines()) > 6:
                print(f"         + ... ({len(new.splitlines()) - 6} more lines)")

            if not state.auto_writes:
                if not state.confirm("apply?"):
                    return "User declined the edit."

            p.write_text(content.replace(old, new, 1), encoding="utf-8")
            state.files_changed.add(str(p))
            return f"Edited {p}."

        elif name == "list_files":
            p = Path(tool_input.get("path") or ".")
            recursive = tool_input.get("recursive", False)
            if not p.exists():
                return f"Error: path not found: {p}"
            items = []
            if recursive:
                for x in p.rglob("*"):
                    if _is_skipped(x):
                        continue
                    items.append(str(x))
                    if len(items) >= MAX_LIST_RESULTS:
                        break
            else:
                for x in sorted(p.iterdir()):
                    if _is_skipped(x):
                        continue
                    suffix = "/" if x.is_dir() else ""
                    items.append(f"{x}{suffix}")
            return "\n".join(items) if items else "(empty)"

        elif name == "glob":
            pattern = tool_input["pattern"]
            matches = []
            for m in Path(".").glob(pattern):
                if _is_skipped(m):
                    continue
                matches.append(str(m))
                if len(matches) >= MAX_LIST_RESULTS:
                    break
            return "\n".join(matches) if matches else "(no matches)"

        elif name == "grep":
            pattern = tool_input["pattern"]
            search_root = Path(tool_input.get("path") or ".")
            glob_filter = tool_input.get("glob")
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return f"Error: invalid regex: {e}"

            results = []
            iterator = search_root.rglob(glob_filter) if glob_filter else search_root.rglob("*")
            for f in iterator:
                if not f.is_file() or _is_skipped(f):
                    continue
                try:
                    for i, line in enumerate(
                        f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if regex.search(line):
                            results.append(f"{f}:{i}: {line[:300]}")
                            if len(results) >= MAX_GREP_RESULTS:
                                break
                except Exception:
                    pass
                if len(results) >= MAX_GREP_RESULTS:
                    break
            if not results:
                return "(no matches)"
            return "\n".join(results)

        elif name == "run_shell":
            cmd = tool_input["command"]
            print(f"\n  [shell] {cmd}")
            if not state.auto_shell:
                if not state.confirm("run?", default_yes=False):
                    return "User declined to run the command."
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=180
                )
            except subprocess.TimeoutExpired:
                return "Error: command timed out after 180s."
            out = (result.stdout or "") + (result.stderr or "")
            if len(out) > MAX_SHELL_OUTPUT:
                out = out[:MAX_SHELL_OUTPUT] + f"\n... [truncated -- {len(out)} chars total]"
            return f"Exit code: {result.returncode}\n{out}"

        elif name == "done":
            state.done_summary = tool_input.get("summary", "")
            return "Acknowledged. Implementation complete."

        else:
            return f"Error: unknown tool '{name}'"

    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"


# ----- Codebase overview -----

def build_codebase_overview(cwd: Path) -> str:
    parts = [f"Working directory: {cwd}"]

    # Top-level files
    try:
        top = sorted(
            x.name + ("/" if x.is_dir() else "")
            for x in cwd.iterdir()
            if not x.name.startswith(".") and x.name not in SKIP_DIRS
        )[:50]
        if top:
            parts.append("Top level: " + ", ".join(top))
    except Exception:
        pass

    # README excerpt if present
    for name in ("README.md", "README.rst", "readme.md", "README"):
        readme = cwd / name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
                excerpt = text[:1500].rstrip()
                if len(text) > 1500:
                    excerpt += "\n... [truncated]"
                parts.append(f"\n## {name}\n{excerpt}")
            except Exception:
                pass
            break

    return "\n".join(parts)


# ----- Agent loop -----

WORKING_MODE = """
# WORKING MODE -- ACTING WITH TOOLS

You have file read/write/search tools. Your job: implement the user's request
with surgical changes.

Loop:
  1. EXPLORE first -- use glob/grep/list_files/read_file to understand the
     codebase before editing. Don't guess at file paths.
  2. PLAN briefly -- say what you'll change and why before editing.
  3. EDIT surgically -- prefer edit_file over write_file when modifying
     existing code. Match indentation exactly.
  4. VERIFY when sensible -- if there are tests or a linter, run them via
     run_shell.
  5. When complete, call the `done` tool with a clear summary of what changed.

Rules:
  - Touch ONLY code that the request requires. No "while I'm here" cleanup.
  - If the request is ambiguous, ask the user before guessing.
  - Don't repeat tool calls -- you've seen what you've already read.
  - Don't call done() until the work is actually finished.
"""


def run_agent(initial_messages, system, state: AgentState, max_iterations=50):
    client = _client()
    messages = list(initial_messages)

    for iteration in range(1, max_iterations + 1):
        print(f"\n{'-' * 60}")
        print(f"  Iteration {iteration}")
        print('-' * 60)

        with client.messages.stream(
            model=DEFAULT_MODEL,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            print()
            final = stream.get_final_message()

        # Collect tool calls
        tool_uses = [b for b in final.content if b.type == "tool_use"]

        # If no tool calls, model has finished (without calling done)
        if not tool_uses:
            print("\n  [Eclair stopped without calling done()]")
            break

        # Execute each tool call
        tool_results = []
        for tu in tool_uses:
            print(f"\n  ↳ tool: {tu.name}")
            result = execute_tool(tu.name, tu.input, state)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        # Append to message history
        messages.append({"role": "assistant", "content": final.content})
        messages.append({"role": "user", "content": tool_results})

        # If Eclair called done, exit cleanly
        if state.done_summary is not None:
            break

    return state.done_summary


# ----- CLI entry -----

def parse_args():
    p = argparse.ArgumentParser(
        description="Eclair the implementation agent. Writes code with full codebase access."
    )
    p.add_argument("description", nargs="+", help="What to implement.")
    p.add_argument("--auto", action="store_true",
                   help="Skip confirmation on file writes (still confirms shell).")
    p.add_argument("--yes", action="store_true",
                   help="Skip ALL confirmations including shell. Use with care.")
    p.add_argument("--max", type=int, default=50,
                   help="Max iterations (default 50).")
    return p.parse_args()


def main():
    args = parse_args()
    description = " ".join(args.description).strip()

    if not description:
        print("[implement] No task description given.")
        sys.exit(1)

    cwd = Path.cwd()

    # Build steering: Eclair persona + engineering + universal style + preferences.
    # Language-specific style is omitted up front -- Eclair will see relevant
    # files via tool use and the steering doc is already long.
    system = load_steering(
        agent="eclair",
        languages=None,
        include_engineering=True,
        include_communication=False,
        include_universal_style=True,
        include_preferences=True,
    )
    system += WORKING_MODE

    overview = build_codebase_overview(cwd)

    initial_prompt = (
        f"Task: {description}\n\n"
        f"{overview}\n\n"
        "Begin by exploring the codebase as needed, then plan, then edit. "
        "Call done() with a summary when finished."
    )

    print(f"\n{'=' * 60}")
    print(f"  Eclair the implementer")
    print(f"  Task:  {description}")
    print(f"  Cwd:   {cwd}")
    if args.auto:
        print(f"  Mode:  --auto (no confirm on writes)")
    if args.yes:
        print(f"  Mode:  --yes (no confirm on anything)")
    print('=' * 60)

    state = AgentState(
        auto_writes=args.auto or args.yes,
        auto_shell=args.yes,
    )

    summary = run_agent(
        initial_messages=[{"role": "user", "content": initial_prompt}],
        system=system,
        state=state,
        max_iterations=args.max,
    )

    # Final report
    print(f"\n{'=' * 60}")
    print(f"  Eclair finished")
    print('=' * 60)
    if summary:
        print(f"\n{summary}\n")
    if state.files_changed:
        print(f"Files changed ({len(state.files_changed)}):")
        for f in sorted(state.files_changed):
            print(f"  - {f}")
        print("\nReview the diff: git diff")
        print("Commit (Cookie will review): git commit")
    else:
        print("No files were changed.")
    print()


if __name__ == "__main__":
    main()
