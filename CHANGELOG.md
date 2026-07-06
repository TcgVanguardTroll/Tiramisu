# Changelog

All notable changes to Tiramisu. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are tagged on
`master`.

## [1.2.0] — 2026-06-18

The self-improvement loop gets sharper and safer. Cannoli can now
autonomously study the tools trending in Tiramisu's space and report what's
worth borrowing; the learnings store becomes searchable, private, and
self-pruning; Cookie gains a secret scanner; and every agent writes less
code by default. Every change was mined from a real survey of top trending
repos (`docs/research/2026-06-18-trending-repos.md`) and filtered through
the CLAUDE.md invariants — the rejected ideas (vector store, web UI,
do-everything skills) are on the record, not in the tree.

### Added
- **`t research benchmark [topic..]`** — autonomously scouts the top trending
  repos, reads each README, and writes a prioritized "what could Tiramisu
  borrow?" report, every idea judged against §4/§6. Runs on the weekly
  schedule via `t research all`. Proposal-only (§4.3) — never edits code.
- **`t learn search <query>`** — full-text search over everything the crew has
  learned (preferences, reviews, commit messages, task plans), backed by
  SQLite **FTS5**. No vector store (§6); degrades to empty if FTS5 is absent.
- **`<private>…</private>` redaction** — secrets wrapped in the tag are
  stripped from every free-text field before it reaches `learnings.db` or the
  search index. Pinned as INVARIANTS.md §10.
- **Preference confidence scoring** — re-teaching a preference reinforces it
  (bumps a confidence count) instead of being dropped; high-confidence rules
  sort to the top of every composed prompt and are tagged `(reinforced xN)`.
  Human-initiated only (§4.3).
- **Deterministic secret scan in Cookie's pre-commit** — a regex pass over the
  staged diff warns on introduced credentials (AWS/GitHub/Slack tokens,
  private keys, hardcoded secrets), masked and warning-only (§4.4).
- **Ponytail-style YAGNI decision ladder** in `engineering-principles.md` — an
  actionable "need it? → stdlib? → platform? → dep? → one line? → minimum"
  checklist with a "lazy, not negligent" guard, composed into every prompt.

### Changed
- **Dedup on preference write** — re-teaching a rule (or a research-apply
  re-proposal) no longer grows `learnings.db` without bound.

### Fixed
- **Research fetches degrade gracefully behind a TLS-intercepting proxy** —
  `TIRAMISU_CA_BUNDLE` trusts a corporate/sandbox root cert, `TIRAMISU_INSECURE_SSL`
  is a last-resort escape hatch. Verification stays on by default.

### Notes
- Test suite grew 175 → **249**, green across the 3 OS × 2 Python CI matrix.
- Schema migrations v5 (FTS5 index) and v6 (`preferences.confidence`) added,
  contiguous and idempotent per the schema-discipline invariant (§7).

## [1.1.0] — 2026-06-11

The research loop closes: Cannoli's findings can now be adopted with one
keystroke per proposal, and research runs on an OS schedule instead of
only when you happen to open the CLI. Still learn-before-mutate — nothing
changes any prompt without an explicit per-edit y/N from the user.

### Added
- **`t research apply [file]`** — walks the latest findings file and asks
  y/N per proposal. Edits are sandboxed to the three shared steering files;
  accepted `code-style.md` changes are inserted inside the matching
  composed section. Persona files are never touched. An `.applied` sidecar
  prevents double-application. 30 safety/behavior tests, written first.
- **`steering/learned/`** — Cannoli can propose whole new steering docs;
  accepted ones are created here and composed into every agent prompt
  (after communication style, before preferences and repo overrides).
  New `include_learned` toggle in `load_steering()`.
- **Weekly OS-scheduled research** — `setup.sh` registers a cron entry,
  `setup.ps1` a Windows Scheduled Task (Mondays 09:00, idempotent,
  fail-soft), so sources are scanned even in weeks the CLI isn't opened.

## [1.0.0] — 2026-06-11

First tagged release. The full crew workflow is live: scope → write →
review → learn → reflect, with every agent wired into the `t` CLI.

### Added
- **Mochi (`t brainstorm`)** — idea stress-testing before scoping: 3+
  angles, the hidden assumption, the boring alternative, follow-up loop.
- **Brioche (`t onboard`)** — drafts new agent personas for unmet needs;
  never overwrites an existing persona, writes only into `agents/`.
- **Run budget for `t implement`** — `--budget` (default $5) stops a run
  cleanly when the estimated cost crosses the cap.
- **Context trimming in Éclair's loop** — old tool results are elided in
  batches past a 120K-char budget; recent rounds are never touched.
- **Per-project cost tracking** — `token_usage.repo_path` (migration v3)
  plus a "By project" section in `t reflect`.
- **Router audit** — every `tiramisu <text>` routing decision is logged
  (migration v4); `t reflect` reports fast/llm/fallback rates and the
  inputs the router couldn't place.
- **Deterministic router fast path** — exact command words skip the LLM;
  real file paths in scan phrasings are forwarded to `t scan`.
- **Adaptive thinking** on the analysis-heavy agents (scan, pr, task,
  reflect, chat, implement).
- **`docs/DESIGN.md`** — architecture, routing, data model, and workflow
  diagrams (Mermaid).
- **CI dispatcher smoke test** — `t help` / `t.bat help` run on every
  matrix cell, catching one-OS dispatcher breakage.
- Direct unit tests for `llm.py` (request shape, cost math) and safety
  tests for Brioche's file-write surface.

### Changed
- `DEFAULT_MODEL` → `claude-sonnet-4-6`; pricing table corrected.
- `research.py` split into four cycle-free modules (`research_common`,
  `research_discovery`, `research_library`, `research`).
- Steering docs moved from the repo root into `steering/`.
- Tool loops (chat, implement) now log token usage and cache the system
  prompt.

### Fixed
- `invoke()` silently dropped the `temperature` parameter.
- README described the commit hooks in the wrong order (Cookie's
  pre-commit review runs before Éclair's draft).
- Mermaid routing diagram failed to render on GitHub (unquoted parens).
