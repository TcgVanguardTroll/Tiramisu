# Tiramisu 🍰

A personal multi-agent dev system. A crew of pastry-named pets that scope your work, write code, review changes, draft commit messages, and learn your preferences over time. Runs as a CLI, integrates with git via hooks. No IDE plugin required.

## The Crew

Every agent is named after a pastry that matches their fur.

All dogs share 🐶, all cats share 🐱. Each agent's pastry is unique — that's the visual differentiator. Source of truth: `scripts/personas.py`.

| Agent | Sigil | Pet | Role |
|-------|:-----:|-----|------|
| **Tiramisu** | 🐶🍮 | Red tri mini American Shepherd | Orchestrator — herds the rest |
| **Éclair** | 🦡🍫 | Sleek black ferret (mustelid kin: badger) | SDE — writes code with full codebase access |
| **Cookie** | 🐱🍪 | Tortoiseshell cat | Reviewer — judgmental, zero tolerance for sloppiness |
| **Croissant** | 🐶🥐 | Corgi | PM — scopes tasks, defines acceptance criteria |
| **Madeleine** | 🐱🧁 | Ginger tabby | Knowledge keeper — surfaces patterns from accumulated data |
| **Cannoli** | 🐶🍩 | Beagle | Research (planned) |
| **Mochi** | 🐰🍡 | White lop rabbit | Brainstorm (planned) |
| **Brioche** | 🐶🍞 | Golden retriever | HR — onboards new agents (planned) |

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
| `t research` | Show Cannoli's latest external-source findings (auto-runs weekly) |
| `t help` | Print the command list |

`t` skips the ~200ms LLM router step. `tiramisu` is friendlier.

---

## What happens automatically

After `t hook` in a repo, every `git commit` triggers:

1. 🦡🍫 **Éclair drafts the commit message** from your staged diff, using your last 5 commits as few-shot examples so the voice matches yours.
2. 🐱🍪 **Cookie reviews** the diff plus the full content of each changed file. She has your engineering principles, code style for the relevant languages, and your learned preferences in her system prompt. She halts on `[BLOCKER]` and prompts to override.
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

## Autonomous external research

Tiramisu watches the world for you. Every time you run `tiramisu`, **Cannoli** checks whether her last scan of external sources was more than 7 days ago. If it was, she spawns a detached background process that:

1. Fetches Anthropic API release notes, the Anthropic Cookbook, aider docs, and Python release notes
2. Diffs each source against the last cached copy (only **new** content is summarized — no full re-summaries)
3. Asks Haiku to produce a markdown findings file with proposed steering edits ranked 1–5 for relevance
4. Writes the file to `~/.tiramisu/.research/findings_YYYY-MM-DD.md`

Next time you run `tiramisu`, you see a one-line notice:

```
🐶 Cannoli has 1 new finding(s). Run `t research` to see them, or `t research mute` to skip.
```

Run `t research` to view the findings (rendered as Markdown), which marks them as read. Or `t research mute` to ignore this week.

**Critical safety property:** Findings are *proposed*, never auto-applied. The user copies any worthwhile suggestions into the steering files by hand. This is the autonomous version of the "learn before mutate" rule in `CLAUDE.md` §4.3.

| `t research` action | What it does |
|---|---|
| `t research` (no args) | Show + mark-read the newest unread file (findings or candidates) |
| `t research run` | Scan watched sources now for new deltas |
| `t research discover` | Scout GitHub Trending + HackerNews for new candidate sources |
| `t research all` | Run + discover in one shot |
| `t research mute` | Mark everything pending as read without showing |
| `t research list` | Chronological history of findings + candidates |
| `t research sources list` | Show active sources for this directory |
| `t research sources add <url> [name] [focus]` | Add a source to your user config |
| `t research sources remove <name-or-url>` | Drop a source |
| `t research sources reset` | Write defaults to `~/.tiramisu/sources.json` to edit |
| `t research sources show` | Dump the raw JSON config |

### Source configuration — three layers

The active source list follows the same per-user / per-repo pattern as everything else:

| Layer | File | Behavior |
|---|---|---|
| **Defaults** | hardcoded in `scripts/research.py` | Always available; used if no user file exists |
| **User** | `~/.tiramisu/sources.json` | **Replaces** defaults if present |
| **Per-repo** | `<repo>/.tiramisu/sources.json` | **Adds** to whichever base is active |

Example user file:

```json
[
  {
    "name": "Anthropic API release notes",
    "url": "https://docs.anthropic.com/en/release-notes/api",
    "focus": "Models, pricing, new SDK features"
  },
  {
    "name": "My team's design-doc repo",
    "url": "https://raw.githubusercontent.com/my-org/design-docs/main/README.md",
    "focus": "New patterns we should know about"
  }
]
```

You can add per-project sources too — e.g., put a `sources.json` in a project's `.tiramisu/` directory pointing at that project's docs, and Cannoli will watch those whenever you run `tiramisu` from inside that repo.

### Two layers — anchors + discovery

Cannoli watches the world in two ways:

