# Changelog

All notable changes to Tiramisu. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are tagged on
`master`.

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
