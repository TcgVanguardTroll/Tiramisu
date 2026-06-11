# Tiramisu — Design

Architecture, data model, and workflow diagrams for the Tiramisu multi-agent
CLI. The prose version of this document is [CLAUDE.md](../CLAUDE.md) (project
context for AI agents); the rules the test suite enforces are in
[INVARIANTS.md](INVARIANTS.md). This file is the visual map.

---

## System architecture

One shell entry point routes natural language to a single-purpose agent
script. Every script composes its system prompt from the same steering
layers and logs every API call to the same local database.

```mermaid
flowchart TD
    U[User] -->|"tiramisu &lt;anything&gt;"| D[dispatch.py<br/>REPL + exact-word fast path<br/>+ Haiku router]
    U -->|"t &lt;command&gt;"| T[t / t.bat dispatcher]
    D -->|picks one route| T

    T --> TASK["start_task.py · Croissant<br/>scope + acceptance criteria"]
    T --> IMPL["implement.py · Éclair<br/>agentic code writer<br/>+ context trimming"]
    T --> CHAT["chat.py · Tiramisu<br/>conversational tools"]
    T --> SCAN["scan.py / pr_review.py · Cookie<br/>full-read + branch review"]
    T --> REFL["reflect.py · Madeleine<br/>insights from data"]
    T --> RES["research.py · Cannoli<br/>watched sources + CLI<br/>(research_discovery / research_library /<br/>research_common)"]
    T --> LEARN["learn.py<br/>preference management"]

    subgraph SHARED["Shared services (scripts/)"]
        ST[steering.py<br/>prompt composition]
        LLM[llm.py<br/>API client + cost capture]
        MEM[memory.py<br/>SQLite layer]
        GU[gitutil.py · personas.py · spinners.py]
    end

    TASK & IMPL & CHAT & SCAN & REFL & RES & LEARN --> ST
    ST --> LLM
    LLM -->|Messages API<br/>prompt-cached| API[(Anthropic API)]
    LLM -->|usage + cost| MEM
    TASK & SCAN & LEARN --> MEM
    MEM --> DB[("~/.tiramisu/learnings.db")]
```

---

## Routing pipeline

`tiramisu <anything>` resolves to a `t <command>` in up to three steps —
cheapest first, the LLM only as a last resort:

```mermaid
flowchart TD
    IN["user input"] --> EX{exact command word?<br/>(scan / pr / review / …)}
    EX -->|yes| GO([run t &lt;cmd&gt; -- no API call])
    EX -->|no| LLM["FAST_MODEL router<br/>(one word out, temperature 0)"]
    LLM -->|known route| ARG{cmd == scan and a real<br/>path appears in the input?}
    LLM -->|unknown / API error| FB([fall back to t task])
    ARG -->|yes| GOP([run t scan &lt;path&gt;])
    ARG -->|no| GO2([run t &lt;cmd&gt;])
```

Path forwarding is deliberately conservative: only a token that actually
exists on disk is passed through to `t scan` — natural language never
becomes a CLI argument.

---

## Context management — Éclair's loop

A `t implement` run can take up to 50 tool iterations, each adding tool
results (file reads up to 30K chars). `implement.trim_tool_history` keeps
the conversation under a total budget:

- Over budget → the **oldest** tool-result contents are replaced with a
  short elision note in **one batch**; the most recent
  `KEEP_RECENT_TOOL_ROUNDS` rounds are never touched.
- Only the `content` string is replaced — message structure and
  `tool_use_id` pairing stay intact, so the API's tool rules still hold.
- Batching matters for cost: eliding one round per iteration would move
  the first changed byte forward every call and invalidate the prompt
  cache each time; a batch elision invalidates it once per overflow.
- Éclair can always re-read an elided file — the tools are still there.

Pinned by `tests/test_context_trim.py`.

---

## Steering composition

Every agent's system prompt is assembled by `scripts/steering.py` from fixed
layers, in order. Later layers override earlier ones; nothing is inlined or
duplicated across personas (CLAUDE.md §4.2).

```mermaid
flowchart LR
    P["1 · agents/&lt;name&gt;.md<br/>persona"] --> E["2 · engineering-principles.md<br/>universal rules"]
    E --> C["3 · code-style.md<br/>language sections only,<br/>auto-detected from files"]
    C --> PR["4 · preferences<br/>from learnings.db"]
    PR --> O["5 · &lt;repo&gt;/.tiramisu/*.md<br/>per-repo overrides<br/>(highest priority)"]
    O --> SYS([composed system prompt])
```

