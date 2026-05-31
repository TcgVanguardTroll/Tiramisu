#!/usr/bin/env python3
"""
Tiramisu -- the orchestrator dispatch.

A single natural-language entry point. The user types `tiramisu "do this thing"`
and Tiramisu (the red tri Aussie Shepherd) picks the right agent and runs the
right command. If you already know which agent you want, use `t <cmd>` directly
to skip the routing step.

With no args, drops into an interactive REPL backed by prompt_toolkit:
  - persistent command history at ~/.tiramisu/.repl_history
  - tab completion on built-ins, phrase starters, and past prompts
  - rich-styled output and a spinner while routing

Usage:
    tiramisu                         # interactive REPL
    tiramisu add a logout button     # one-shot, no quotes needed
    tiramisu scope a refactor of auth
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
from personas import pair as persona_pair, pet as persona_pet, color as persona_color
import spinners as _spinners   # registers tiramisu spinners with rich

# Rich + prompt_toolkit are required at runtime. The dispatcher refuses to start
# without them, so import failures are loud, not silent.
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText


# What the router can route to. Keys are the t.bat subcommand; values describe
# when to pick them. The prompt below renders these for the model.
ROUTES = {
    "task":      "define scope, acceptance criteria, IN/OUT, and risks BEFORE writing code",
    "implement": "write or edit code; create new files; refactor",
    "scan":      "read the current directory in full to look for issues; no commit involved",
    "review":    "review the currently staged diff right before a commit",
    "pr":        "review the whole current branch versus main (final pre-merge check)",
    "chat":      "have a conversation: ask questions, explain code, think out loud, plan -- read-only, with memory",
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
  "what does this codebase do"        -> chat
  "explain the auth flow"             -> chat
  "how would I structure X"           -> chat
  "i want to think through Y"         -> chat
  "remember I like guard clauses"     -> learn
  "show me my patterns"               -> reflect
  "what can you do"                   -> help

Rules:
- Output ONLY one word from the list. No quotes, no explanation, no punctuation.
- If genuinely ambiguous, prefer `task` (scoping first is the safest default).

User request: {input}

Command:"""


HOME = Path.home()
HISTORY_FILE = Path(os.environ.get("TIRAMISU_HOME", HOME / ".tiramisu")) / ".repl_history"

console = Console()

PROMPT_STYLE = Style.from_dict({
    "tiramisu": "ansicyan bold",
    "arrow":    "ansigreen",
})


def route(user_input: str) -> str:
    """Ask FAST_MODEL which agent should handle this request."""
    routes_block = "\n".join(f"  - {k:10} -- {v}" for k, v in ROUTES.items())
    prompt = ROUTER_PROMPT.format(routes=routes_block, input=user_input)

    try:
        raw = invoke(prompt=prompt, model=FAST_MODEL, max_tokens=10, temperature=0.0)
    except Exception as e:
        console.print(f"[red][tiramisu][/red] router failed ({type(e).__name__}: {e}); "
                      f"falling back to [cyan]task[/cyan].")
        return "task"

    cmd = raw.strip().lower().split()[0].strip(".,;:'\"()[]") if raw.strip() else ""

    if cmd not in ROUTES:
        console.print(f"[yellow][tiramisu][/yellow] router returned unknown command "
                      f"[dim]{cmd!r}[/dim]; falling back to [cyan]task[/cyan].")
        return "task"

    return cmd


def run_subcommand(cmd: str, user_input: str) -> int:
    """Exec t.bat <cmd> [user_input]. Returns the subprocess returncode."""
    if os.name == "nt":
        t_dispatcher = ROOT / "t.bat"
    else:
        t_dispatcher = ROOT / "t"

    if not t_dispatcher.exists():
        console.print(f"[red][tiramisu][/red] missing dispatcher: {t_dispatcher}")
        return 1

    takes_input = {"task", "implement", "learn", "chat"}

    if cmd in takes_input:
        args = [str(t_dispatcher), cmd, user_input]
    else:
        args = [str(t_dispatcher), cmd]

    result = subprocess.run(args, shell=(os.name == "nt"))
    return result.returncode


# -------- REPL --------

REPL_BUILTINS = {"exit", "quit", "bye", "q", ":q"}

