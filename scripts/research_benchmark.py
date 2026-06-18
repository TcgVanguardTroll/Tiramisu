#!/usr/bin/env python3
"""
Cannoli benchmark -- autonomous "what could we borrow?" analysis.

This automates the workflow a human (or this repo's own crew) ran by hand to
produce docs/research/2026-06-18-trending-repos.md: scout the top trending
repos in Tiramisu's space, read each README, and emit a prioritized report of
what each does, what Tiramisu could borrow, and -- crucially -- which of its
invariants a given idea would conflict with.

It is PROPOSAL-ONLY by design (CLAUDE.md §4.3). It never edits code or
personas. Steering-shaped suggestions can be funneled through the existing
`t research apply` y/N gate; code-shaped suggestions are for a human to pick
up. Nothing here is auto-applied.

CLI (via research.py):
  t research benchmark            scout + analyze, write a report
  t research benchmark <topic..>  override the topics to scout

Output: ~/.tiramisu/.research/benchmark_YYYY-MM-DD.md
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL
from research_common import RESEARCH_DIR, MAX_SRC_CHARS, _fetch
from research_discovery import _fetch_github_topic, DISCOVERY_TOPICS

# How many distinct repos to analyze per run (after dedupe, by stars).
MAX_REPOS = 8
README_CHARS = 8000

# Distilled from CLAUDE.md §4/§6 so the model filters borrow-ideas the same
# way a maintainer would. Kept here (not parsed from CLAUDE.md) so the prompt
# stays small and stable; update it when the invariants change.
TIRAMISU_CONTEXT = """\
Tiramisu is a single-user, local-first, CLI multi-agent dev tool. A crew of
pastry-themed personas (Cookie reviews, Eclair writes, Croissant scopes,
Madeleine reflects, Cannoli researches) share a composed steering layer and
a SQLite learnings.db. It already has: a Haiku NL router, per-run cost
budgets + token tracking, git hooks (pre-commit review, commit-msg draft),
FTS5 full-text search over learnings, <private> redaction, preference
dedup+confidence, and an autonomous research subsystem with a human-gated
`t research apply`.

HARD INVARIANTS (reject ideas that violate these):
  - §4.3 learn-before-mutate: agents NEVER auto-rewrite their own prompts;
    proposals are human-applied.
  - §4.4 one agent, one job: no "do-everything" agent or parallel skills
    system.
  - §6 no vector store / RAG for learnings (structured SQLite only); no
    cloud/hosted features; no web UI / daemon; not a framework; not multi-user;
    keep the persona theme.

When judging a trending repo, ideas that fit are things like: a sharper
steering rule, a cheap deterministic check in a hook, a new structured
signal in learnings.db, a CLI ergonomics win. Ideas that DON'T fit: vector
memory, web dashboards, framework adoption, multi-vendor harness parity,
auto-applying changes."""

BENCHMARK_PROMPT = """\
You are Cannoli, Tiramisu's researcher. Below is a trending repo. Decide what
(if anything) Tiramisu could borrow, filtered through Tiramisu's design.

{context}

TRENDING REPO
  Name:  {name}
  Stars: {stars}
  About: {about}

README (truncated):
{readme}

Output EXACTLY this markdown, nothing else:

### {name}
**Relevance:** <1-5; 5 = adopt an idea from this soon, 1 = ignore>

**What it does:** <2-3 sentences, concrete mechanisms.>

**What Tiramisu could borrow:** <a specific, in-scope idea, or "Nothing
actionable -- already covered / out of scope.">

**Invariant check:** <"fits" with why, OR "conflicts with §X because ..." for
the parts that don't fit. Be specific.>

**Proposed action:** <one concrete next step phrased as a backlog item, or
"skip">

Rules:
- Be honest and conservative. Most repos rate 1-3. Saying "skip" is good.
- If an idea would violate a hard invariant, say so and rate it low.
- Don't invent features the README doesn't show.
"""


def _readme_for(repo: dict) -> str | None:
    """Fetch a repo's README via the raw GitHub CDN. Tries the default branch
    then main/master. Returns None if unreachable."""
    full = repo.get("full_name")
    if not full:
        return None
    branches = []
    if repo.get("default_branch"):
        branches.append(repo["default_branch"])
    branches += [b for b in ("main", "master") if b not in branches]
    for branch in branches:
        url = f"https://raw.githubusercontent.com/{full}/{branch}/README.md"
        text = _fetch(url)
        if not text.startswith("[error"):
            return text
    return None


def _collect_repos(topics: list[str]) -> list[dict]:
    """Top recently-pushed repos across the given topics, deduped by full_name,
    sorted by stars, capped at MAX_REPOS."""
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    by_name: dict[str, dict] = {}
    for topic in topics:
        for repo in _fetch_github_topic(topic, cutoff):
            name = repo.get("full_name")
            if name and name not in by_name:
                by_name[name] = repo
    repos = sorted(by_name.values(),
                   key=lambda r: r.get("stargazers_count", 0), reverse=True)
    return repos[:MAX_REPOS]


def _analyze(repo: dict) -> str:
    """Read one repo's README and return a markdown analysis section."""
    name = repo.get("full_name", "?")
    readme = _readme_for(repo)
    if not readme:
        return (f"### {name}\n**Relevance:** n/a\n\n"
                f"**What it does:** (README unreachable -- skipped.)\n")
    try:
        return invoke(
            prompt=BENCHMARK_PROMPT.format(
                context=TIRAMISU_CONTEXT,
                name=name,
                stars=f"{repo.get('stargazers_count', 0):,}",
                about=(repo.get("description") or "(no description)")[:300],
                readme=readme[:README_CHARS],
            ),
            model=FAST_MODEL,
            max_tokens=600,
            temperature=0.2,
        ).strip() + "\n"
    except Exception as e:
        return (f"### {name}\n**Relevance:** n/a\n\n"
                f"**What it does:** (analysis failed: {type(e).__name__}: {e})\n")


def _build_report(sections: list[str], topics: list[str], date: str) -> str:
    """Assemble the full benchmark report body. Pure function (no IO) so it's
    unit-testable."""
    return (
        f"# Cannoli benchmark -- {date}\n\n"
        f"Trending repos scouted across topics: {', '.join(topics)}.\n\n"
        "Each is analyzed for what Tiramisu could borrow, filtered through the "
        "invariants in CLAUDE.md §4/§6. **Nothing here is applied.** These are "
        "proposals: implement the code-shaped ones by hand, or funnel "
        "steering-shaped ones through `t research apply`. Ideas that conflict "
        "with a hard invariant are called out, not queued.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )


def benchmark(topics: list[str] | None = None, quiet: bool = False) -> Path | None:
    """Scout trending repos, analyze each README against Tiramisu's invariants,
    write benchmark_YYYY-MM-DD.md. Returns the path (or None if nothing found)."""
    topics = topics or list(DISCOVERY_TOPICS)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    log(f"\n🐶 Cannoli is benchmarking trending repos "
        f"(topics: {', '.join(topics)})...\n")

    repos = _collect_repos(topics)
    if not repos:
        log("  (no repos found -- network blocked or no recent matches.)")
        return None

    sections = []
    for repo in repos:
        log(f"  ↳ analyzing {repo.get('full_name')} "
            f"(★{repo.get('stargazers_count', 0):,})")
        sections.append(_analyze(repo))

    date = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"benchmark_{date}.md"
    out_path.write_text(_build_report(sections, topics, date), encoding="utf-8")

    log(f"\n✓ Benchmark written to: {out_path}")
    log("  Review it; implement what's worth borrowing. Nothing was applied.\n")
    return out_path
