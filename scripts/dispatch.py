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
import research as _research   # background research kicker + pending notice

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
    "brainstorm": "bounce an idea around; generate options and alternatives; stress-test a plan BEFORE scoping it",
    "task":      "define scope, acceptance criteria, IN/OUT, and risks BEFORE writing code",
    "implement": "write or edit code; create new files; refactor",
    "scan":      "read the current directory in full to look for issues; no commit involved",
    "review":    "review the currently staged diff right before a commit",
    "pr":        "review the whole current branch versus main (final pre-merge check)",
    "chat":      "have a conversation: ask questions, explain code, think out loud, plan -- read-only, with memory",
    "learn":     "record a new user preference for the agents to remember",
    "reflect":   "produce a weekly insights report from accumulated data",
    "research":  "show Cannoli's research findings -- what's new from watched external sources",
    "onboard":   "create a brand-new agent persona for a job the crew can't cover yet",
    "help":      "show the command list / general help",
}

ROUTER_PROMPT = """\
You are Tiramisu, the orchestrator. The user has typed a natural-language request.
Pick the SINGLE best command to handle it from this list:

{routes}

Examples of correct routing:
  "add a logout button"               -> implement
  "scope a refactor of auth"          -> task
  "give me options for caching here"  -> brainstorm
  "is this idea even worth building"  -> brainstorm
  "we need an agent for infra review" -> onboard
  "is my code clean"                  -> scan
  "review my staged diff"             -> review
  "check this PR"                     -> pr
  "what does this codebase do"        -> chat
  "explain the auth flow"             -> chat
  "how would I structure X"           -> chat
  "i want to think through Y"         -> chat
  "remember I like guard clauses"     -> learn
  "show me my patterns"               -> reflect
  "what did cannoli find"             -> research
  "anything new from my sources"      -> research
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


def route_ex(user_input: str) -> tuple[str, str]:
    """Pick the command for this request. Returns (command, via) where via
    is one of: fast (exact command word, no API call), llm (router model),
    fallback (router returned an unknown command), error (API failure).

    Callers that route real traffic should log the decision via
    memory.log_route so `t reflect` can audit the router."""
    # Deterministic fast path: the input IS a command word ("scan", "pr",
    # "review", ...). Free, instant, and can't misroute. Only an exact
    # single-token match qualifies -- "help me think through X" must still
    # reach the LLM (it's a chat question, not `t help`).
    exact = user_input.strip().lower()
    if exact in ROUTES:
        return exact, "fast"

    routes_block = "\n".join(f"  - {k:10} -- {v}" for k, v in ROUTES.items())
    prompt = ROUTER_PROMPT.format(routes=routes_block, input=user_input)

    try:
        raw = invoke(prompt=prompt, model=FAST_MODEL, max_tokens=10, temperature=0.0)
    except Exception as e:
        console.print(f"[red][tiramisu][/red] router failed ({type(e).__name__}: {e}); "
                      f"falling back to [cyan]task[/cyan].")
        return "task", "error"

    cmd = raw.strip().lower().split()[0].strip(".,;:'\"()[]") if raw.strip() else ""

    if cmd not in ROUTES:
        console.print(f"[yellow][tiramisu][/yellow] router returned unknown command "
                      f"[dim]{cmd!r}[/dim]; falling back to [cyan]task[/cyan].")
        return "task", "fallback"

    return cmd, "llm"


def route(user_input: str) -> str:
    """Backward-compatible wrapper: just the command."""
    return route_ex(user_input)[0]


def _extract_path_arg(user_input: str) -> str | None:
    """Find a real filesystem path mentioned in the input.

    The router's input is natural language, so it can't be passed to
    `t scan` verbatim -- "is my code clean" is not a path. But "scan
    scripts/llm.py" mentions one, and dropping it silently scans the whole
    cwd instead. Only a token that actually exists on disk is safe to
    forward; everything else stays natural language. First match wins.
    """
    for token in user_input.split():
        candidate = token.strip("\"'`,;:!?")
        if not candidate or candidate in (".", ".."):
            continue
        if Path(candidate).exists():
            return candidate
    return None


def run_subcommand(cmd: str, user_input: str) -> int:
    """Exec t.bat <cmd> [user_input]. Returns the subprocess returncode."""
    if os.name == "nt":
        t_dispatcher = ROOT / "t.bat"
    else:
        t_dispatcher = ROOT / "t"

    if not t_dispatcher.exists():
        console.print(f"[red][tiramisu][/red] missing dispatcher: {t_dispatcher}")
        return 1

    takes_input = {"task", "implement", "learn", "chat", "brainstorm", "onboard"}

    if cmd in takes_input:
        args = [str(t_dispatcher), cmd, user_input]
    elif cmd == "scan" and (path := _extract_path_arg(user_input)):
        # "scan scripts/llm.py" should scan that file, not the whole cwd
        args = [str(t_dispatcher), cmd, path]
    else:
        args = [str(t_dispatcher), cmd]

    result = subprocess.run(args)
    return result.returncode


def _log_route_decision(user_input: str, cmd: str, via: str) -> None:
    """Best-effort routing log for `t reflect`'s router audit. Fail-soft:
    a logging failure must never block the user's actual command."""
    try:
        import memory
        memory.log_route(user_input, cmd, via)
    except Exception:
        pass


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