PHRASE_STARTERS = [
    "implement ",
    "scope ",
    "scan ",
    "review my staged diff",
    "look at the codebase",
    "remember ",
    "show me my patterns",
    "check this PR",
    "review the branch",
]


class TiramisuCompleter(Completer):
    """Complete on built-ins + phrase starters + recent history (newest first)."""
    def __init__(self, history: FileHistory):
        self._history = history

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.strip():
            return

        text_lower = text.lower()
        seen = set()

        all_candidates = list(REPL_BUILTINS) + ["help", "?", "clear", "cls"] + PHRASE_STARTERS

        for c in all_candidates:
            if c.lower().startswith(text_lower) and c not in seen:
                seen.add(c)
                yield Completion(c, start_position=-len(text))

        try:
            history_entries = list(self._history.load_history_strings())
        except Exception:
            history_entries = []
        for entry in reversed(history_entries):
            if entry.lower().startswith(text_lower) and entry not in seen:
                seen.add(entry)
                yield Completion(entry, start_position=-len(text))
                if len(seen) > 25:
                    break


def render_banner():
    body = (
        "[bold]Type a request or question.[/bold] The right agent will run.\n"
        "[dim]Built-ins:[/dim] help, exit, clear   "
        "[dim]Keys:[/dim] up/down history, Tab complete, Esc+Enter multi-line"
    )
    console.print(Panel(body, title=f"{persona_pair('tiramisu')}  Tiramisu", border_style="cyan", padding=(0, 2)))


def render_routes():
    console.print()
    console.print("[bold]  Routing table (Tiramisu picks one of these per input):[/bold]")
    for k, v in ROUTES.items():
        console.print(f"    [cyan]t {k:10}[/cyan]  [dim]{v}[/dim]")
    console.print()


def repl():
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.touch(exist_ok=True)
    history = FileHistory(str(HISTORY_FILE))

    entry_count = sum(1 for _ in history.load_history_strings())

    render_banner()
    if entry_count:
        console.print(f"[dim]   {HISTORY_FILE} loaded ({entry_count} entries)[/dim]\n")
    else:
        console.print()

    session = PromptSession(
        history=history,
        completer=TiramisuCompleter(history),
        complete_while_typing=False,
        complete_in_thread=True,
    )

    prompt_msg = FormattedText([
        ("class:tiramisu", "tiramisu"),
        ("class:arrow", " » "),
    ])

    while True:
        try:
            user_input = session.prompt(prompt_msg, style=PROMPT_STYLE).strip()
        except KeyboardInterrupt:
            console.print("[dim](use 'exit' to leave)[/dim]")
            continue
        except EOFError:
            break

        if not user_input:
            continue

        lower = user_input.lower()
        if lower in REPL_BUILTINS:
            break
        if lower in ("help", "?", "h"):
            render_routes()
            continue
        if lower in ("clear", "cls"):
            console.clear()
            continue

        try:
            with console.status("[dim]Tiramisu is routing…[/dim]", spinner=_spinners.chosen()):
                cmd = route(user_input)
            console.print(f"  [dim]→[/dim] [bold cyan]t {cmd}[/bold cyan]\n")
            run_subcommand(cmd, user_input)
            console.print()
        except KeyboardInterrupt:
            console.print("\n[yellow][interrupted -- back at the prompt][/yellow]\n")
            continue

    console.print(f"[bold cyan]{persona_pair('tiramisu')}  bye[/bold cyan]\n")


def main():
    args = sys.argv[1:]

    if not args:
        repl()
        return

    user_input = " ".join(args).strip()
    if not user_input:
        console.print("[red][tiramisu][/red] empty input")
        sys.exit(1)

    with console.status("[dim]Tiramisu is routing…[/dim]", spinner="dots"):
        cmd = route(user_input)

    preview = user_input if len(user_input) <= 60 else user_input[:60] + "..."
    console.print(f"\n[bold cyan]{persona_pair('tiramisu')}  Tiramisu[/bold cyan]  [dim]→[/dim]  "
                  f"[bold cyan]t {cmd}[/bold cyan]    [dim]({preview!r})[/dim]\n")

    sys.exit(run_subcommand(cmd, user_input))


if __name__ == "__main__":
    main()
