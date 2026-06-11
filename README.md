# Tiramisu 🍰

[![tests](https://github.com/TcgVanguardTroll/Tiramisu/actions/workflows/test.yml/badge.svg)](https://github.com/TcgVanguardTroll/Tiramisu/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Local-first](https://img.shields.io/badge/data-local--first-8A2BE2)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

A personal multi-agent dev system. A crew of pastry-named pets that scope your work, write code, review changes, draft commit messages, and learn your preferences over time. Runs as a CLI, integrates with git via hooks. No IDE plugin required.

MIT licensed. Windows-first; macOS / Linux supported via POSIX shell shims. 142-test suite across 10 modules covers the safety surfaces (path sandboxing, confirmation gating), the router, the 6-layer steering composition, the memory layer + schema migrations, and the source-config loader. Runs in seconds with no API calls (Anthropic client is mocked).

## The Crew

Every agent is named after a pastry that matches their fur. All dogs share 🐶, all cats share 🐱. Each agent's pastry is unique — that's the visual differentiator. Source of truth: `scripts/personas.py`.

| Agent | Sigil | Pet | Role |
|-------|:-----:|-----|------|
| **Tiramisu** | 🐶🍮 | Red tri mini American Shepherd | Orchestrator — herds the rest |
| **Éclair** | 🦡🍫 | Sleek black ferret | SDE — writes code with full codebase access |
| **Cookie** | 🐱🍪 | Tortoiseshell cat | Reviewer — judgmental, zero tolerance for sloppiness |
| **Croissant** | 🐶🥐 | Corgi | PM — scopes tasks, defines acceptance criteria |
| **Madeleine** | 🐱🧁 | Ginger tabby | Knowledge keeper — surfaces patterns from accumulated data |
| **Cannoli** | 🐶🍩 | Beagle | Researcher — scans external sources, ingests your library |
| **Mochi** | 🐰🍡 | White lop rabbit | Brainstorm — stress-tests ideas before they become tasks |
| **Brioche** | 🐶🍞 | Golden retriever | HR — drafts new agent personas for unmet needs |

---

## Two entry points: `tiramisu` and `t`

### `tiramisu` — natural language entry / REPL

Don't want to think about which agent? Just type:

```
tiramisu add a logout button to the header
tiramisu scope adding dark mode
tiramisu look over the codebase
tiramisu remember I prefer guard clauses
```

Tiramisu (the orchestrator) picks the right agent and runs it. No quotes needed.

With no args, you enter an interactive REPL:

```
$ tiramisu

🐶🍮  Tiramisu — interactive mode
      Type a request or question. The right agent will run.
      Built-ins: 'help' for routes, 'exit' / Ctrl+D to leave.

tiramisu » scope adding dark mode
  ->  t task
  [🐶🥐  Croissant streams a scope plan]

tiramisu » implement the dark mode toggle
  ->  t implement
  [🦡🍫  Éclair writes the code]

tiramisu » exit
```

REPL details (history, tab-completion, multi-line, keys) are in [docs/UI.md](docs/UI.md).

### `t <command>` — direct invocation

If you already know which agent you want, skip the router:

| Command | What it does |
|---------|--------------|
| `t hook` | Install Cookie + Éclair git hooks in the current repo (one time per repo) |
| `t task "desc"` | Croissant scopes the task — acceptance criteria, out-of-scope, risks |
| `t implement "desc"` | Éclair writes code with full codebase access via tool use |
| `t chat [question]` | Conversational mode — read + edit + shell tools with per-action confirmation, remembers context |
| `t scan [path]` | Cookie reads a file or directory in full and reports issues |
| `t review` | Cookie reviews the currently staged diff |
| `t pr [base]` | Cookie reviews your whole branch vs main |
| `t pr --post` | ...and posts inline comments at exact lines on the GitHub PR |
| `t learn "text"` | Teach the agents a preference (e.g. `t learn "prefer guard clauses"`) |
| `t learn list` | Show all active preferences |
| `t reflect [days]` | Madeleine's self-improvement report from accumulated data |
| `t research [...]` | Cannoli's external research — see [docs/RESEARCH.md](docs/RESEARCH.md) |
| `t brainstorm "topic"` | Mochi stress-tests an idea — angles, hidden assumptions, the boring alternative |
| `t onboard "need"` | Brioche drafts a new agent persona for a job the crew can't cover |
| `t help` | Print the command list |

`t` skips the ~200ms LLM router step. `tiramisu` is friendlier.

---

## What happens automatically at every `git commit`

After `t hook` in a repo, every commit triggers (in git's hook order):

1. 🐱🍪 **Cookie reviews** the staged diff plus the full content of each changed file (`pre-commit`). She has your engineering principles, code style for the relevant languages, and your learned preferences in her system prompt. She halts on `[BLOCKER]` and prompts to override.
2. 🦡🍫 **Éclair drafts the commit message** from your staged diff (`prepare-commit-msg`), using your last 5 commits as few-shot examples so the voice matches yours.
3. 📊 **Post-commit captures** the final message you actually shipped, so Éclair learns whether her drafts are landing as-is or getting heavily edited.

Skip for one commit: `git commit --no-verify`.

---

## The learning loop

Every meaningful interaction lands in `~/.tiramisu/learnings.db`:

- **Cookie reviews** — passed, blocked-and-aborted, blocked-and-overridden
- **Éclair commit drafts** — paired with what you actually committed (similarity score)
- **Override snippets** — what Cookie flagged that you dismissed (so she calibrates)
- **Preferences** — anything you taught via `t learn`
- **Task plans** — saved Croissant scope sessions
- **Token usage** — every API call, for `t reflect` cost analysis

This data feeds back into every agent's system prompt automatically. After a few weeks:

- Éclair drafts messages in **your** voice
- Cookie stops crying wolf on patterns you've consistently dismissed
- `t reflect` proposes concrete agent-prompt edits grounded in real data, not theory

---

## Steering composition

When an agent runs, its system prompt is composed from these layers in order. Later layers override earlier ones:

1. `agents/<name>.md` — persona
2. `engineering-principles.md` — universal design rules
3. `code-style.md` — **only the language sections relevant to the files in scope** (auto-detected from file extensions)
4. Active preferences from `learnings.db`
5. (Cookie-only) recent override snippets so she stays calibrated
6. **Per-repo overrides** from `<repo>/.tiramisu/*.md` — project-specific rules (see below)

Cookie reviewing a `.py` change sees Python conventions. Cookie reviewing `.java` sees Java conventions. Same agent, different context.

### Per-repo overrides

Drop any `.md` files into a `.tiramisu/` directory at the root of any repo and they get loaded as the **highest-priority** steering layer for that project:

```
my-project/
├── .tiramisu/
│   ├── style.md          # project-specific style rules
│   ├── context.md        # architecture overview, glossary
│   └── preferences.md    # things specific to this codebase
├── .git/
└── src/
```

Examples of what to put there:
- "Money values use `Decimal`, never `float`."
- "All async functions take a `trace_id: str` as the first arg."
- "Skip docstring lints in `legacy/` — that code is deprecated."

The agents will know these rules apply only in that project.

---

## Setup

### Requirements

| Requirement | Why | Get it |
|---|---|---|
| Python 3.10+ (3.14 tested) | Runs every agent script | [python.org](https://www.python.org/downloads/) |
| Git | Hooks, diff plumbing, `gitutil.py` | [git-scm.com](https://git-scm.com/) |
| Anthropic API key | The models behind the crew | [console.anthropic.com](https://console.anthropic.com/) |
| GitHub CLI *(optional)* | Only needed for `t pr --post` inline comments | [cli.github.com](https://cli.github.com/) |

### Install — 2 commands either platform

**Windows:**

```powershell
git clone https://github.com/TcgVanguardTroll/tiramisu.git C:\tiramisu
C:\tiramisu\setup.ps1
```

**macOS / Linux:**

```bash
git clone https://github.com/TcgVanguardTroll/tiramisu.git ~/.local/share/tiramisu
~/.local/share/tiramisu/setup.sh
```

The setup script finds Python 3.10+, installs deps (`anthropic`, `rich`, `prompt_toolkit`, `pypdf`), adds the install dir to PATH, and creates `~/.tiramisu/.env` with a key placeholder. Idempotent — safe to re-run after `git pull` to refresh dependencies.

After it finishes, you'll be told if you need to open a fresh terminal (PATH refresh) or add your `ANTHROPIC_API_KEY` to `~/.tiramisu/.env`. Then verify:

```
t help
tiramisu
```

> **Line-ending note**: `.gitattributes` pins `.bat` files to CRLF (otherwise `cmd.exe` mis-parses them) and the POSIX shims (`t`, `tiramisu`, `*.sh`) to LF (otherwise `/bin/sh` fails on `^M`). Handled automatically on `git clone`.

---

## Advanced features (deep dives in `docs/`)

| Topic | Where |
|---|---|
| **Design** — architecture, data model, and workflow diagrams (Mermaid) | [docs/DESIGN.md](docs/DESIGN.md) |
| **Invariants** — the rules the test suite enforces | [docs/INVARIANTS.md](docs/INVARIANTS.md) |
| **Autonomous research** — anchors + GitHub/HN/arxiv discovery + local-library ingest + PDF auto-split + library scout | [docs/RESEARCH.md](docs/RESEARCH.md) |
| **Terminal UI** — `TIRAMISU_RENDER` modes + animal-themed spinners + REPL keys | [docs/UI.md](docs/UI.md) |
| **Contributing / writing agents** — invariants, persona template, recipes | [docs/DEVELOPING.md](docs/DEVELOPING.md) and [CLAUDE.md](CLAUDE.md) |
| **Running tests** — `pip install -r requirements-dev.txt && pytest tests/` (no API key needed; everything is mocked) | [tests/](tests/) |

---

## Data & privacy

Everything Tiramisu learns stays on your machine — `learnings.db` is plain
SQLite, there is no cloud sync and no telemetry. The only data that leaves
your machine is what gets sent to the Anthropic API to do the work you asked
for: staged diffs and changed-file contents (reviews), file contents the
agents read via tools (implement / chat / scan), and your prompts. Per-repo
`.tiramisu/*.md` overrides and learned preferences ride along inside system
prompts. Nothing is sent anywhere when you aren't running a command.

| Path | What lives there |
|------|------------------|
| `~/.tiramisu/.env` | API key |
| `~/.tiramisu/learnings.db` | Reviews, drafts, preferences, overrides, token usage |
| `~/.tiramisu/.research/` | Findings, candidates, scout reports |
| `~/.tiramisu/library/` | Books / PDFs / docs Cannoli ingests on the weekly background run |
| `~/.tiramisu/sources.json` | Custom watched-source list (overrides defaults) |
| `<repo>/shared_workspace/tasks/` | Croissant's saved scope plans (per-repo) |
| `<repo>/.git/hooks/` | Cookie + Éclair hooks (created by `t hook`) |
| `<repo>/.tiramisu/*.md` | Per-project steering overrides |

---

## A typical day

```bash
# Define scope before touching code
t task "add OAuth refresh flow to settings page"
# Croissant returns acceptance criteria + out-of-scope + breakdown + risks

# Let Éclair implement it (confirms before every file write by default)
t implement "the OAuth refresh flow we just scoped"

# You eyeball her work
git diff

# Commit -- Éclair drafts the message, Cookie reviews automatically
git commit

# Ship it
git push
gh pr create
t pr --post        # Cookie posts inline comments on the GitHub PR
```

Weekly:
```bash
t reflect          # see patterns; get specific preference + prompt-edit proposals
t learn "..."      # teach a new preference based on what you noticed
t research         # see what Cannoli scouted from the world this week
```

---

## Directory structure

```
tiramisu/
├── agents/                  # Persona files for each crew member
├── hooks/                   # cookie_review.py, eclair_commit_msg.py, eclair_post_commit.py
├── scripts/                 # CLI implementations + shared utilities
│   ├── dispatch.py          # `tiramisu` -- natural-language router + REPL
│   ├── implement.py         # `t implement` -- agentic code writer with tool use
│   ├── chat.py              # `t chat` -- conversational mode with read+write+shell
│   ├── scan.py              # `t scan` -- Cookie reads files/dirs in full
│   ├── pr_review.py         # `t pr` -- branch review, --post creates inline PR comments
│   ├── start_task.py        # `t task` -- Croissant scope session
│   ├── reflect.py           # `t reflect` -- Madeleine's insights
│   ├── research.py          # `t research` -- Cannoli's watched sources + CLI
│   ├── research_discovery.py# GitHub / HN / arxiv scouting + paper grab
│   ├── research_library.py  # Library ingestion + PDF auto-split + scout
│   ├── research_common.py   # Shared research config + HTTP plumbing
│   ├── learn.py             # `t learn` -- preference management
│   ├── install_hooks.py     # `t hook`
│   ├── memory.py            # learnings.db layer
│   ├── steering.py          # composition: persona + engineering + code-style + preferences
│   ├── personas.py          # agent emoji + colors
│   ├── spinners.py          # custom rich spinner registrations
│   ├── gitutil.py           # cross-platform git resolution
│   └── llm.py               # Anthropic API client with prompt caching
├── docs/                    # Deep dives for advanced features
│   ├── DESIGN.md            # Architecture + data model + workflow diagrams (Mermaid)
│   ├── INVARIANTS.md        # The rules the test suite enforces
│   ├── RESEARCH.md          # Autonomous research subsystem
│   ├── UI.md                # Render modes + spinners + REPL keys
│   └── DEVELOPING.md        # Contributor / agent-developer pointer
├── tests/                   # 142-test pytest suite (safety, router, steering, memory)
├── .github/workflows/       # CI: 3 OS x 2 Python matrix on every push / PR
├── steering/                # Shared steering docs (composed into every agent prompt)
│   ├── code-style.md        # Per-language style (Java / Python / Rust / TypeScript)
│   ├── engineering-principles.md  # Distilled from Bloch / Martin / Ousterhout / etc.
│   └── communication-style.md     # Tone, commit format, PR description template
├── CLAUDE.md                # Load-bearing architectural invariants for AI agents
├── t.bat / t                # CLI dispatcher (Windows / POSIX)
├── tiramisu.bat / tiramisu  # REPL dispatcher (Windows / POSIX)
├── setup.ps1 / setup.sh     # One-command install (Windows / POSIX)
├── requirements.txt         # anthropic, rich, prompt_toolkit, pypdf
└── .gitattributes           # Pins .bat to CRLF, POSIX shims to LF
```

---

## Design principles

- **CLI-first** — every workflow is a `t <command>` or a `tiramisu` REPL turn. No required IDE plugin.
- **Cross-platform** — Windows uses `.bat` dispatchers, macOS/Linux uses extension-less POSIX shell scripts. Both call the same Python.
- **Local-first** — your data, your preferences, your `learnings.db`. No cloud sync.
- **Composable steering** — agents share the same source of truth (your codified standards) so they agree on what "good" means.
- **Learn before mutate** — `t reflect` and Cannoli's research *propose* changes; you decide whether to apply. Agents don't silently rewrite their own prompts.
- **Surgical** — every change traces to a request. No "while I'm here" cleanup.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell it, ignore it. No warranty.