---

## The commit flow

`t hook` installs three git hooks. Git runs them in this order on every
`git commit` (skip once with `--no-verify`):

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant Git as git commit
    participant Cookie as pre-commit<br/>cookie_review.py
    participant Eclair as prepare-commit-msg<br/>eclair_commit_msg.py
    participant Post as post-commit<br/>eclair_post_commit.py
    participant DB as learnings.db

    Dev->>Git: git commit
    Git->>Cookie: staged diff + full changed files
    Cookie->>DB: log review (passed / blocked / overridden)
    alt [BLOCKER] found
        Cookie-->>Dev: halt, prompt to override
        Dev-->>Cookie: override or abort
    end
    Git->>Eclair: draft message from diff<br/>(last 5 commits as voice examples)
    Eclair-->>Dev: editor opens with draft
    Dev->>Git: edit / accept, commit lands
    Git->>Post: final message
    Post->>DB: pair draft vs final (similarity)
```

---

## Data model — `learnings.db`

SQLite, append-mostly, fail-soft (a locked or corrupt DB never blocks the
actual work — CLAUDE.md §4.5). Schema changes ship as append-only
migrations in `scripts/memory.py` (INVARIANTS.md §7).

```mermaid
erDiagram
    reviews {
        int id PK
        text ts
        text repo_path
        text diff_hash
        text files
        int diff_chars
        text review
        int blockers_found
        text outcome
    }
    overrides {
        int id PK
        text ts
        int review_id FK
        text snippet
        text files
    }
    commit_drafts {
        int id PK
        text ts
        text repo_path
        text files
        text draft
        text final
        real similarity
        int accepted
        int has_blockers
    }
    tasks {
        int id PK
        text ts
        text description
        text plan
        int saved
    }
    preferences {
        int id PK
        text ts
        text text
        text category
        text source
        int active
    }
    token_usage {
        int id PK
        text ts
        text script
        text model
        int input_tokens
        int output_tokens
        int cache_create_tokens
        int cache_read_tokens
        real cost_usd
        text repo_path
    }
    schema_migrations {
        int version PK
        text applied_at
    }

    reviews ||--o{ overrides : "dismissed blockers"
```

---

## The learning loop

Data flows in one direction: interactions land in `learnings.db`, and the
steering layer reads them back into future prompts. Agents never rewrite
their own personas — `t reflect` *proposes* edits, the user applies them by
hand (CLAUDE.md §4.3).

```mermaid
flowchart LR
    A[Cookie reviews<br/>Éclair drafts<br/>overrides · t learn] -->|write| DB[(learnings.db)]
    DB -->|preferences +<br/>override snippets| ST[steering.py]
    ST -->|stronger prompts| AG[every agent]
    DB --> REFL[t reflect]
    REFL -->|proposed edits| Dev([you])
    Dev -->|apply by hand| PERS[agents/*.md]
    PERS --> ST
```

---

## Research subsystem layout

Cannoli's subsystem is four modules with a strict, cycle-free import
hierarchy. `research.py` stays the single CLI entry point (`t research …`).

```mermaid
flowchart BT
    COMMON["research_common.py<br/>shared config + _fetch / _ssl_context<br/>(leaf -- imports no research module)"]
    DISC["research_discovery.py<br/>GitHub / HN / arxiv scouting + grab"] --> COMMON
    LIB["research_library.py<br/>library ingest + PDF split + scout"] --> COMMON
    R["research.py<br/>watched sources + findings + CLI"] --> COMMON
    R --> DISC
    R --> LIB
```

---

## Model tiers

| Tier | Model | Used by | Why |
|---|---|---|---|
| Quality (`DEFAULT_MODEL`) | `claude-sonnet-4-6` | implement, chat, scan, pr, task, reflect, research ingest | Output quality over latency; adaptive thinking enabled on analysis-heavy calls |
| Fast (`FAST_MODEL`) | `claude-haiku-4-5` | router, git hooks, learn classifier, research scans | Hot paths that fire on every commit / keystroke |

Both tiers go through `llm.py`, which marks system prompts cacheable and
logs token usage + estimated cost for every call (CLAUDE.md §4.6).
