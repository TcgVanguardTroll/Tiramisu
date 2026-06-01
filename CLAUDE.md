# Tiramisu — Project context for AI agents

> This file is the canonical context document for **any AI agent working on
> the Tiramisu repository itself** — Claude Code, Cursor, Aider, or
> Tiramisu's own crew. Read this before making changes. The conventions
> below are not suggestions; they are how this codebase stays coherent
> across many small commits.

---

## 1. What Tiramisu is

A personal multi-agent CLI for AI-assisted software development. A crew of
pastry-themed agents (Cookie reviews, Éclair writes, Croissant scopes,
Madeleine reflects) coordinate through a single shell entry point (`tiramisu`)
and a set of git hooks. Each agent is a focused persona with a narrow,
explicit role. Together they form an opinionated workflow — scope → write
→ review → learn → reflect.

It is deliberately **not**:
- A monolithic agent that does everything
- A SaaS / hosted service
- An IDE plugin (no IDE required)
- A team / collaboration tool (single-user, local-first)
- A "memory" or RAG system (we use SQLite for structured signals, not vector search)

---

## 2. Architecture at a glance

```
User types `tiramisu …`
      |
      v
+----------------+    routes to    +---------------------------+
|  dispatch.py   |--------------->|  one of: t task / implement |
|  (REPL +       |                |    scan / review / pr /     |
|   router via   |                |    learn / reflect / chat   |
|   Haiku)       |                +-------------+---------------+
+----------------+                              |
                                                v
                                  +-----------------------------+
                                  |  scripts/<thing>.py or      |
                                  |  hooks/cookie_review.py     |
                                  +--------------+--------------+
                                                 |
                              uses scripts/      v        writes to
                              ----------------------------------> learnings.db
                              llm.py        Anthropic API       (SQLite)
                              steering.py   composed system prompt
                              memory.py     read/write to learnings.db
                              personas.py   emoji + colors per agent
                              gitutil.py    git resolution
                              spinners.py   themed wait indicators
```

The system has three layers:
- **CLI layer** — `t.bat` / `tiramisu.bat` / POSIX shims dispatch to Python scripts
- **Agent layer** — each script is one agent's job (Cookie reviews, Éclair writes, …)
- **Shared services** — `llm.py`, `steering.py`, `memory.py`, `personas.py`, `gitutil.py`, `spinners.py`

---

## 3. Where things live

```
tiramisu/
├── agents/                       Persona files (system prompts)
│   ├── cookie.md                 reviewer
│   ├── croissant.md              scope planner
│   ├── eclair.md                 implementer
│   ├── eclair-standards.md       Éclair-specific coding standards
│   ├── madeleine.md              reflection / insights keeper
│   ├── tiramisu.md               orchestrator (the conversational layer)
│   ├── brioche.md cannoli.md mochi.md   planned agents
│   └── README.md                 meta: how to write a Tiramisu persona
├── hooks/                        Git hooks installed by `t hook`
│   ├── cookie_review.py          pre-commit
│   ├── eclair_commit_msg.py      prepare-commit-msg
│   └── eclair_post_commit.py     post-commit (captures final message)
├── scripts/                      CLI implementations + shared utilities
│   ├── dispatch.py               `tiramisu` REPL + router (one entry point)
│   ├── implement.py              `t implement` — agentic code writer
│   ├── chat.py                   `t chat` — conversational, read-only tools
│   ├── scan.py                   `t scan` — Cookie reads files in full
│   ├── pr_review.py              `t pr` — branch review + `--post` inline comments
│   ├── start_task.py             `t task` — Croissant scope session
│   ├── reflect.py                `t reflect` — Madeleine's insights
│   ├── learn.py                  `t learn` — preference management
│   ├── install_hooks.py          `t hook` — install hooks in a repo
│   ├── memory.py                 SQLite layer (read/write learnings.db)
│   ├── steering.py               System-prompt composition
│   ├── personas.py               Emoji + color per agent
│   ├── llm.py                    Anthropic API client + token tracking
│   ├── gitutil.py                Cross-platform git resolution
│   └── spinners.py               Themed wait indicators
├── docs/                         Deep-dive docs for advanced features
│   ├── RESEARCH.md               Cannoli's research subsystem
│   ├── UI.md                     Render modes, spinners, REPL keys
│   └── DEVELOPING.md             Pointer for contributors / AI agents working on this repo
├── code-style.md                 Per-language style rules (Java/Python/Rust/TS)
├── engineering-principles.md     Universal design rules (Bloch/Martin/Ousterhout/etc.)
├── communication-style.md        Tone, commit format, PR template
├── README.md                     User-facing entry point
├── CLAUDE.md                     This file
├── t.bat / tiramisu.bat          Windows dispatchers (CRLF, pinned)
├── t / tiramisu                  POSIX dispatchers (LF, 100755)
├── setup.ps1 / setup.sh          One-command install
├── requirements.txt              anthropic, rich, prompt_toolkit
└── .gitattributes                Locks line endings per file type
```

