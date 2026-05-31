#!/usr/bin/env python3
"""
Tiramisu chat -- conversational mode with read-only codebase access.

You're talking to Tiramisu directly. She can read files, search the codebase,
and remember what you've discussed within the session. She does NOT edit code
in this mode -- for that, exit chat and use `t implement`.

Usage:
    t chat                          # start with no initial prompt
    t chat what does this codebase do
    t chat "explain the auth flow"

Built-ins inside the chat:
    exit, quit, bye, q   -- leave chat, return to outer shell
    Ctrl+D               -- same as exit
    Ctrl+C               -- cancel current line / interrupt running response
"""
import argparse
import os
import re
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
from personas import pair as persona_pair, pet as persona_pet

from rich.console import Console
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style


# ----- config -----

MAX_FILE_CHARS    = 30000
MAX_GREP_RESULTS  = 100
MAX_LIST_RESULTS  = 200
MAX_TOOL_DEPTH    = 10   # max recursive tool turns per user input

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".idea", ".next", "coverage",
    ".mypy_cache", ".pytest_cache",
}

CHAT_HISTORY_FILE = Path(
    os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu")
) / ".chat_history"

EXIT_WORDS = {"exit", "quit", "bye", "q", ":q"}


# ----- tools (read + write + shell, with confirmation on write/shell) -----

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from disk. Use when the user asks about specific code or configuration.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative or absolute file path."}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List a directory's contents. Use to explore project structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, defaults to current dir."},
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
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents for a regex pattern. Returns 'path:line: matching text'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Directory to search. Defaults to current dir."},
                "glob": {"type": "string", "description": "Optional file pattern filter, e.g. '*.py'."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a surgical edit by replacing exact text in a file. "
            "old_string must match EXACTLY (including whitespace), and must be UNIQUE in the file. "
            "Use this for modifying existing code; prefer it over write_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file. OVERWRITES if it exists, creates it otherwise. "
            "Use for NEW files or full rewrites. For modifying existing code, prefer edit_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Run a shell command. Use sparingly -- for running tests, linters, builds. "
            "User will be prompted to confirm before the command runs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _validate_path(p: Path) -> str | None:
    """Return an error string if path escapes cwd, or None if safe."""
    try:
        resolved = p.resolve()
    except (OSError, ValueError) as e:
        return f"Error: invalid path: {e}"
    if not resolved.is_relative_to(Path.cwd().resolve()):
        return f"Error: path {p} is outside the working directory."
    return None


def _confirm(prompt_msg: str, default_yes: bool = True) -> bool:
    """Ask the user [Y/n] (or [y/N]) before a write/shell action."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"    {prompt_msg} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return default_yes
    return ans == "y"


def execute_tool(name, tool_input) -> str:
    """Execute a chat tool. Read tools run silently; write/shell tools prompt."""
    try:
        if name == "read_file":
            p = Path(tool_input["path"])
            if err := _validate_path(p):
                return err
            if not p.exists():
                return f"Error: file not found: {p}"
            if not p.is_file():
                return f"Error: not a file: {p}"
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_FILE_CHARS:
                return text[:MAX_FILE_CHARS] + f"\n... [truncated -- {len(text)} chars total]"
            return text

        elif name == "list_files":
            p = Path(tool_input.get("path") or ".")
            if err := _validate_path(p):
                return err
            if not p.exists():
                return f"Error: path not found: {p}"
            recursive = tool_input.get("recursive", False)
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
                    items.append(f"{x.name}{suffix}")
            return "\n".join(items) if items else "(empty)"

        elif name == "glob":
            matches = []
            for m in Path(".").glob(tool_input["pattern"]):
                if _is_skipped(m):
                    continue
                matches.append(str(m))
                if len(matches) >= MAX_LIST_RESULTS:
                    break
            return "\n".join(matches) if matches else "(no matches)"

        elif name == "grep":
            try:
                regex = re.compile(tool_input["pattern"])
            except re.error as e:
                return f"Error: invalid regex: {e}"
            search_root = Path(tool_input.get("path") or ".")
            if err := _validate_path(search_root):
                return err
            glob_filter = tool_input.get("glob")
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
            return "\n".join(results) if results else "(no matches)"

        elif name == "edit_file":
            p   = Path(tool_input["path"])
            old = tool_input["old_string"]
            new = tool_input["new_string"]
            if err := _validate_path(p):
                return err
            if not p.exists():
                return f"Error: file not found: {p}"
            content = p.read_text(encoding="utf-8", errors="replace")
            if old not in content:
                return f"Error: old_string not found in {p}. Re-read the file and try again with exact whitespace."
            count = content.count(old)
            if count > 1:
                return (f"Error: old_string appears {count} times in {p} -- not unique. "
                        "Include more surrounding context to make it unique.")
            print(f"\n  [edit] {p}")
            for line in old.splitlines()[:6]:
                print(f"         - {line}")
            if len(old.splitlines()) > 6:
                print(f"         - ... ({len(old.splitlines()) - 6} more lines)")
            for line in new.splitlines()[:6]:
                print(f"         + {line}")
            if len(new.splitlines()) > 6:
                print(f"         + ... ({len(new.splitlines()) - 6} more lines)")
            if not _confirm("apply?"):
                return "User declined the edit."
            p.write_text(content.replace(old, new, 1), encoding="utf-8")
            return f"Edited {p}."

        elif name == "write_file":
            p       = Path(tool_input["path"])
            content = tool_input["content"]
            if err := _validate_path(p):
                return err
            is_new = not p.exists()
            label  = "create" if is_new else "overwrite"
            print(f"\n  [{label}] {p}  ({len(content)} chars)")
            if not is_new:
                old = p.read_text(encoding="utf-8", errors="replace")
                print(f"           replacing {len(old)} existing chars")
            preview = content[:200].replace("\n", "\n           ")
            print(f"           preview: {preview}{'...' if len(content) > 200 else ''}")
            if not _confirm("apply?"):
                return "User declined the write."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {p}."

        elif name == "run_shell":
            cmd = tool_input["command"]
            print(f"\n  [shell] {cmd}")
            if not _confirm("run?", default_yes=False):
                return "User declined to run the command."
            try:
                import subprocess
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=180
                )
            except subprocess.TimeoutExpired:
                return "Error: command timed out after 180s."
            out = (result.stdout or "") + (result.stderr or "")
            if len(out) > 5000:
                out = out[:5000] + f"\n... [truncated -- {len(out)} chars total]"
            return f"Exit code: {result.returncode}\n{out}"

        else:
            return f"Error: unknown tool '{name}'"

    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"


# ----- chat-mode system prompt -----

CHAT_MODE_INSTRUCTIONS = """

# CHAT MODE

You're in conversational mode with the user. They want to think out loud,
explore the codebase, plan, or ask questions -- and sometimes have you
actually do the work right there in chat.

You have full tools:
  READ:  read_file, glob, grep, list_files
  WRITE: edit_file, write_file       (user confirms each one with Y/n)
  SHELL: run_shell                   (user confirms each command with y/N)

When the user asks "can you do that for me?" or "apply it" or "go ahead",
YOU ARE THE AGENT THAT DOES IT. Don't suggest they exit chat and run
`t implement` -- just call edit_file or write_file. The user will see a
preview and confirm.

When to suggest a different command instead:
  - Want a deep code review across many files?    "run:  t scan <path>"
  - Want a structured PR review?                  "run:  t pr"
  - Want Croissant to define WHEN/THEN/SHALL scope? "run:  t task <description>"
These are higher-level workflows with their own outputs. Quick file edits
belong here in chat.

Style:
- Conversational and concise, NOT a structured report.
- Read first, then plan in one sentence, then call the tool. Don't ask
  permission to call a tool; the user will see the confirmation prompt
  and decide there.
- Use the conversation history. If they say "it" or "that function",
  figure it out from context.
- Keep most responses to 1-3 paragraphs unless they ask for depth.
- When you reference code, cite the file:line so they can jump there.
- Surgical: touch only what the user asked about, no "while I'm here"
  cleanup of adjacent code.
"""


# ----- pretty -----

console = Console()

PROMPT_STYLE = Style.from_dict({
    "you":   "ansicyan bold",
    "arrow": "ansigreen",
})


def render_banner():
    body = (
        "[bold]Chat mode.[/bold] Tiramisu reads, edits, and runs shell commands.\n"
        "[dim]Writes prompt you with[/dim] [cyan]Y/n[/cyan][dim] before applying. "
        "Shell runs prompt with[/dim] [cyan]y/N[/cyan].\n"
        "[dim]Type[/dim] [cyan]exit[/cyan][dim] or Ctrl+D to leave chat.[/dim]"
    )
    console.print(Panel(body, title=f"{persona_pair('tiramisu')}  Tiramisu — chat", border_style="cyan", padding=(0, 2)))
    console.print()


# ----- chat turn loop -----

def chat_turn(messages, system, client):
    """
    Run one user turn end-to-end:
      - stream Claude's response
      - if Claude calls tools, execute them and recurse
      - returns when Claude produces text without tool calls
    """
    for depth in range(MAX_TOOL_DEPTH):
        try:
            with client.messages.stream(
                model=DEFAULT_MODEL,
                max_tokens=4096,
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                first_text = True
                for text in stream.text_stream:
                    if first_text:
                        console.print(f"[bold magenta]{persona_pet('tiramisu')}  [/bold magenta]", end="")
                        first_text = False
                    console.print(text, end="")
                if not first_text:
                    console.print()  # newline after streamed text
                final = stream.get_final_message()
        except KeyboardInterrupt:
            console.print("\n[yellow][interrupted][/yellow]")
            return

        # Append assistant's full content (text + any tool_use blocks)
        messages.append({"role": "assistant", "content": final.content})

        tool_uses = [b for b in final.content if b.type == "tool_use"]
        if not tool_uses:
            return  # done -- conversation continues on next user input

        # Execute tools, append results, loop so Claude can respond to them
        tool_results = []
        for tu in tool_uses:
            console.print(f"  [dim]↳ {tu.name}({_summarize_input(tu.input)})[/dim]")
            result = execute_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

    console.print("[yellow][chat] hit max tool depth -- back to prompt[/yellow]")


def _summarize_input(d) -> str:
    """Short one-line summary of a tool input dict for the inline trace."""
    if not isinstance(d, dict):
        return ""
    parts = []
    for k, v in d.items():
        s = str(v)
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


# ----- main -----

def parse_args():
    p = argparse.ArgumentParser(description="Tiramisu chat -- conversational mode")
    p.add_argument("initial", nargs="*", help="Optional initial question.")
    return p.parse_args()


def main():
    args = parse_args()
    initial = " ".join(args.initial).strip() if args.initial else None

    # Tiramisu's persona + chat-mode-specific instructions. We skip
    # engineering principles and code style here -- chat isn't reviewing
    # or implementing, it's talking. Preferences stay (they're personal).
    system = load_steering(
        agent="tiramisu",
        languages=None,
        include_engineering=False,
        include_universal_style=False,
        include_preferences=True,
    ) + CHAT_MODE_INSTRUCTIONS

    # History file (persistent across sessions)
    CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_HISTORY_FILE.touch(exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(CHAT_HISTORY_FILE)),
    )

    render_banner()

    client = _client()
    messages = []

    # If an initial prompt was given, kick off the conversation with it
    if initial:
        console.print(f"[bold cyan]you »[/bold cyan] {initial}\n")
        messages.append({"role": "user", "content": initial})
        chat_turn(messages, system, client)
        console.print()

    prompt_msg = FormattedText([
        ("class:you",   "you"),
        ("class:arrow", " » "),
    ])

    while True:
        try:
            user_input = session.prompt(prompt_msg, style=PROMPT_STYLE).strip()
        except KeyboardInterrupt:
            console.print("[dim](use 'exit' to leave chat)[/dim]")
            continue
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_WORDS:
            break

        messages.append({"role": "user", "content": user_input})
        try:
            chat_turn(messages, system, client)
        except KeyboardInterrupt:
            console.print("\n[yellow][interrupted -- back at chat prompt][/yellow]")
        console.print()

    console.print(f"[bold cyan]{persona_pair('tiramisu')}  exiting chat[/bold cyan]\n")


if __name__ == "__main__":
    main()
