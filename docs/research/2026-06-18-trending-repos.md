# Cannoli research report — improving Tiramisu from GitHub trending

**Date:** 2026-06-18
**Method:** Cannoli discovery query (topics `claude`, `ai-agents`, `anthropic`,
pushed in the last 7 days, sorted by stars), then READMEs of the most
Tiramisu-adjacent repos read in full. Every idea below is filtered through
the invariants in [CLAUDE.md](../../CLAUDE.md) §4 and §6 — several popular
mechanisms are *deliberate non-choices* for this project and are listed as
such, not as a backlog.

**Nothing here is applied.** This is a proposal document for the maintainer
to act on selectively — the code analogue of a `candidates_*.md` findings
file.

---

## 1. What the trend looks like right now

The top of the `claude` / `ai-agents` / `anthropic` topics is dominated by
**agent harnesses and memory layers** — tools that wrap a coding agent
(Claude Code, Cursor, Codex…) with skills, persisted context, security
gating, and self-learning. That is exactly Tiramisu's neighborhood, so the
signal is high. Representative repos scouted:

| Repo | What it is | Relevance to Tiramisu |
|---|---|---|
| `DietrichGebert/ponytail` | YAGNI "lazy senior dev" steering ruleset (~27.6k★) | **5/5** — directly adoptable as steering |
| `affaan-m/ECC` | "Agent harness OS": skills, instincts, security, research-first | **5/5** — closest analogue |
| `thedotmack/claude-mem` | Persistent cross-session memory, FTS + vector | **4/5** — memory/learnings overlap |
| `bytedance/deer-flow` | "Long-horizon SuperAgent harness" on LangGraph (~25k★) | 3/5 — memory/dedup ideas; mostly a non-choice |
| `NousResearch/hermes-agent` | "The agent that grows with you" | 3/5 — self-improvement framing |
| `JuliusBrussee/caveman` | Skill that cuts ~65% of tokens via terse prompts | 2/5 — token-cost adjacent |
| `langchain-ai/langchain` | Agent *framework* | 2/5 — Tiramisu is deliberately not a framework |
| `firecrawl`, `gemini-cli`, `AutoGPT`, `prompts.chat` | scrape / other-vendor CLI / autonomous / prompt list | 1–2/5 — tangential |

> Caveat: raw star counts from the search API looked inflated; the table is
> ranked by *relevance to Tiramisu*, not popularity.

---

## 2. Detailed findings on the two closest repos

### 2.1 `affaan-m/ECC` — agent harness "OS"

**What it does (concrete mechanisms):**

- **Skills system** — YAML-frontmatter markdown files in `skills/`,
  auto-suggested or invoked directly. 271+ of them spanning coding
  standards and domain workflows.
- **AgentShield** — a security scan in the commit path: secrets detection
  (14 patterns), permission auditing, hook-injection analysis, MCP-server
  risk profiling. Exit codes fail builds on critical findings; an
  attacker/defender/auditor three-agent pipeline runs under `--opus`.
- **Instincts / Continuous Learning v2** — extracts behavioral patterns
  into reusable "instincts" with **confidence scoring**, clusters related
  ones, and promotes high-confidence instincts into permanent skills.
- **Hook events (15+)** — SessionStart, beforeShellExecution, afterFileEdit,
  beforeMCPExecution, beforeSubmitPrompt, etc.
- **Model routing + cost budgets**, **git-worktree lifecycle**,
  **evaluation loops** (checkpoint vs continuous, grader types, pass@k).

**What Tiramisu already matches or beats:** model routing (Haiku router),
cost budgets (`--budget`), token tracking (`token_usage`), git hooks
(pre-commit / prepare-commit-msg / post-commit). No action needed.

**What's genuinely borrowable:** the *security-scan-in-review* idea and the
*confidence-scored learning* idea (see §3).

### 2.2 `thedotmack/claude-mem` — persistent memory

**What it does (concrete mechanisms):**

- Captures tool-usage observations via lifecycle hooks; **compresses** them
  into semantic summaries rather than storing raw transcripts.
- **Storage:** SQLite + **FTS5 full-text search** for keyword matching,
  *plus* Chroma vector DB for semantic search.
- **Progressive disclosure** — layered retrieval (`search` → `timeline` →
  `get_observations`) with **token-cost visibility** so you don't pull more
  context than you need.
- **`<private>` tags** — content wrapped in `<private>` is excluded from
  storage.
- Worker service + web UI on `localhost:37777`.

**What's genuinely borrowable:** FTS5 search over `learnings.db`, and
`<private>` redaction (see §3). The vector half and the web UI are
non-choices (see §4).