---

## 4. Core invariants (do not break these)

### 4.1 — Persona files are persona-only
Files under `agents/*.md` define **who the agent is**. They MUST NOT contain
tool instructions, code, or implementation details. Behavior lives in
`scripts/` and `hooks/`. If you find yourself adding "to use the database,
run …" to a persona, stop — that goes in the script.

### 4.2 — Composition over duplication
Every agent's system prompt is composed by `scripts/steering.py`:
`persona + engineering-principles + filtered code-style + preferences + per-repo overrides`.
DO NOT inline engineering principles into individual personas. If a rule
applies to all agents, put it in `engineering-principles.md`. If it applies
only to Cookie, put it in `cookie.md` voice section.

### 4.3 — Learn before mutate
Agents must NEVER auto-rewrite their own prompts. `t reflect` **proposes**
edits; the user applies them by hand. Anything that silently changes agent
behavior based on accumulated data is a bug, not a feature.

### 4.4 — One agent per script, narrow scope
Each script in `scripts/` corresponds to exactly one agent's job. Don't
make `implement.py` also do reviews. Don't make `cookie_review.py` also
suggest fixes (it can mention them, but writing code is Éclair's job).

### 4.5 — Fail-soft on learning
`memory.py` writes are wrapped in `@_safe`. Logging is never allowed to
break the actual work. If `learnings.db` is locked or corrupted, the agent
must still complete its task.

### 4.6 — Token usage capture is mandatory
Every API call goes through `llm.py`'s `invoke` / `invoke_stream` /
`invoke_stream_markdown`. Each of these calls `_log_api_usage()` so token
costs land in `token_usage`. Do not bypass with a raw
`client.messages.create(...)` in a new script — you'll lose cost data.

### 4.7 — Git invocations go through gitutil
Any `subprocess.run(["git", ...])` in new code must instead call
`gitutil.run_git(...)` (or use `gitutil.git_exe()`). Windows Python
subprocess often can't find git on PATH the way the shell does — gitutil
falls back to known install locations.

### 4.8 — Line endings: pinned per file type
`.gitattributes` enforces:
- `.bat` / `.cmd` → CRLF (otherwise cmd.exe mis-parses GOTO labels)
- `t` / `tiramisu` / `*.sh` → LF (otherwise POSIX shells reject `^M`)
Do not override these.

### 4.9 — Cross-platform: every change should work on both
Windows is primary, macOS/Linux is supported. New CLI commands need
matching entries in **both** `t.bat` (and `tiramisu.bat`) **and** the POSIX
`t` / `tiramisu` shell scripts. Symptoms of forgetting: command works on
one OS, errors on the other.

---

## 5. Common operations (recipes)

### Add a new `t <command>`
1. Write `scripts/your_command.py` — single-purpose, uses `load_steering`.
2. Add a case to `t.bat` (Windows) **and** `t` (POSIX). Match the existing pattern.
3. If it should be routable from `tiramisu`'s natural-language entry, add the command name to `ROUTES` in `scripts/dispatch.py` and add 1-2 example phrasings in the router prompt.
4. Update the README CLI table and `t help` text in both dispatchers.

### Add a new agent
1. Write `agents/<name>.md` following the pattern in existing personas.
2. Add to `scripts/personas.py` — pet + pastry emoji + color.
3. Add the row to the README crew table.
4. If the agent has a CLI surface, follow "Add a new `t <command>`" above.
5. Mark as "planned — not yet wired into the t CLI" if you haven't built the CLI yet (see `cannoli.md`).

### Add a new steering layer
1. Decide what scope it has: universal, per-language, or agent-specific.
2. Edit the appropriate file: `engineering-principles.md` for universal,
   `code-style.md` for per-language, the persona file for agent-specific.
3. Do not create new top-level steering files without coordinating with
   `scripts/steering.py` — it loads a fixed set.

### Add a tool to an agent that has tool use (Éclair / Tiramisu chat)
1. Add the tool schema dict to the script's `TOOLS` list.
2. Add the handler in `execute_tool()`.
3. Confirmation gating for any write/shell tools follows the existing
   `state.auto_writes` / `state.confirm()` pattern in `implement.py`.

---

## 6. What to push back on

If asked to do any of these, raise it before implementing:

- **Auto-mutating agent prompts.** Violates §4.3.
- **Adding a "do everything" agent.** Violates §4.4. Pick a specific role or refactor an existing one.
- **Cloud / hosted features.** Violates the local-first design.
- **A team / multi-user mode.** Out of scope; this is a single-developer tool.
- **Replacing SQLite with a vector store** for `learnings.db`. The data is structured (reviews, drafts, overrides). Vectors don't fit.
- **A monolithic config file** instead of `.tiramisu/<files>.md`. Markdown files in a directory is the pattern — keep it.
- **Removing the persona / pet theme** because it "feels unprofessional." The personality is load-bearing for memorability and role differentiation.

---

## 7. Code style at a glance

(Full version in `code-style.md` and `engineering-principles.md`. Highlights:)

### Python — most of this codebase
- Type-annotate public function signatures
- Use `X | None` over `Optional[X]` (3.10+)
- Prefer early returns / guard clauses over nested if-else
- No bare `except:` — always catch specific exceptions, log if you'd otherwise swallow
- Functions max ~40 lines; split when longer
- Constants at module top, never magic numbers inline
- `pathlib.Path` over `os.path`
- One assertion per test where possible

### Universal
- Surgical changes only — every line traces to the requirement
- No "while I'm here" cleanup in unrelated code
- Working code over lengthy explanations
- Match the codebase's existing patterns when adding new code

---

## 8. Testing posture

Tiramisu currently has **no automated test suite** (this is a known gap).
When you add tests:
- Put them in `tests/` (mirror the source layout: `tests/test_personas.py`)
- Use plain `assert` statements; no test framework needed yet
- Prefer fast, deterministic unit tests on pure functions (`steering._parse_h2_sections`, `personas.pair`, `dispatch.route` with a mock client)
- Avoid tests that hit the real API. Mock `llm.invoke` / `invoke_stream`.

---

## 9. Commit hygiene

- Conventional commit format: `type(scope): subject`
- Imperative subject under 72 chars, no trailing period
- Body explains the **why**, not the diff
- Include `Co-Authored-By:` for AI-assisted commits — be honest, don't hide it
- One logical change per commit; don't bundle unrelated cleanup

---

## 10. When you're not sure

Read three things, in this order:
1. The user's request (literal)
2. This file (project context)
3. The relevant persona file in `agents/`

If the request conflicts with the invariants in §4 or the "push back" list
in §6, surface the tension before acting. Don't silently choose for the
user.
