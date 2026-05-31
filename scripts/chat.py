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
from rich.live import Live
from rich.markdown import Markdown
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


# ----- read-only tools -----

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
]


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def execute_tool(name, tool_input) -> str:
    """Execute a read-only tool. Returns string result for the model."""
    try:
        if name == "read_file":
            p = Path(tool_input["path"])
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

        else:
            return f"Error: unknown tool '{name}'"

    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"


# ----- chat-mode system prompt -----

CHAT_MODE_INSTRUCTIONS = """

# CHAT MODE

You're in conversational mode with the user. They want to think out loud,
explore the codebase, plan, or ask questions.

You have READ tools (read_file, glob, grep, list_files) but you NEVER edit
code yourself in this mode. When the user wants action, suggest the specific
`t` command they should run:
  - Want code written?     "exit chat and run:  t implement <description>"
  - Want a scope plan?     "run:  t task <description>"
  - Want a code review?    "run:  t scan <path>"
  - Want a PR review?      "run:  t pr"

Style:
- Conversational and concise, NOT a structured report.
- Use the conversation history. If they say "it" or "that function", figure
  it out from context. Don't ask for clarification unless truly ambiguous.
- Keep most responses to 1-3 paragraphs unless they ask for depth.
- Don't restate what was already established.
- When you reference code, cite the file:line so they can jump there.
"""


# ----- pretty -----

console = Console()

PROMPT_STYLE = Style.from_dict({
    "you":   "ansicyan bold",
    "arrow": "ansigreen",
})


def render_banner():
    body = (
        "[bold]Chat mode.[/bold] Tiramisu can read files, search code, "
        "and remember context.\n"
        "[dim]She does NOT edit code -- for that, exit and run `t implement`.[/dim]\n"
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
            buffer = []
            with Live("", console=console, refresh_per_second=8,
                      vertical_overflow="visible", auto_refresh=False) as live:
                with client.messages.stream(
                    model=DEFAULT_MODEL,
                    max_tokens=4096,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        buffer.append(text)
                        content = "".join(buffer).rstrip()
                        if content:
                            # Show the persona marker once content starts arriving
                            live.update(Markdown(f"{persona_pet('tiramisu')}  {content}"),
                                        refresh=True)
                    final = stream.get_final_message()
            # If no text was streamed (pure tool call), nothing to render
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
