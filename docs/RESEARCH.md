# Tiramisu — Autonomous research

Cannoli (the beagle 🐶🍩) watches the world for you in three layers, all running
on a single weekly cadence. The dispatcher kicks her off in the background
whenever you launch `tiramisu` and her last scan was more than 7 days ago.

This document is the deep dive. The README has the one-paragraph version.

> **Critical safety property:** Findings are *proposed*, never auto-applied.
> The user adopts suggestions either by hand or one keystroke at a time via
> `t research apply` — every edit is shown and individually confirmed (y/N,
> default no). This is the autonomous version of the "learn before mutate"
> rule in [CLAUDE.md](../CLAUDE.md) §4.3.

---

## The three layers at a glance

| Layer | What it does | Output | Cost / week |
|---|---|---|---|
| **Anchors** (watched sources) | Diffs the URLs in your `sources.json` | `findings_YYYY-MM-DD.md` | ~$0.04 |
| **Discovery** (scouting) | GitHub Trending + HackerNews + arxiv search → candidate URLs | `candidates_YYYY-MM-DD.md` | ~$0.13 |
| **Library** (local docs / PDFs / books) | Reads files you drop into `~/.tiramisu/library/` or `<repo>/.tiramisu/library/` | `findings_library_YYYY-MM-DD.md` | depends on what's added |

Total weekly steady-state cost: **~$0.17**. Discovery uses only free public APIs (GitHub Search + HN Algolia + arxiv) — no API keys needed.

When findings are waiting, the next `tiramisu` invocation surfaces a one-liner:

```
🐶 Cannoli has 2 new finding(s) and 3 candidate(s) waiting.
   Run `t research` to see them.
```

---

## The full CLI surface

| `t research` action | What it does |
|---|---|
| `t research` (no args) | Show + mark-read the newest unread file (findings or candidates) |
| `t research run` | Scan watched sources now for new deltas |
| `t research apply [file]` | Walk the latest (or given) findings file; adopt each proposal with a y/N |
| `t research discover` | Scout GitHub Trending + HN + arxiv for new candidate sources |
| `t research ingest <path>` | Manually ingest a PDF / `.md` / `.txt` / `.rst` file or directory |
| `t research grab <arxiv-id>` | Download an arxiv paper into your library (auto-ingests on next run) |
| `t research grab --all` | Download every arxiv paper from the latest candidates file |
| `t research scout <path>` | Rank books in a directory by filename relevance (no API reads, cheap) |
| `t research show-scout` | Re-display the latest scout report |
| `t research library` | List files in your library + ingestion status |
| `t research all` | Run + discover + ingest-library in one shot |
| `t research mute` | Mark everything pending as read without showing |
| `t research list` | Chronological history of findings + candidates |
| `t research sources list` | Show active sources for this directory |
| `t research sources add <url> [name] [focus]` | Add a source to your user config |
| `t research sources remove <name-or-url>` | Drop a source |
| `t research sources reset` | Write defaults to `~/.tiramisu/sources.json` to edit |
| `t research sources show` | Dump the raw JSON config |

---

## Layer 1: Anchors (curated sources)

Cannoli diffs the URLs in `sources.json` against last-fetched copies. Only new content is summarized — no full re-summaries on every run.

Default sources cover: Anthropic API release notes, Anthropic Cookbook, aider docs, Python release notes.

### Source configuration — three precedence layers

The active source list follows the same per-user / per-repo pattern as everything else in Tiramisu:

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

Put a `sources.json` in a project's `.tiramisu/` directory pointing at that project's docs and Cannoli will watch them only while you're inside that repo.

---

## Layer 2: Discovery (scouting)

Three free public APIs run on each weekly background pass:

