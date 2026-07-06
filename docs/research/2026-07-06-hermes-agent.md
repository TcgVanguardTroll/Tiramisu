# Benchmark note — NousResearch/hermes-agent

**Date:** 2026-07-06
**Method:** the `t research benchmark` workflow, aimed at a single repo. Read
hermes-agent's README + feature docs for concrete mechanisms, then filtered
each against the CLAUDE.md §4/§6 invariants.

Hermes-agent ("the agent that grows with you", Python/MIT) is a self-improving
autonomous agent — the closest philosophical neighbor to Tiramisu, but with the
opposite architectural bets (multi-platform, cloud-delivered, "does everything").

## Validating signal

Hermes stores session memory in **SQLite FTS5** ("FTS5 session search with LLM
summarization for cross-session recall"). That's the exact contrarian choice
Tiramisu made in v1.2.0 (#10) — a large project independently landed on
"structured FTS5, not vectors." Evidence that §6 is a serious-engineering
stance, not a compromise.

## Borrowable (in-scope)

| # | Idea | Relevance | Status |
|---|------|-----------|--------|
| T1 | REPL slash-command autocomplete (+ interrupt-and-redirect) | 4/5 | **T1a shipped below** |
| T2 | `learn recall`: FTS5 hits → one LLM-summarized answer | 3/5 | backlog |
| T3 | Post-task learning *proposals* (gated) after a complex `t implement` | 3/5 | backlog, §4.3-careful |

**T1a — command autocomplete derives from ROUTES (shipped).** Hermes' TUI has
"slash-command autocomplete". Tiramisu already had a REPL completer, but its
command vocabulary came from a hand-maintained `PHRASE_STARTERS` list that had
drifted — `brainstorm`, `pr`, `chat`, `learn`, `reflect`, `research`, `onboard`
had *no* tab-completion. Fixed by deriving completions from `ROUTES` (+ a
subcommand map: `research benchmark`, `learn search`, …), with an anti-drift
test so new commands can't regress it. Pure CLI UX, zero invariant conflict.

**T1b — interrupt-and-redirect (backlog).** Stop Éclair mid-run and steer,
instead of Ctrl-C + restart. Bigger — touches the streaming agent loop in
`implement.py` — so deferred, not bundled here (YAGNI).

## Deliberately rejected (consistent with prior calls)

- **Autonomous skill creation / procedural "skills" system** — §4.4 (one agent,
  one job) and §4.3 (no auto-mutation). Same answer we gave ECC's 271-skill
  system. The gated analogue is T3.
- **Multi-platform delivery (Telegram/Discord/Slack/…), multi-LLM, Docker /
  SSH / serverless** — break local-first, Anthropic-centric, CLI-only (§6).
- **Arbitrary natural-language cron automations** — edges into "do everything";
  Tiramisu already schedules the one job that matters (research, #7).

## One flagged maybe

**MCP client support** (let Éclair / chat consume any MCP server as tools) is
on-trend and arguably fits the existing tool-use pattern — but it's a *new
dangerous surface* (external tools) and would demand the safety-test-first
treatment (§8). A deliberate design decision, not a quick borrow.