# Common subcommands per routable command, so the REPL completes e.g.
# `research benchmark` and `learn search`, not just the bare verb.
_SUBCOMMANDS = {
    "learn":    ["search ", "list", "forget "],
    "research": ["apply", "benchmark", "discover", "run", "list", "sources "],
    "pr":       ["--post"],
}
# Verbs that take no argument read better with no trailing space.
_TERMINAL = {"review", "reflect", "help"}


def command_completions() -> list[str]:
    """Autocomplete entries for the literal command vocabulary, DERIVED from
    ROUTES so it can never drift out of sync with what the router accepts.
    Each routable command plus its known subcommands."""
    out: list[str] = []
    for cmd in ROUTES:
        out.append(cmd if cmd in _TERMINAL else cmd + " ")
        for sub in _SUBCOMMANDS.get(cmd, []):
            out.append(f"{cmd} {sub}")
    return out


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

        all_candidates = (
            command_completions()
            + list(REPL_BUILTINS)
            + ["?", "clear", "cls"]
            + PHRASE_STARTERS
        )

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
                cmd, via = route_ex(user_input)
            _log_route_decision(user_input, cmd, via)
            console.print(f"  [dim]→[/dim] [bold cyan]t {cmd}[/bold cyan]\n")
            run_subcommand(cmd, user_input)
            console.print()
        except KeyboardInterrupt:
            console.print("\n[yellow][interrupted -- back at the prompt][/yellow]\n")
            continue

    console.print(f"[bold cyan]{persona_pair('tiramisu')}  bye[/bold cyan]\n")
    _research.print_pending_notice(console=console)


def main():
    # Autonomous research: if Cannoli's last scan was >7 days ago, kick off
    # a fresh one in a detached background process. Non-blocking; the user
    # never sees it run. They DO see a one-line notice on their next
    # invocation if findings are waiting (printed below + at REPL exit).
    _research.kick_off_background_if_stale()

    args = sys.argv[1:]

    if not args:
        # REPL path -- notice surfaces in repl() after exit, see below
        repl()
        return

    user_input = " ".join(args).strip()
    if not user_input:
        console.print("[red][tiramisu][/red] empty input")
        sys.exit(1)

    with console.status("[dim]Tiramisu is routing…[/dim]", spinner=_spinners.chosen()):
        cmd, via = route_ex(user_input)
    _log_route_decision(user_input, cmd, via)

    preview = user_input if len(user_input) <= 60 else user_input[:60] + "..."
    console.print(f"\n[bold cyan]{persona_pair('tiramisu')}  Tiramisu[/bold cyan]  [dim]→[/dim]  "
                  f"[bold cyan]t {cmd}[/bold cyan]    [dim]({preview!r})[/dim]\n")

    rc = run_subcommand(cmd, user_input)

    # Surface any pending research findings AFTER the user's main command
    # completed, so the notice doesn't interfere with their primary output.
    _research.print_pending_notice(console=console)

    sys.exit(rc)


if __name__ == "__main__":
    main()
