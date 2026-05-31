# Tiramisu 🍰

A personal multi-agent dev system. A crew of pastry-named pets that scope your work, write code, review changes, draft commit messages, and learn your preferences over time. Runs as a CLI, integrates with git via hooks. No IDE plugin required.

## The Crew

Every agent is named after a pastry that matches their fur.

| Agent | Pet | Role |
|-------|-----|------|
| 🐕 **Tiramisu** | Red tri mini American Shepherd | Orchestrator — herds the rest |
| 🐾 **Éclair** | Sleek black ferret | SDE — writes code with full codebase access |
| 🐈 **Cookie** | Tortoiseshell cat | Reviewer — judgmental, zero tolerance for sloppiness |
| 🐕 **Croissant** | Corgi | PM — scopes tasks, defines acceptance criteria |
| 🐱 **Madeleine** | Ginger tabby | Knowledge keeper — surfaces patterns from accumulated data |
| 🐶 **Cannoli** | Beagle | Research (planned) |
| 🐰 **Mochi** | White lop rabbit | Brainstorm (planned) |
| 🦮 **Brioche** | Golden retriever | HR — onboards new agents (planned) |

---

## Two entry points: `tiramisu` and `t`

Tiramisu has two CLI surfaces. Use whichever fits the moment.

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

🐕  Tiramisu — interactive mode
    Type a request or question. The right agent will run.
    Built-ins: 'help' for routes, 'exit' / Ctrl+D to leave.

tiramisu > scope adding dark mode
  ->  t task
  [Croissant streams a scope plan]

tiramisu > implement the dark mode toggle
  ->  t implement
  [Éclair writes the code]

tiramisu > exit
```

REPL built-ins: `help`, `exit` / `quit` / `q`, `clear`. Ctrl+C kills a running subcommand but keeps the REPL alive. Ctrl+D exits the REPL.

### `t <command>` — direct invocation

If you already know which agent you want, skip the router:

| Command | What it does |
|---------|--------------|
| `t hook` | Install Cookie + Éclair git hooks in the current repo (one time per repo) |
| `t task "desc"` | Croissant scopes the task — acceptance criteria, out-of-scope, risks |
| `t implement "desc"` | Éclair writes code with full codebase access via tool use |
| `t chat [question]` | Conversational mode — read-only tools, remembers context within the session |
| `t scan [path]` | Cookie reads a file or directory in full and reports issues |
| `t review` | Cookie reviews the currently staged diff |
| `t pr [base]` | Cookie reviews your whole branch vs main |
| `t pr --post` | ...and posts inline comments at exact lines on the GitHub PR |
| `t learn "text"` | Teach the agents a preference (e.g. `t learn "prefer guard clauses"`) |
| `t learn list` | Show all active preferences |
| `t reflect [days]` | Madeleine's self-improvement report from accumulated data |
| `t help` | Print the command list |

`t` skips the ~200ms LLM router step. `tiramisu` is friendlier.

---

## What happens automatically

After `t hook` in a repo, every `git commit` triggers:

1. 🐾 **Éclair drafts the commit message** from your staged diff, using your last 5 commits as few-shot examples so the voice matches yours.
2. 🐈 **Cookie reviews** the diff plus the full content of each changed file. She has your engineering principles, code style for the relevant languages, and your learned preferences in her system prompt. She halts on `[BLOCKER]` and prompts to override.
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

This data feeds back into every agent's system prompt automatically. After a few weeks:

- Éclair drafts messages in **your** voice
- Cookie stops crying wolf on patterns you've consistently dismissed
- `t reflect` proposes concrete agent-prompt edits grounded in real data, not theory

---

## Steering composition

When an agent runs, its system prompt is composed from:

1. `agents/<name>.md` — persona
2. `engineering-principles.md` — universal design rules
3. `code-style.md` — **only the language sections relevant to the files in scope** (auto-detected from file extensions)
4. Active preferences from `learnings.db`
5. (Cookie-only) recent override snippets so she stays calibrated

Cookie reviewing a `.py` change sees Python conventions. Cookie reviewing `.java` sees Java conventions. Same agent, different context.

---

## Setup

### Prerequisites

- Python 3.10+ (3.14 tested)
- Git + [GitHub CLI](https://cli.github.com/) — the CLI is only required for `t pr --post`
- An [Anthropic API key](https://console.anthropic.com/)

### Install (Windows)

```powershell
# 1. Clone wherever you want it to live
git clone https://github.com/TcgVanguardTroll/tiramisu.git C:\tiramisu