| Layer | What it does | Output | Cost / week |
|---|---|---|---|
| **Anchors** (watched sources) | Diffs the sources you've curated in `sources.json` | `findings_YYYY-MM-DD.md` | ~$0.04 |
| **Discovery** (scouting) | Pulls trending repos from GitHub topics (`claude`, `ai-agents`, `anthropic`) and top HackerNews stories matching `claude anthropic` / `ai code review` / `ai dev tools`. Summarizes each as a *candidate source* you can add to your watched list. | `candidates_YYYY-MM-DD.md` | ~$0.10 |

Both run automatically when you next launch `tiramisu` after >7 days — the dispatcher kicks off `t research all` as a detached background process. The pending notice combines both:

```
🐶 Cannoli has 2 new finding(s) and 3 candidate(s) waiting.
   Run `t research` to see them.
```

Discovery uses **only free public APIs** (GitHub Search API + HN Algolia API). No API keys, no rate-limit headaches at this scale. Total weekly cost: **~$0.14**.

When a candidate looks promising, copy the `Add command` from the candidates file:

```
**Add command:** `t research sources add https://example.com "Some Repo" "Why it matters"`
```

That graduates it from a one-off candidate to a permanently-watched anchor source — and you'll see deltas in `findings_*.md` going forward.

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

The agents will know these rules apply only in that project. Add Tiramisu's `.tiramisu/.repl_history` and similar to your `.gitignore` if you don't want them committed (only `.md` files in `.tiramisu/` are loaded as steering).

---

## Setup

### Prerequisites

- Python 3.10+ (3.14 tested)
- Git + [GitHub CLI](https://cli.github.com/) — the CLI is only required for `t pr --post`
- An [Anthropic API key](https://console.anthropic.com/)

### Install — 2 commands either platform

#### Windows

```powershell
git clone https://github.com/TcgVanguardTroll/tiramisu.git C:\tiramisu
C:\tiramisu\setup.ps1
```

#### macOS / Linux

```bash
git clone https://github.com/TcgVanguardTroll/tiramisu.git ~/.local/share/tiramisu
~/.local/share/tiramisu/setup.sh
```

The setup script:
1. Finds Python 3.10+
2. Installs all deps (`anthropic`, `rich`, `prompt_toolkit`)
3. Adds the install dir to your user PATH
4. Creates `~/.tiramisu/.env` with a key placeholder

It's idempotent — safe to re-run after `git pull` to refresh dependencies.

After the script finishes, you'll be told if you need to:
- Open a fresh terminal (so PATH picks up)
- Add your `ANTHROPIC_API_KEY` to `~/.tiramisu/.env`

Then verify:
```
t help
tiramisu
```

> **Line-ending note**: `.gitattributes` pins `.bat` files to CRLF (otherwise `cmd.exe` mis-parses them) and the POSIX shims (`t`, `tiramisu`, `*.sh`) to LF (otherwise `/bin/sh` fails on `^M`). This is automatic on `git clone` — you don't need to think about it.

---

## Output rendering modes

Cookie's reviews, Croissant's plans, and Madeleine's reports can render in three ways via the `TIRAMISU_RENDER` env var:

| `TIRAMISU_RENDER` | Behavior |
|---|---|
| `both` *(default)* | Stream raw text live, then print a rendered Markdown view below a divider. Two-phase: best of both — real-time feedback plus a polished final view. |
| `stream` | Stream raw text only. No rendered view. Cleanest for piping output to files or grepping. |
| `rendered` | Silent buffer with a thinking-spinner during the API call, then print the rendered Markdown only. No raw text shown. |

Set per session:
```powershell
$env:TIRAMISU_RENDER = "rendered"
t scan
```

Or permanently in your PowerShell profile:
```powershell
# add to $PROFILE
$env:TIRAMISU_RENDER = "rendered"
```

POSIX:
```bash
# add to ~/.bashrc or ~/.zshrc
export TIRAMISU_RENDER=rendered
```

`TIRAMISU_NO_RENDER=1` is kept as a deprecated alias for `stream`.

### Spinner themes

The "thinking…" indicator (visible during router decisions and in `rendered` mode) has a few animal-themed variants. Set via `TIRAMISU_SPINNER`:

| Value | Looks like |
|---|---|
| `paws` *(default)* | 🐾 walking paw prints with a fading trail |
| `chase` | 🐶 puppy running across the line |
| `pastries` | 🍮 🥐 🍪 🧁 🍩 🍡 🍞 🍫 rotating |
| `naptime` | 🐱 cat sleeping, zzz building |
| `sniff` | 🐶 puppy sniffing left-to-right and back |
| any rich built-in | `dots`, `dots2`, `line`, `arrow`, etc. — passes through |

```powershell
$env:TIRAMISU_SPINNER = "pastries"
tiramisu look at my code
```

Set permanently the same way as `TIRAMISU_RENDER` above.

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

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell it, ignore it. No warranty.

## Design principles

- **CLI-first** — every workflow is a `t <command>` or a `tiramisu` REPL turn. No required IDE plugin.
- **Cross-platform** — Windows uses `.bat` dispatchers, macOS/Linux uses extension-less POSIX shell scripts. Both call the same Python.
- **Local-first** — your data, your preferences, your `learnings.db`. No cloud sync.
- **Composable steering** — agents share the same source of truth (your codified standards) so they agree on what "good" means.
- **Learn before mutate** — `t reflect` *proposes* changes; you decide whether to apply. Agents don't silently rewrite their own prompts.
- **Surgical** — every change traces to a request. No "while I'm here" cleanup.