### 2.3 `DietrichGebert/ponytail` — YAGNI steering ruleset *(strongest fit)*

**What it does:** packages a single, sharp engineering discipline — "think
like the laziest senior dev in the room; the best code is the code you never
wrote" — as an always-on steering ruleset. Before writing code, the agent
walks a **decision ladder**:

> 1. Does this need to exist?  → no: skip it (YAGNI)
> 2. Stdlib does it?           → use it
> 3. Native platform feature?  → use it
> 4. Installed dependency?     → use it
> 5. One line?                 → one line
> 6. Only then: the minimum that works

And a **non-negotiable guard** — "Lazy, not negligent": trust-boundary
validation, data-loss handling, security, and accessibility are *never* cut.
Claimed effect: 80–94% less code, 42–75% less cost, 3–6× faster.

**Why it's the strongest fit of anything scouted:** it is *not a framework* —
it's a steering doc. It even ships in `.kiro/steering/` and `AGENTS.md`
formats and exposes mode switches (`/ponytail lite|full|ultra|off`). That is
**exactly** Tiramisu's model: a composed steering layer with toggles. It
sharpens the rule already in `engineering-principles.md` ("surgical changes
only — every line traces to the requirement") into an actionable ladder. It
slots in with zero invariant conflict and is precisely what `t research
apply` was built to adopt.

### 2.4 `bytedance/deer-flow` — SuperAgent harness (mostly a non-choice)

**What it does:** a long-horizon agent built on **LangGraph/LangChain** with
a filesystem, sandbox execution, progressively-loaded skills, dynamic
sub-agent spawning (scoped context, parallel, token usage attributed back),
an HTTP gateway, and a web frontend. Persistent memory of "profile,
preferences, accumulated knowledge," managed aggressively (summarize
finished sub-tasks, offload intermediate results to the filesystem).

**Mostly a non-choice for Tiramisu:** the framework core (LangGraph),
HTTP gateway, web UI, Docker sandbox, and progressive "skills" all conflict
with the local-first, CLI-only, not-a-framework, one-agent-one-job design
(§4.4, §6). One concrete, in-scope idea is worth lifting, though:
**memory dedup** — DeerFlow "skip[s] duplicate fact entries at apply time,
so repeated preferences and context do not accumulate endlessly." Tiramisu's
`learnings.db` preferences can grow stale/duplicative; dedup-on-write is a
small, on-brand hardening (see P0b below).

---

## 3. Proposed improvement backlog (in-scope, prioritized)

Each item lists source, the change, why it fits Tiramisu's design, the
invariant check, and rough effort.

### P0 — Adopt a ponytail-style YAGNI decision ladder  *(highest value / lowest risk)*
- **Source:** `DietrichGebert/ponytail`.
- **Change:** add the decision ladder + "lazy, not negligent" guard to
  `steering/engineering-principles.md` (or land it as a `steering/learned/`
  doc via `t research apply` — this is the canonical adoption path). Optional:
  a composition toggle mirroring ponytail's lite/full modes.
- **Why it fits:** it's a steering doc, the core Tiramisu pattern — no new
  code, no new subsystem, no invariant tension. Sharpens the "surgical
  changes only" rule that already exists. Most-distinctive, highest-leverage
  change available, and it makes every agent (especially Éclair) write less.
- **Invariant check:** §4.2 composition-over-duplication (put it in the
  shared steering doc, not each persona) ✅ · §4.3 user-gated adoption ✅.
- **Effort:** tiny. A steering-doc edit + (if toggled) a `steering.py` flag
  and an order test.

### P0b — Preference dedup on write
- **Source:** `bytedance/deer-flow` (skip duplicate facts at apply time).
- **Change:** in `memory.py`, dedup preferences/learnings on write so
  repeated signals don't accumulate endlessly and bloat every prompt.
- **Why it fits:** keeps the composed steering lean; pairs with P1/P2.
- **Invariant check:** §4.5 fail-soft ✅ · §7 (no schema change needed —
  pure write-path guard, or a uniqueness index migration if preferred) ✅.
- **Effort:** small.

### P1 — Searchable learnings via SQLite FTS5
- **Source:** claude-mem's FTS5 layer.
- **Change:** a `t learn search <query>` (and/or `t reflect search`) backed
  by an FTS5 virtual table over the existing reviews / drafts / preferences
  in `learnings.db`. Keyword + phrase search, ranked by relevance.
- **Why it fits:** §6 says the data is *structured* and SQLite is the right
  store — FTS5 is the textbook way to make structured SQLite searchable
  **without** a vector DB. It's the "searchable memory" everyone's chasing,
  done the Tiramisu way.