# 2. Install Python dependencies
C:\Python314\python.exe -m pip install -r C:\tiramisu\requirements.txt

# 3. Add to user PATH
$p = [System.Environment]::GetEnvironmentVariable("PATH", "User")
[System.Environment]::SetEnvironmentVariable("PATH", "$p;C:\tiramisu", "User")

# 4. Configure your API key
New-Item -ItemType Directory -Force "$HOME\.tiramisu" | Out-Null
"ANTHROPIC_API_KEY=sk-ant-your-key-here" | Out-File -Encoding utf8 "$HOME\.tiramisu\.env"
```

Open a fresh terminal, then verify:
```powershell
t help
tiramisu
```

Windows uses `t.bat` / `tiramisu.bat`. PATHEXT picks them up automatically when you type `t` or `tiramisu`.

### Install (macOS / Linux)

```bash
# 1. Clone wherever you want it to live
git clone https://github.com/TcgVanguardTroll/tiramisu.git ~/.local/share/tiramisu

# 2. Install Python deps
pip3 install -r ~/.local/share/tiramisu/requirements.txt

# 3. Add to PATH (adjust shell as needed)
echo 'export PATH="$HOME/.local/share/tiramisu:$PATH"' >> ~/.bashrc   # or ~/.zshrc

# 4. Configure your API key
mkdir -p ~/.tiramisu
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > ~/.tiramisu/.env
```

Open a fresh terminal, then verify:
```bash
t help
tiramisu
```

POSIX uses the extension-less `t` and `tiramisu` shell scripts, which exec the same Python entry points the `.bat` files do.

> **Cross-platform note**: line endings on the shell scripts are pinned to LF in `.gitattributes` (otherwise Windows checkouts of POSIX shims would break), and `.bat` files are pinned to CRLF (otherwise `cmd.exe` mis-parses them).

---

## Per-user data

| Path | What lives there |
|------|------------------|
| `~/.tiramisu/.env` | API key |
| `~/.tiramisu/learnings.db` | Reviews, drafts, preferences, overrides |
| `<repo>/shared_workspace/tasks/` | Croissant's saved scope plans (per-repo) |
| `<repo>/.git/hooks/` | Cookie + Éclair hooks (created by `t hook`) |

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
```

---

## Directory structure

```
tiramisu/
├── agents/                  # Persona files for each crew member
├── hooks/                   # cookie_review.py, eclair_commit_msg.py, eclair_post_commit.py
├── scripts/                 # CLI implementations + shared utilities
│   ├── dispatch.py          # tiramisu — natural-language router + REPL
│   ├── implement.py         # t implement -- agentic code writer with tool use
│   ├── scan.py              # t scan -- Cookie reads files/dirs in full
│   ├── pr_review.py         # t pr -- branch review, --post creates inline PR comments
│   ├── start_task.py        # t task -- Croissant scope session
│   ├── reflect.py           # t reflect -- Madeleine's insights
│   ├── learn.py             # t learn -- preference management
│   ├── install_hooks.py     # t hook
│   ├── memory.py            # learnings.db layer (read/write helpers)
│   ├── steering.py          # composes persona + engineering + code-style + preferences
│   └── llm.py               # Anthropic API client with prompt caching
├── code-style.md            # Per-language style: Java, Python, Rust, TypeScript
├── engineering-principles.md# Distilled from Bloch / Martin / Ousterhout / Kleppmann / Nygard
├── communication-style.md   # Tone, commit format, PR description template
├── t.bat                    # Windows dispatcher for `t <cmd>`
├── tiramisu.bat             # Windows dispatcher for `tiramisu` (REPL + router)
├── t                        # POSIX dispatcher for `t <cmd>`
├── tiramisu                 # POSIX dispatcher for `tiramisu` (REPL + router)
├── requirements.txt         # anthropic, etc.
└── .gitattributes           # Pins .bat to CRLF, POSIX shims to LF
```

---

## Design principles

- **CLI-first** — every workflow is a `t <command>` or a `tiramisu` REPL turn. No required IDE plugin.
- **Cross-platform** — Windows uses `.bat` dispatchers, macOS/Linux uses extension-less POSIX shell scripts. Both call the same Python.
- **Local-first** — your data, your preferences, your `learnings.db`. No cloud sync.
- **Composable steering** — agents share the same source of truth (your codified standards) so they agree on what "good" means.
- **Learn before mutate** — `t reflect` *proposes* changes; you decide whether to apply. Agents don't silently rewrite their own prompts.
- **Surgical** — every change traces to a request. No "while I'm here" cleanup.
