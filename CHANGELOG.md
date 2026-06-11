# Changelog

All notable changes to Tiramisu. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are tagged on
`master`.

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