- **Invariant check:** §6 (no vectors) ✅ · §7 (append a migration; FTS5
  table + triggers, idempotent) ✅ · fail-soft reads ✅.
- **Effort:** medium. One migration + one read function + one CLI verb in
  both dispatchers + tests (schema-exists, search-ranking, fail-soft).

### P2 — `<private>` redaction before logging
- **Source:** claude-mem's `<private>` tags.
- **Change:** in `memory.py`, strip/redact any `<private>…</private>` span
  from text before it's written to `learnings.db`.
- **Why it fits:** small, fail-soft privacy guarantee; the user controls
  what the crew remembers. Pairs naturally with P1 (don't index secrets).
- **Invariant check:** §4.5 fail-soft ✅ · safety-test-first if it gates a
  write path ✅.
- **Effort:** small. One redaction helper + unit tests (redacts, leaves
  normal text intact, fail-soft on malformed tags).

### P3 — Secrets / permission scan in Cookie's pre-commit
- **Source:** ECC's AgentShield.
- **Change:** Cookie's `hooks/cookie_review.py` gains a cheap, deterministic
  pre-LLM pass over the staged diff — high-signal secret patterns (AWS keys,
  private-key headers, tokens) and obviously-risky additions — surfaced as a
  **warning**, not a hard block (matches Cookie's "mention, don't fix" role,
  §4.4).
- **Why it fits:** the reviewer and the hook already exist; this sharpens
  Cookie without widening her scope.
- **Invariant check:** §4.4 one-agent-one-job ✅ (Cookie still only reviews)
  · new safety surface → write the test first ✅.
- **Effort:** medium. Pattern set + diff scan + test fixtures of planted
  secrets; must not block commits on false positives.

### P4 — Confidence scoring on learned preferences
- **Source:** ECC's instincts / confidence scoring.
- **Change:** add a `confidence`/`weight` column to preferences; `t reflect`
  nudges it based on whether a learning keeps recurring; `steering.py`
  surfaces high-confidence preferences more prominently.
- **Why it fits:** extends the existing reflect → `learnings.db` → steering
  loop instead of adding a subsystem.
- **Invariant check:** §4.3 learn-before-mutate — must stay **proposal-only**
  (confidence informs ordering/emphasis, never auto-rewrites a persona) ✅ ·
  §7 migration ✅.
- **Effort:** medium-high, and the §4.3 boundary needs care — easiest to get
  subtly wrong, so lowest priority.

---

## 4. Deliberately NOT borrowing (and why)

These are popular in the trending set but conflict with Tiramisu's design.
Listing them so the decision is on the record, not re-litigated later.

- **Vector / embedding store (Chroma, RAG)** — claude-mem, langchain.
  Rejected by **§6**: the data is structured (reviews, drafts, overrides);
  vectors don't fit. FTS5 (P1) covers the real need.
- **Web UI / daemon service** (`localhost:37777`) — claude-mem. Conflicts
  with the local-first, no-server, CLI-only design (§6 "push back" list).
- **A do-everything "skills" layer / 271 skills** — ECC. Conflicts with
  **§4.4** (one agent, narrow scope). Tiramisu's persona + steering split is
  the chosen equivalent; don't grow a parallel system.
- **Auto-promoting learnings into agent prompts** — ECC instincts→skills.
  Hard stop under **§4.3**: agents must never auto-rewrite their own
  prompts. P4 is allowed *only* as proposal/emphasis, never auto-apply.
- **Multi-vendor harness parity** (Cursor/Codex/Gemini/Copilot) — ECC.
  Out of scope; Tiramisu is its own crew, not a wrapper.

---

## 5. Recommendation

Do **P0 first** — adopting the ponytail YAGNI ladder into
`engineering-principles.md` is a tiny, zero-risk steering edit with the
highest leverage of anything here: it makes the whole crew write less code,
and it's the textbook use case for `t research apply`. It needs no new code.

Then ship **P1 + P2 (+ P0b)** together as one "searchable,
privacy-respecting, lean learnings" unit (all live in `memory.py`, share a
migration, reinforce each other). It's the most on-brand response to the
trend — the "searchable self-improving memory" that ECC, claude-mem, and
DeerFlow are built around, but using structured SQLite + FTS5 exactly as §6
prescribes, with zero new dependencies and no vector store. P3 is a strong
follow-up; P4 last, and only with the §4.3 guardrail explicit.

The throughline across every top repo (ECC, claude-mem, DeerFlow): a harness
with **skills + persistent memory + self-learning**. Tiramisu already has the
crew and the gated learning loop; P0–P2 close the remaining gap (lean output,
searchable memory) *without* importing the framework/vector/web-UI baggage
that those repos carry and that this project deliberately rejects.