- **GitHub Search** — topics `claude`, `ai-agents`, `anthropic` (or whatever you've configured), sorted by recent activity
- **HackerNews Algolia** — top stories matching `claude anthropic` / `ai code review` / `ai dev tools`
- **arxiv** — papers matching configurable queries (see below)

Each candidate is summarized by Haiku from its abstract / README excerpt and ranked 1–5 for relevance. The output is `candidates_YYYY-MM-DD.md` with paste-able commands.

### Going from candidate to watched source

When a candidate looks promising, copy its `Add command`:

```
**Add command:** `t research sources add https://example.com "Some Repo" "Why it matters"`
```

That graduates it from a one-off candidate to a permanently-watched anchor source — and you'll see deltas in `findings_*.md` going forward.

### arxiv papers — discover + grab

Cannoli's arxiv queries are configurable via `~/.tiramisu/arxiv_queries.json` (a simple JSON list of strings). Defaults:

```
abs:prompt+engineering+AND+abs:agent
abs:LLM+AND+abs:code+review
abs:tool+use+AND+abs:language+model
```

Each arxiv result is ranked 1–5 based on the abstract — Cannoli never downloads the PDF during discovery (that would balloon costs). Each candidate carries an exact grab command:

```markdown
### Some Paper Title
**Relevance:** 4/5
**arxiv ID:** 2401.12345
**Why:** Argues for X-pattern in tool-use loops; would update agents/eclair.md.
**Grab command:** `t research grab 2401.12345`
```

Run `t research grab 2401.12345` to pull that one paper. Or `t research grab --all` to download every arxiv candidate from the latest scan. Papers land in `~/.tiramisu/library/arxiv/` and get auto-ingested (with chunked PDF splitting if needed) on the next `t research all` or weekly background run.

---

## Layer 3: Library (local docs / PDFs / books)

Two directories Cannoli reads automatically on the weekly background run:

```
~/.tiramisu/library/           ← books, papers, internal docs every agent learns from
<repo>/.tiramisu/library/      ← project-specific docs only that repo should consider
```

Supported file types: **.pdf** (sent as native document blocks to Claude — text, tables, figures all readable), **.md / .markdown / .txt / .rst** (read as text).

### Hash cache

A SHA-256 cache at `~/.tiramisu/.research/library_hashes.json` tracks each file. **Unchanged files are skipped** on subsequent runs — Cannoli only re-reads what's new or edited. Adding a 500-page PDF once costs ~$0.30 of Sonnet input; after that, free until you edit it.

### No size cap

PDFs over ~80 pages or ~25 MB are auto-split into chunks via `pypdf` and each chunk is ingested separately, with findings aggregated under a single header. A 500-page technical book becomes ~7 API calls, ~$1 one-time.

### Manual ingest

```powershell
t research ingest "C:\path\to\effective-python.pdf"
t research ingest "C:\path\to\design-docs-folder"
t research library              # see what's in your library + ingestion status
```

### Library scout (for huge collections)

If you point Tiramisu at a library of thousands of books, ingesting all of them would be very expensive. Instead, run scout first — it ranks every PDF by filename relevance using Haiku (no API reads of the actual PDFs):

```powershell
t research scout "C:\path\to\big-library"
t research show-scout          # render the latest scout report
```

Cost: ~$0.01 per 50 books. For a 1,886-PDF library, that's ~$0.25 total. The scout output is a ranked list of top-N candidates with paste-able `t research ingest "<path>"` commands — you pick the 5-10 worth ingesting.

### iCloud / OneDrive "Files On-Demand"

If a target file is a cloud-only placeholder (common with iCloud Drive on Windows), Cannoli detects the `OSError [Errno 22]` and materializes it via `shutil.copy2` automatically. If the cloud provider isn't running on the local machine, you'll get a clear instruction to right-click the file and choose "Always keep on this device" in File Explorer.

---

## Closing the loop: `t research apply`

`t research apply` turns "copy-paste what's worth keeping" into one
keystroke per proposal, without giving up the human gate:

```
🐶 Cannoli — applying findings from findings_2026-06-11.md
   3 actionable proposal(s), 0 already applied.

--- Anthropic API release notes ---
**Relevance:** 4/5 ...

Apply to steering/code-style.md? [y/N] y
  ✓ applied to steering/code-style.md
```

What it can and cannot do (pinned by `tests/test_research_apply.py`):

- **Edits** may only touch the three shared steering files. `code-style.md`
  edits are inserted *inside* the matching language section (or Universal
  Preferences) so `steering.py`'s section filter still composes them.
- **New docs** — when a finding deserves its own document, Cannoli proposes
  `steering/learned/<name>.md`. Accepted docs are created there and
  composed into every agent prompt by `steering.py` (after communication
  style, before user preferences and repo overrides, so the more specific
  layers still win).
- **Persona files are never touched.** Proposals targeting `agents/*.md`
  are skipped with a note — personas stay hand-edited (CLAUDE.md §4.1).
- Confirmation defaults to **no**; empty input, EOF, and Ctrl+C all decline.
- An `.applied` sidecar next to the findings file records accepted titles,
  so re-running never double-applies.
- Every accepted change is a normal file edit in the Tiramisu repo —
  review with `git diff`, revert with git like anything else.

## Scheduling

Two mechanisms keep Cannoli current; both end at the same human gate:

1. **Piggyback (always on):** every `tiramisu` launch checks the last-run
   marker and kicks a detached background `research all` if it's >7 days old.
2. **OS scheduler (installed by setup):** `setup.sh` registers a cron entry,
   `setup.ps1` a Windows Scheduled Task — both run
   `t research all --quiet` Mondays 09:00, so research stays fresh even
   during weeks you don't open the CLI. Registration is idempotent and
   fail-soft; removal one-liners are in the setup script comments:
   - macOS/Linux: `crontab -l | grep -v 'tiramisu-research' | crontab -`
   - Windows: `Unregister-ScheduledTask -TaskName "Tiramisu Research" -Confirm:$false`

---

## Architecture invariants for this subsystem

These match the broader rules in [CLAUDE.md](../CLAUDE.md) but are restated here because the research surface area is large:

1. **Findings are proposals, never auto-applied.** Every output file is meant for a human to read and selectively act on — `t research apply` just compresses "act on" to a per-edit y/N. The library hash cache prevents wasted work, not approval.
2. **Background runs never block user commands.** The dispatcher spawns research detached. If it fails, the user's main command still proceeds.
3. **Token usage is captured** in `learnings.db.token_usage` like every other API call. Run `t reflect` to see how much research is costing you.
4. **No persistent network state.** Each run fetches fresh content; the only state kept is the diff cache and findings files.
