#!/usr/bin/env python3
"""
Cannoli -- autonomous research.

Periodically fetches external sources (Anthropic API release notes,
Anthropic Cookbook, aider docs, Python release notes) and produces a markdown
findings file with proposed updates to the steering docs.

Architecture:
  - Background-kicked when the user runs `tiramisu` and last run is stale (>7d).
  - Writes findings to ~/.tiramisu/.research/findings_YYYY-MM-DD.md
  - The dispatcher surfaces a one-line notice on the next interactive session.
  - Findings are PROPOSED EDITS ONLY -- never auto-applied. The user copies the
    suggested patches into the steering files by hand (or ignores them).
  - This is the autonomous version of the "learn before mutate" rule.

CLI:
  t research          show the most recent unread findings, mark as read
  t research run      force a research run now (foreground)
  t research mute     mark all pending findings as read without showing
  t research list     list all findings files chronologically
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL


# -------- config --------

TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
RESEARCH_DIR  = TIRAMISU_HOME / ".research"
CACHE_DIR     = RESEARCH_DIR / "cache"      # last-fetched copies
READ_MARKER   = ".read"                     # suffix appended once user has seen findings
STALE_DAYS    = 7
HTTP_TIMEOUT  = 20                          # seconds per source
MAX_SRC_CHARS = 30000                       # truncate huge pages

# Default sources -- shipped as a fallback so first-run always has something
# to scan. Users can override via ~/.tiramisu/sources.json or extend per-repo
# via <repo>/.tiramisu/sources.json. See load_sources() below.
DEFAULT_SOURCES = [
    {
        "name":  "Anthropic API release notes",
        "url":   "https://docs.anthropic.com/en/release-notes/api",
        "focus": "New models, deprecated model names, pricing changes, new SDK "
                 "features (tool use, extended thinking, prompt caching, batches).",
    },
    {
        "name":  "Anthropic Cookbook",
        "url":   "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md",
        "focus": "New patterns for tool use, agent loops, prompt caching, "
                 "and streaming. Skip examples already in use.",
    },
    {
        "name":  "aider docs",
        "url":   "https://aider.chat/docs/",
        "focus": "Features Tiramisu doesn't have. Worth borrowing or explicitly "
                 "deciding not to borrow. Coding-style conventions.",
    },
    {
        "name":  "Python release notes",
        "url":   "https://docs.python.org/3/whatsnew/3.13.html",
        "focus": "Deprecations, new idioms relevant to code-style.md (pathlib, "
                 "typing, async, error handling).",
    },
]


def user_sources_path() -> Path:
    return TIRAMISU_HOME / "sources.json"


def repo_sources_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".tiramisu" / "sources.json"


def _load_json_list(path: Path) -> list[dict] | None:
    """Read a JSON list of source dicts from a file. Returns None on any error
    (file missing, malformed JSON, wrong shape) so the caller can fall back."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cannoli] couldn't parse {path}: {e}", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(f"[cannoli] {path} must be a JSON list; ignoring.", file=sys.stderr)
        return None
    valid = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "name" not in item or "url" not in item:
            continue
        item.setdefault("focus", "general updates")
        valid.append(item)
    return valid


def load_sources() -> list[dict]:
    """
    Return the active source list, layered:
      1. If ~/.tiramisu/sources.json exists -> REPLACES defaults entirely.
         Otherwise the hardcoded DEFAULT_SOURCES is the base.
      2. Plus any sources in <cwd>/.tiramisu/sources.json -- additive.

    Same precedence model as steering.py's load_steering().
    """
    base = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)
    repo_extra = _load_json_list(repo_sources_path())
    if repo_extra:
        base = base + repo_extra
    return base


# -------- helpers --------

def _last_run_file() -> Path:
    return RESEARCH_DIR / "last_run"


def is_stale() -> bool:
    """True if the last research run was more than STALE_DAYS ago."""
    lr = _last_run_file()
    if not lr.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(lr.stat().st_mtime)
    return age > timedelta(days=STALE_DAYS)


def _is_research_output(path: Path) -> bool:
    """Either findings_*.md (delta on watched source) or candidates_*.md
    (new source proposals from discovery layer)."""
    name = path.name
    return name.startswith("findings_") or name.startswith("candidates_")


def _all_research_files() -> list[Path]:
    if not RESEARCH_DIR.exists():
        return []
    return [f for f in RESEARCH_DIR.glob("*.md") if _is_research_output(f)]


def pending_count() -> int:
    """Count of unread findings + candidates files."""
    return sum(
        1 for f in _all_research_files()
        if not (f.with_suffix(f.suffix + READ_MARKER)).exists()
    )


def latest_pending() -> Path | None:
    """The newest unread research output (findings OR candidates)."""
    unread = [
        f for f in _all_research_files()
        if not (f.with_suffix(f.suffix + READ_MARKER)).exists()
    ]
    unread.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return unread[0] if unread else None


def mark_read(findings_file: Path) -> None:
    """Touch a .read sibling so this file no longer counts as pending."""
    marker = findings_file.with_suffix(findings_file.suffix + READ_MARKER)
    marker.touch(exist_ok=True)


def kick_off_background_if_stale() -> None:
    """
    Called by dispatch.py at start of every `tiramisu` invocation. If research
    is stale, spawn the run in a detached background process and return
    immediately. Best-effort -- never raises.
    """
    if not is_stale():
        return
    try:
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        # Touch the marker BEFORE spawning so a second invocation in the same
        # minute doesn't trigger a second background run.
        _last_run_file().touch()

        creationflags = 0
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            creationflags = 0x00000008 | 0x00000200

        # "all" runs both: deltas on watched sources + discovery scouting
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "research.py"), "all", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception:
        # Never block the user's main command on a research-spawn failure
        pass


def print_pending_notice(console=None) -> None:
    """Surface a one-liner if there are unread findings."""
    n = pending_count()
    if n == 0:
        return
    msg = (f"🐶 Cannoli has {n} new finding(s). "
           f"Run `t research` to see them, or `t research mute` to skip.")
    if console is not None:
        console.print(f"\n[dim]{msg}[/dim]\n")
    else:
        print(f"\n{msg}\n")


# -------- the actual research --------

def _fetch(url: str) -> str:
    """Download a URL with a user agent, return text (truncated to MAX_SRC_CHARS)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Tiramisu-Cannoli/1.0 (+research)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                    context=_ssl_context()) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return f"[error fetching {url}: {e}]"
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_SRC_CHARS:
        text = text[:MAX_SRC_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text


def _cache_path(source_name: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in source_name)
    return CACHE_DIR / f"{safe}.txt"


def _delta_vs_cached(source_name: str, fresh: str) -> str | None:
    """
    Compare fresh content to the cached version. Returns:
      - None if no cache existed (treat all as new)
      - "" if no change since last run (skip)
      - A small diff-ish snippet otherwise (the new/changed lines)
    """
    cache = _cache_path(source_name)
    if not cache.exists():
        return None
    try:
        old = cache.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if old.strip() == fresh.strip():
        return ""
    # Naive delta: lines in fresh that aren't in old. Good enough for changelogs.
    old_lines = set(old.splitlines())
    new_lines = [ln for ln in fresh.splitlines() if ln.strip() and ln not in old_lines]
    if not new_lines:
        return ""
    return "\n".join(new_lines[:200])


def _save_cache(source_name: str, content: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(source_name).write_text(content, encoding="utf-8")


SUMMARY_PROMPT = """\
You are Cannoli, the Tiramisu researcher. Your job is to scan an external
source for changes relevant to Tiramisu and produce ONE concise section
with proposed updates to its steering docs.

SOURCE: {name}
SOURCE URL: {url}
WHAT TO LOOK FOR: {focus}

Tiramisu context (so you know what's already covered):
  - Anthropic SDK, Python 3.10+
  - DEFAULT_MODEL = claude-sonnet-4-6, FAST_MODEL = claude-haiku-4-5
  - Prompt caching, tool use, streaming already in use
  - Steering docs: engineering-principles.md, code-style.md, communication-style.md
  - Per-language code style for Python/Java/Rust/TS
  - Learn-before-mutate rule: agents must never auto-edit their own prompts

CONTENT (raw, possibly HTML):
{content}

Output format (markdown):

## {name}
**Relevance:** <1-5, where 5 = ship this change today, 1 = ignore>

**Summary (2-3 sentences):** what's new and why it matters to Tiramisu.

**Proposed update:** the exact file + section to edit, with the proposed
text in a fenced block. If no actionable update, write "No action recommended."

Rules:
- ONLY propose updates that meaningfully improve Tiramisu. Ignore tangential changes.
- ALWAYS cite the specific bit of the source you're acting on.
- If relevance is 1 or 2, say so honestly and recommend no action.
- Be concise. The user reads this on a Monday morning.
"""


def run_research(quiet: bool = False) -> Path:
    """
    Fetch each source, summarize via FAST_MODEL, write a findings file.
    Returns the findings file path.
    """
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    sources = load_sources()
    log(f"\n🐶 Cannoli is scanning {len(sources)} external source(s)...\n")

    findings_sections: list[str] = []

    for src in sources:
        name, url, focus = src["name"], src["url"], src["focus"]
        log(f"  ↳ fetching: {name}")

        fresh = _fetch(url)
        if fresh.startswith("[error fetching"):
            findings_sections.append(
                f"## {name}\n**Relevance:** n/a\n\n{fresh}\n"
            )
            continue

        delta = _delta_vs_cached(name, fresh)
        _save_cache(name, fresh)

        if delta == "":
            # No change since last run
            log(f"     (no delta -- skipping)")
            continue

        # Summarize via the LLM. Pass either the delta (if we have one) or the
        # full fresh content (first time we see this source).
        content_for_prompt = delta if delta is not None else fresh

        try:
            section = invoke(
                prompt=SUMMARY_PROMPT.format(
                    name=name, url=url, focus=focus,
                    content=content_for_prompt[:MAX_SRC_CHARS],
                ),
                model=FAST_MODEL,
                max_tokens=600,
                temperature=0.2,
            ).strip()
            findings_sections.append(section)
            log(f"     done")
        except Exception as e:
            findings_sections.append(
                f"## {name}\n**Relevance:** n/a\n\n[summarization failed: {e}]\n"
            )

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"findings_{today}.md"
    body = (
        f"# Cannoli findings -- {today}\n\n"
        "Proposed updates from external sources. **None of these are applied "
        "automatically.** Review, paste what's worth keeping into the steering "
        "files, ignore the rest.\n\n"
        + "\n\n".join(findings_sections)
        + "\n"
    )
    out_path.write_text(body, encoding="utf-8")
    _last_run_file().touch()

    log(f"\n✓ Findings written to: {out_path}")
    log(f"  ({pending_count()} unread now)\n")
    return out_path


# -------- show / mute --------

def show_latest() -> None:
    f = latest_pending()
    if f is None:
        print("\n🐶 Cannoli has no unread findings.")
        if is_stale():
            print("  Run `t research run` to scan sources now.\n")
        else:
            most_recent = sorted(
                RESEARCH_DIR.glob("findings_*.md") if RESEARCH_DIR.exists() else [],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if most_recent:
                print(f"  Last run: {most_recent[0].name}\n")
            else:
                print()
        return

    text = f.read_text(encoding="utf-8")

    # Use rich for nice rendering if available; fall back to plain print.
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console().print(Markdown(text))
    except Exception:
        print(text)

    mark_read(f)
    print(f"\n[marked as read: {f.name}]\n")


def mute_all_pending() -> None:
    n = 0
    for f in _all_research_files():
        marker = f.with_suffix(f.suffix + READ_MARKER)
        if not marker.exists():
            marker.touch()
            n += 1
    print(f"Marked {n} pending finding(s) / candidate(s) as read.")


def list_all() -> None:
    files = sorted(_all_research_files(), key=lambda p: p.stat().st_mtime)
    if not files:
        print("No research history yet. Run `t research run` (or `t research discover`) to start.")
        return
    print("\nResearch history:\n")
    for f in files:
        marker = f.with_suffix(f.suffix + READ_MARKER)
        status = "read" if marker.exists() else "UNREAD"
        kind   = "candidates" if f.name.startswith("candidates_") else "findings  "
        ts     = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  [{status:6}]  {kind}  {f.name}   ({ts})")
    print()


# -------- open discovery (GitHub Trending + HackerNews) --------
#
# This is the exploration layer that complements the curated sources above.
# No API keys required -- both endpoints are free public APIs.
#
# Output goes to ~/.tiramisu/.research/candidates_YYYY-MM-DD.md, separate from
# findings_*.md. Candidates are URLs the user might want to ADD as permanent
# watched sources (via `t research sources add <url>`), not deltas from
# existing watched sources.

DISCOVERY_TOPICS = ["claude", "ai-agents", "anthropic"]
DISCOVERY_HN_QUERIES = ["claude anthropic", "ai code review", "ai dev tools"]
MAX_PER_BUCKET = 3  # how many candidates to take from each topic / query

GITHUB_API_BASE = "https://api.github.com"
HN_API_BASE     = "https://hn.algolia.com/api/v1"
ARXIV_API_BASE  = "http://export.arxiv.org/api/query"

# arxiv queries -- defaults baked in, user can override via
# ~/.tiramisu/arxiv_queries.json (same pattern as sources.json).
# Syntax: arxiv search strings, URL-encoded boolean operators allowed.
DEFAULT_ARXIV_QUERIES = [
    "abs:prompt+engineering+AND+abs:agent",
    "abs:LLM+AND+abs:code+review",
    "abs:tool+use+AND+abs:language+model",
]
ARXIV_QUERIES_FILE = TIRAMISU_HOME / "arxiv_queries.json"


def _ssl_context():
    """SSL context that works on Windows where Python's default cert store
    often lacks modern CA roots. Uses certifi (already shipped via the
    anthropic dep) when available; falls back to the system default."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _http_get_json(url: str) -> dict | None:
    """GET a URL and parse as JSON. Returns None on any error."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Tiramisu-Cannoli/1.0",
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                    context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[cannoli] GET {url} failed: {e}", file=sys.stderr)
        return None


def _fetch_github_topic(topic: str, since_date: str) -> list[dict]:
    """Top repos with this topic, pushed within the last week. Free public API."""
    q = f"topic:{topic}+pushed:>{since_date}"
    url = f"{GITHUB_API_BASE}/search/repositories?q={q}&sort=stars&order=desc&per_page=5"
    data = _http_get_json(url)
    return (data or {}).get("items", [])[:MAX_PER_BUCKET]


def _fetch_hn(query: str) -> list[dict]:
    """HN stories matching this query, sorted by points. Free Algolia API."""
    enc = urllib.request.quote(query)
    url = f"{HN_API_BASE}/search?query={enc}&tags=story&hitsPerPage=5&numericFilters=points>20"
    data = _http_get_json(url)
    return (data or {}).get("hits", [])[:MAX_PER_BUCKET]


def _load_arxiv_queries() -> list[str]:
    """Read user's arxiv_queries.json or fall back to DEFAULT_ARXIV_QUERIES.
    Same precedence pattern as sources.json (user file replaces defaults)."""
    if ARXIV_QUERIES_FILE.exists():
        try:
            data = json.loads(ARXIV_QUERIES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                clean = [q for q in data if isinstance(q, str) and q.strip()]
                if clean:
                    return clean
        except Exception as e:
            print(f"[cannoli] couldn't parse {ARXIV_QUERIES_FILE}: {e}",
                  file=sys.stderr)
    return list(DEFAULT_ARXIV_QUERIES)


def _search_arxiv(query: str, max_results: int = MAX_PER_BUCKET) -> list[dict]:
    """Query the arxiv API. Returns papers as dicts. No API key required."""
    import xml.etree.ElementTree as ET

    url = (f"{ARXIV_API_BASE}?search_query={query}"
           f"&max_results={max_results}"
           f"&sortBy=submittedDate&sortOrder=descending")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Tiramisu-Cannoli/1.0 (+research)"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                    context=_ssl_context()) as resp:
            xml_data = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[cannoli] arxiv query failed ({query}): {e}", file=sys.stderr)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        print(f"[cannoli] arxiv response parse error: {e}", file=sys.stderr)
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        id_el      = entry.find("atom:id",        ns)
        title_el   = entry.find("atom:title",     ns)
        summary_el = entry.find("atom:summary",   ns)
        pub_el     = entry.find("atom:published", ns)
        if id_el is None or title_el is None:
            continue

        # Extract bare arxiv id from URL like http://arxiv.org/abs/2401.12345v1
        full_id  = (id_el.text or "").rsplit("/", 1)[-1]
        # Strip version suffix (v1, v2, etc.) so the PDF URL points to current
        arxiv_id = full_id.split("v")[0] if "v" in full_id else full_id

        papers.append({
            "arxiv_id": arxiv_id,
            "title":    (title_el.text or "").strip().replace("\n", " "),
            "summary":  ((summary_el.text or "").strip() if summary_el is not None else ""),
            "published":((pub_el.text or "")[:10] if pub_el is not None else ""),
            "pdf_url":  f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            "abs_url":  f"https://arxiv.org/abs/{arxiv_id}",
        })
    return papers


CANDIDATE_PROMPT = """\
You are Cannoli, the Tiramisu researcher. You've found a candidate URL that
*might* be worth adding to the user's watched-sources list. Decide if it is.

The user's existing watched sources are about: Anthropic API docs / Cookbook /
aider / Python release notes. They care about: AI-assisted code review,
prompt engineering, agent loops, tool use, Python idioms.

CANDIDATE
  Discovered via: {discovered_via}
  Name:           {name}
  URL:            {url}
  Quick preview:  {preview}
  Popularity:     {metric}

CONTENT (truncated):
{content}

Output exactly this markdown structure:

### {name}
**Relevance:** <1-5 -- 5 = add this today, 3 = maybe, 1 = ignore>

**Why:** <1-2 sentences -- what this is and whether it would actually
inform Tiramisu's steering. Be honest. Saying "skip" is encouraged.>

**Add command:** `t research sources add <appropriate-url> "<name>" "<focus>"`
                 (if relevance >= 3, otherwise write "(skip -- relevance too low)")

Rules:
- "Ranked lists of AI tools" or generic blog posts -> usually relevance 1-2.
- Repos with concrete patterns / code examples -> can be 3-5.
- If the URL is paywalled, behind login, or 404s, mark relevance 1 and skip.
- Don't pad. If you can't tell what it is, say so.
"""


def _summarize_candidate(c: dict) -> str:
    """Fetch the candidate URL, ask Haiku for a one-section verdict."""
    content = _fetch(c["url"])
    if content.startswith("[error fetching"):
        return (
            f"### {c['name']}\n"
            f"**Relevance:** 1/5\n\n"
            f"**Why:** Could not fetch the page ({content}). Skip.\n\n"
            f"**Add command:** (skip -- unreachable)\n"
        )

    # GitHub repo pages are huge -- prefer the README content if we can guess it
    if "github.com" in c["url"] and "/raw.githubusercontent.com/" not in c["url"]:
        # Best effort: try the README at the top-level
        parts = c["url"].replace("https://github.com/", "").rstrip("/").split("/")
        if len(parts) >= 2:
            for branch in ("main", "master"):
                readme = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/{branch}/README.md"
                readme_content = _fetch(readme)
                if not readme_content.startswith("[error"):
                    content = readme_content
                    c["url"] = readme  # report the raw URL so `add` works
                    break

    try:
        return invoke(
            prompt=CANDIDATE_PROMPT.format(
                discovered_via=c["discovered_via"],
                name=c["name"],
                url=c["url"],
                preview=c.get("preview") or "(none)",
                metric=c.get("metric") or "(unknown)",
                content=content[:8000],
            ),
            model=FAST_MODEL,
            max_tokens=400,
            temperature=0.2,
        ).strip() + "\n"
    except Exception as e:
        return (
            f"### {c['name']}\n"
            f"**Relevance:** n/a\n\n"
            f"**Why:** Summarization failed ({type(e).__name__}: {e}).\n\n"
            f"**Add command:** (skip)\n"
        )


ARXIV_CANDIDATE_PROMPT = """\
You are Cannoli, the Tiramisu researcher. You found an arxiv paper that
*might* be worth grabbing into the user's library (where Tiramisu would
read it on the next weekly run). Decide if it is.

PAPER
  Title:     {name}
  arxiv ID:  {arxiv_id}
  Submitted: {published}

ABSTRACT
{abstract}

The user's existing watched sources cover: Anthropic API docs, Cookbook,
aider, Python release notes. They care about AI-assisted code review,
prompt engineering, agent loops, tool use, Python idioms.

Output exactly this markdown:

### {name}
**Relevance:** <1-5 -- 5 = grab today, 3 = maybe, 1 = ignore>
**arxiv ID:** {arxiv_id}
**Why:** <2 sentences -- what the paper actually argues and whether
reading it would inform Tiramisu's steering. Be honest. "Skip" is fine.>
**Grab command:** `t research grab {arxiv_id}`   (if relevance >= 3,
                  otherwise write "(skip -- relevance too low)")

Rules:
- A theory paper with no concrete recommendations -> usually 1-2.
- A paper with patterns / measurements directly applicable to agent
  design or code review -> can be 3-5.
- Don't pad. If the abstract is generic AI-hype, mark relevance 1.
"""


def _summarize_arxiv_candidate(c: dict) -> str:
    """Summarize an arxiv paper using its abstract (no PDF fetch needed).
    Output matches the candidate format with a Grab command line."""
    try:
        return invoke(
            prompt=ARXIV_CANDIDATE_PROMPT.format(
                name=c["name"],
                arxiv_id=c["arxiv_id"],
                published=c["metric"].replace("submitted ", ""),
                abstract=c["preview"][:2000],
            ),
            model=FAST_MODEL,
            max_tokens=350,
            temperature=0.2,
        ).strip() + "\n"
    except Exception as e:
        return (f"### {c['name']}\n"
                f"**Relevance:** n/a\n\n"
                f"**arxiv ID:** {c['arxiv_id']}\n\n"
                f"Summarization failed: {type(e).__name__}: {e}\n")


# -------- grab: pull an arxiv paper into the library --------


def _arxiv_library_dir() -> Path:
    """Path of the arxiv subdir under the user library. Computed lazily because
    USER_LIBRARY is defined further down in this file."""
    return USER_LIBRARY / "arxiv"


def _normalize_arxiv_id(raw: str) -> str:
    """Accept '2401.12345', '2401.12345v3', 'http://arxiv.org/abs/2401.12345',
    or 'arxiv:2401.12345' and return the bare ID."""
    s = raw.strip()
    # Strip URL prefixes
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                   "https://arxiv.org/pdf/", "http://arxiv.org/pdf/",
                   "arxiv:", "arXiv:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = s.rstrip(".pdf").rstrip("/")
    # Strip version suffix
    if "v" in s and s.split("v")[-1].isdigit():
        s = s.rsplit("v", 1)[0]
    return s


def grab_paper(arxiv_id_raw: str) -> Path | None:
    """Download an arxiv PDF into ~/.tiramisu/library/arxiv/<id>.pdf.
    The library ingestion path will pick it up on the next run-all."""
    arxiv_id = _normalize_arxiv_id(arxiv_id_raw)
    if not arxiv_id:
        print(f"[grab] could not parse arxiv id: {arxiv_id_raw!r}")
        return None

    arxiv_dir = _arxiv_library_dir()
    arxiv_dir.mkdir(parents=True, exist_ok=True)
    target = arxiv_dir / f"{arxiv_id}.pdf"
    if target.exists():
        kb = target.stat().st_size / 1024
        print(f"  already have: {target}  ({kb:.0f} KB)")
        return target

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    print(f"  downloading: {pdf_url}")
    req = urllib.request.Request(
        pdf_url,
        headers={"User-Agent": "Tiramisu-Cannoli/1.0 (+research)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60,
                                    context=_ssl_context()) as resp:
            data = resp.read()
    except Exception as e:
        print(f"  [error] download failed: {e}")
        return None

    target.write_bytes(data)
    kb = target.stat().st_size / 1024
    print(f"  saved: {target}  ({kb:.0f} KB)")
    print(f"  -> Cannoli will ingest this on the next `t research all` "
          f"(or weekly background run).")
    return target


def grab_all_from_latest() -> int:
    """Find every `t research grab <id>` command in the newest candidates
    file and execute them. Returns the count actually downloaded."""
    import re

    files = sorted(
        RESEARCH_DIR.glob("candidates_*.md"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not files:
        print("\nNo candidates file found. Run `t research discover` first.\n")
        return 0

    latest = files[0]
    text = latest.read_text(encoding="utf-8")

    # Grab commands look like: `t research grab 2401.12345`
    # Skip placeholders like "(skip -- relevance too low)"
    raw_ids = re.findall(r"t research grab\s+([0-9][^\s`)]+)", text)
    ids = []
    for r in raw_ids:
        norm = _normalize_arxiv_id(r)
        if norm and norm not in ids:
            ids.append(norm)

    if not ids:
        print(f"\nNo graspable arxiv papers in {latest.name}\n")
        return 0

    print(f"\nGrabbing {len(ids)} paper(s) from {latest.name}:\n")
    n = 0
    for aid in ids:
        if grab_paper(aid):
            n += 1
    print(f"\n  done: {n}/{len(ids)} downloaded.")
    print(f"  Run `t research all` (or wait for next weekly background run) "
          f"to ingest them into findings.\n")
    return n


def discover(quiet: bool = False) -> Path | None:
    """
    Pull candidates from GitHub Trending + HackerNews, summarize each via
    FAST_MODEL, write to candidates_YYYY-MM-DD.md. Returns the file path.
    """
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    log("\n🐶 Cannoli is scouting for new sources...\n")
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    candidates: list[dict] = []

    # GitHub repos with relevant topics
    for topic in DISCOVERY_TOPICS:
        log(f"  ↳ GitHub topic:{topic}")
        for repo in _fetch_github_topic(topic, cutoff):
            candidates.append({
                "discovered_via": f"GitHub topic:{topic}",
                "name":           repo.get("full_name") or repo.get("name", "?"),
                "url":            repo.get("html_url", ""),
                "preview":        (repo.get("description") or "")[:200],
                "metric":         f"{repo.get('stargazers_count', 0):,} stars",
            })

    # HN stories matching topical queries
    for query in DISCOVERY_HN_QUERIES:
        log(f"  ↳ HN search: {query!r}")
        for story in _fetch_hn(query):
            url = story.get("url") or f"https://news.ycombinator.com/item?id={story.get('objectID', '')}"
            candidates.append({
                "discovered_via": f"HackerNews ({query})",
                "name":           story.get("title", "?"),
                "url":            url,
                "preview":        "",
                "metric":         f"{story.get('points', 0)} points",
            })

    # arxiv papers (uses the abstract directly -- no PDF fetch during discovery)
    import time
    for query in _load_arxiv_queries():
        log(f"  ↳ arxiv search: {query}")
        for paper in _search_arxiv(query, max_results=MAX_PER_BUCKET):
            candidates.append({
                "discovered_via": f"arxiv search: {query}",
                "name":           paper["title"],
                "url":            paper["abs_url"],
                "preview":        paper["summary"][:300],
                "metric":         f"submitted {paper['published']}",
                "arxiv_id":       paper["arxiv_id"],
                "pdf_url":        paper["pdf_url"],
            })
        # arxiv asks for ~3s between requests; be a good citizen
        time.sleep(3)

    # Dedupe by URL
    seen = set()
    unique = []
    for c in candidates:
        u = c["url"]
        if u and u not in seen:
            seen.add(u)
            unique.append(c)
    candidates = unique

    log(f"\n  Found {len(candidates)} unique candidate(s). Summarizing...")

    sections: list[str] = []
    by_source: dict[str, list[str]] = {}
    for c in candidates:
        # arxiv has its own prompt + uses the abstract instead of fetching PDF
        if "arxiv_id" in c:
            summary = _summarize_arxiv_candidate(c)
        else:
            summary = _summarize_candidate(c)
        by_source.setdefault(c["discovered_via"], []).append(summary)

    for src, sums in by_source.items():
        sections.append(f"## Found via {src}\n\n" + "\n".join(sums))

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"candidates_{today}.md"
    body = (
        f"# Cannoli candidates -- {today}\n\n"
        "URLs Cannoli scouted that you might want to add to your watched "
        "sources. **Nothing is auto-added.** Copy the `Add command` for any "
        "candidate you want to graduate to a permanent watched source.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    out_path.write_text(body, encoding="utf-8")

    log(f"\n✓ Candidates written to: {out_path}\n")
    return out_path


# -------- library ingestion (local PDFs / docs / books) --------
#
# Drop files into ~/.tiramisu/library/ (user-wide) or <repo>/.tiramisu/library/
# (project-specific) and Cannoli will read them on the weekly background run,
# skipping unchanged files via content-hash cache.
#
# Supported file types:
#   .pdf  -- sent to Anthropic API as a document block (native PDF support)
#   .md / .txt / .rst -- sent as text
#
# Limits:
#   - PDFs over ~100 pages or 32MB will fail at the API boundary; split first.
#   - Each file gets DEFAULT_MODEL (Sonnet) -- quality matters more than speed
#     because we only re-process when content changes.

USER_LIBRARY    = TIRAMISU_HOME / "library"
LIBRARY_HASH_DB = RESEARCH_DIR / "library_hashes.json"
INGESTIBLE_EXTS = {".pdf", ".md", ".markdown", ".txt", ".rst"}
MAX_TEXT_CHARS  = 80000   # ~20k tokens, well under any model limit
# PDF size / page limits for auto-splitting live further down by _ingest_pdf().


def repo_library_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".tiramisu" / "library"


@contextmanager
def _accessible_path(path: Path):
    """
    Yield a path that we can actually read.

    On Windows, files under iCloud Drive (and OneDrive "Files On-Demand")
    may exist on disk only as cloud-placeholders -- their st_size etc. look
    normal but `f.read()` fails with OSError [Errno 22] "Invalid argument"
    until they get materialized.

    If the direct read works, we yield the original path. If we hit the
    placeholder error, we copy via shutil (which goes through Windows
    file APIs that trigger materialization) into a temp file and yield
    that instead. Temp file is cleaned up on context exit.
    """
    # Probe: try to read a single byte directly.
    try:
        with path.open("rb") as f:
            f.read(1)
        yield path
        return
    except OSError as e:
        # Errno 22 is the iCloud-on-Windows "cloud-only placeholder" symptom.
        # ENOENT, PermissionError, etc. should propagate untouched.
        if e.errno != 22:
            raise

    print(f"  [tiramisu] {path.name} is a cloud-only placeholder; "
          f"materializing to local temp...", flush=True)

    tmp = tempfile.NamedTemporaryFile(suffix=path.suffix, delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        try:
            shutil.copy2(str(path), str(tmp_path))
        except OSError:
            try:
                tmp_path.unlink()
            except Exception:
                pass
            raise OSError(
                f"\n  Could not materialize iCloud / OneDrive placeholder:\n"
                f"    {path}\n"
                f"  Fix: in File Explorer, right-click the file and choose\n"
                f"  'Always keep on this device'. Wait for the green check,\n"
                f"  then re-run the same command.\n"
            )
        yield tmp_path
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with _accessible_path(path) as p:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


def _load_hash_cache() -> dict[str, str]:
    if not LIBRARY_HASH_DB.exists():
        return {}
    try:
        return json.loads(LIBRARY_HASH_DB.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_hash_cache(cache: dict[str, str]) -> None:
    LIBRARY_HASH_DB.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_HASH_DB.write_text(json.dumps(cache, indent=2), encoding="utf-8")


INGEST_PROMPT = """\
You are Cannoli, the Tiramisu researcher. You're skimming a document the user
has added to their library. Your job is to extract SHORT, SPECIFIC, ACTIONABLE
insights that would improve Tiramisu's steering docs.

Tiramisu's steering docs:
  - engineering-principles.md  (universal design rules)
  - code-style.md              (per-language style)
  - communication-style.md     (commit / review tone)
  - agents/<name>.md           (per-agent persona, voice, role)

Document name: {name}
Document type: {kind}

For each insight worth proposing, output:

### <one-line insight title>
**Relevance:** <1-5, where 5 = ship today, 1 = ignore>
**Cite:** <chapter / section / page if you can identify it>
**Idea:** <2-3 sentence summary of what the document argues>
**Proposed update:** <exact file + section + new text in a fenced block>

Rules:
- Only propose updates that meaningfully improve Tiramisu. Aim for 1-4
  high-quality insights, not a wall of mediocre ones.
- If nothing is worth proposing, output just: "No actionable insights from
  this document." and stop. That's a respectable answer.
- Cite the source -- chapter, section, or page number. Don't make claims
  without grounding.
- Never propose changes that would conflict with the "learn before mutate"
  rule in CLAUDE.md §4.3.
"""


PDF_SPLIT_PAGE_TARGET = 80        # API limit is ~100 pages; 80 leaves headroom
PDF_SPLIT_SIZE_TARGET = 25 * 1024 * 1024   # 30 MB API limit; 25 MB headroom


def _pdf_page_count(path: Path) -> int | None:
    """Return total pages via pypdf. None if pypdf missing or PDF unreadable."""
    try:
        from pypdf import PdfReader
        with _accessible_path(path) as p:
            return len(PdfReader(str(p)).pages)
    except ImportError:
        return None
    except Exception as e:
        print(f"[cannoli] could not read page count for {path.name}: {e}",
              file=sys.stderr)
        return None


def _split_pdf(path: Path, max_pages: int = PDF_SPLIT_PAGE_TARGET) -> list[Path]:
    """
    Split a PDF into chunks of <= max_pages pages each. Writes chunks to a
    temp subdir under RESEARCH_DIR and returns the chunk paths.

    If pypdf isn't installed, returns []. Caller should handle that case.
    If the PDF is already small enough, returns [path] (no split needed).
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return []

    try:
        with _accessible_path(path) as local:
            reader = PdfReader(str(local))
            total = len(reader.pages)
            if total <= max_pages and local.stat().st_size <= PDF_SPLIT_SIZE_TARGET:
                # Small enough; yield the materialized copy so downstream reads
                # don't re-hit the cloud-only error. But the temp file will be
                # cleaned up when we exit this context -- so we need to copy
                # it out to a stable location for the caller.
                # Simplest: copy to split_dir even though it's "one chunk".
                if local is path:
                    return [path]
                # local is a temp; promote it so it survives the context exit
                split_dir = RESEARCH_DIR / "_split_tmp"
                split_dir.mkdir(parents=True, exist_ok=True)
                stable = split_dir / f"{path.stem}__local.pdf"
                shutil.copy2(str(local), str(stable))
                return [stable]

            split_dir = RESEARCH_DIR / "_split_tmp"
            split_dir.mkdir(parents=True, exist_ok=True)

            chunk_count = (total + max_pages - 1) // max_pages
            chunks: list[Path] = []
            for i in range(chunk_count):
                start = i * max_pages
                end   = min(start + max_pages, total)

                writer = PdfWriter()
                for page_idx in range(start, end):
                    writer.add_page(reader.pages[page_idx])

                chunk_name = f"{path.stem}__part{i+1}of{chunk_count}.pdf"
                chunk_path = split_dir / chunk_name
                try:
                    with chunk_path.open("wb") as f:
                        writer.write(f)
                    chunks.append(chunk_path)
                except Exception as e:
                    print(f"[cannoli] failed to write chunk {chunk_name}: {e}",
                          file=sys.stderr)

            return chunks
    except Exception as e:
        print(f"[cannoli] could not open {path.name} for splitting: {e}",
              file=sys.stderr)
        return []


def _ingest_pdf_chunk(chunk_path: Path, display_name: str,
                      chunk_label: str = "") -> str:
    """Send one PDF chunk to Anthropic as a document block. Returns section text."""
    with _accessible_path(chunk_path) as p:
        pdf_b64 = base64.standard_b64encode(p.read_bytes()).decode("ascii")

    from llm import _client, DEFAULT_MODEL, _log_api_usage
    chunk_note = (f"\n\n(This is {chunk_label} of a larger document. "
                  f"Cite chapter/section, not absolute page numbers from "
                  f"the full book.)") if chunk_label else ""

    try:
        client = _client()
        resp = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": INGEST_PROMPT.format(
                            name=display_name, kind="PDF"
                        ) + chunk_note,
                    },
                ],
            }],
        )
        _log_api_usage(resp.usage, DEFAULT_MODEL)
        return resp.content[0].text.strip()
    except Exception as e:
        return (f"### {display_name}\n"
                f"**Relevance:** n/a\n\n"
                f"Ingestion failed: {type(e).__name__}: {e}\n")


def _ingest_pdf(path: Path) -> str:
    """
    Ingest a PDF, auto-splitting if it exceeds the API's per-request limits
    (~100 pages / 30 MB). Splitting requires pypdf; if pypdf isn't installed
    and the PDF is too big, we return a helpful message instead of trying.
    """
    size  = path.stat().st_size
    pages = _pdf_page_count(path)

    needs_split = (
        size > PDF_SPLIT_SIZE_TARGET
        or (pages is not None and pages > PDF_SPLIT_PAGE_TARGET)
    )

    if not needs_split:
        return _ingest_pdf_chunk(path, path.name)

    # Try to split
    chunks = _split_pdf(path)
    if not chunks:
        # pypdf missing
        return (f"### {path.name}\n"
                f"**Relevance:** n/a\n\n"
                f"PDF is {size / 1024 / 1024:.1f} MB"
                + (f", {pages} pages" if pages else "")
                + " -- too large for one API call, and pypdf isn't available "
                "to auto-split. Install with `pip install pypdf` or split "
                "the file manually and retry.\n")

    if len(chunks) == 1:
        # _split_pdf decided no split was actually needed
        return _ingest_pdf_chunk(chunks[0], path.name)

    print(f"  ↳ splitting {path.name} into {len(chunks)} chunks "
          f"(~{PDF_SPLIT_PAGE_TARGET} pages each)", flush=True)

    sections: list[str] = []
    for i, chunk in enumerate(chunks):
        label = f"part {i+1}/{len(chunks)}"
        print(f"     ingesting {label}", flush=True)
        sections.append(_ingest_pdf_chunk(chunk, f"{path.name} ({label})", label))
        try:
            chunk.unlink()
        except Exception:
            pass

    # Try to clean up the split dir if empty
    try:
        split_dir = chunks[0].parent
        if not any(split_dir.iterdir()):
            split_dir.rmdir()
    except Exception:
        pass

    # Aggregate: each chunk produced one or more ### sections.
    combined = "\n\n---\n\n".join(sections)
    header = (f"### {path.name}\n"
              f"_Auto-split into {len(chunks)} chunks of ~{PDF_SPLIT_PAGE_TARGET} pages each. "
              f"Findings below are aggregated across all chunks._\n\n")
    return header + combined


def _ingest_text_file(path: Path) -> str:
    """Read a text-based file (.md, .txt, .rst) and summarize via Sonnet."""
    try:
        with _accessible_path(path) as p:
            text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"### {path.name}\n**Relevance:** n/a\n\nCould not read: {e}\n"

    if not text.strip():
        return ""  # empty file -- skip silently

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n\n[truncated -- {len(text)} chars total]"

    try:
        return invoke(
            prompt=INGEST_PROMPT.format(name=path.name, kind=path.suffix[1:].upper())
                   + "\n\n---\n\n" + text,
            model=DEFAULT_MODEL,
            max_tokens=1200,
            temperature=0.2,
        ).strip()
    except Exception as e:
        return f"### {path.name}\n**Relevance:** n/a\n\nText ingestion failed: {e}\n"


def _ingest_file(path: Path) -> str | None:
    """Dispatch on extension. Returns the section text, or None if skipped."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _ingest_pdf(path)
    if ext in {".md", ".markdown", ".txt", ".rst"}:
        return _ingest_text_file(path)
    return None


def _enumerate_library_files() -> list[Path]:
    """All ingestible files from both library locations (user + per-repo)."""
    files: list[Path] = []
    for root in (USER_LIBRARY, repo_library_path()):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.is_file() and f.suffix.lower() in INGESTIBLE_EXTS:
                files.append(f)
    return files


def ingest_library(quiet: bool = False, force: bool = False) -> Path | None:
    """
    Walk the user + per-repo library dirs, ingest any file whose hash has
    changed since the last run (or all of them if force=True). Writes a
    findings file with proposed steering updates.
    """
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    files = _enumerate_library_files()
    if not files:
        log("\n🐶 Cannoli: library is empty (no PDFs / .md / .txt / .rst found).")
        log(f"   Drop files into {USER_LIBRARY} to start.\n")
        return None

    cache    = _load_hash_cache() if not force else {}
    sections = []
    changed  = 0

    log(f"\n🐶 Cannoli is reading the library ({len(files)} file(s))...\n")

    for f in files:
        key = str(f.resolve())
        current = _file_sha256(f)
        if cache.get(key) == current:
            log(f"  ↳ unchanged: {f.name}")
            continue
        log(f"  ↳ ingesting: {f.name}  ({f.stat().st_size / 1024:.0f} KB)")
        section = _ingest_file(f)
        if section:
            sections.append(f"## {f.name}\n_Source: {f.parent}_\n\n{section}")
            cache[key] = current
            changed += 1

    _save_hash_cache(cache)

    if changed == 0:
        log("\n  No new or changed files -- nothing to ingest.\n")
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"findings_library_{today}.md"
    body = (
        f"# Cannoli library findings -- {today}\n\n"
        f"Proposed updates from {changed} file(s) in your library. "
        f"**Nothing is auto-applied.** Copy what's worth keeping into the "
        f"steering files by hand.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    out_path.write_text(body, encoding="utf-8")
    log(f"\n✓ Library findings written to: {out_path}\n")
    return out_path


def ingest_path_cli(path_str: str, quiet: bool = False) -> None:
    """Manual one-shot: ingest a specific file or directory NOW.
    Does not use the hash cache -- always processes the target."""
    target = Path(path_str).expanduser().resolve()
    if not target.exists():
        print(f"[cannoli] not found: {target}")
        sys.exit(1)

    if target.is_file():
        if target.suffix.lower() not in INGESTIBLE_EXTS:
            print(f"[cannoli] unsupported file type: {target.suffix}")
            print(f"  Supported: {', '.join(sorted(INGESTIBLE_EXTS))}")
            sys.exit(1)
        files = [target]
    else:
        files = [
            f for f in target.rglob("*")
            if f.is_file() and f.suffix.lower() in INGESTIBLE_EXTS
        ]

    if not files:
        print(f"[cannoli] no ingestible files under: {target}")
        return

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    sections = []
    print(f"\n🐶 Cannoli is ingesting {len(files)} file(s) from {target}...\n")
    for f in files:
        print(f"  ↳ {f.name}  ({f.stat().st_size / 1024:.0f} KB)")
        section = _ingest_file(f)
        if section:
            sections.append(f"## {f.name}\n_Source: {f.parent}_\n\n{section}")

    if not sections:
        print("\n  No content extracted.\n")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"findings_library_{today}.md"
    body = (
        f"# Cannoli library findings -- {today}  (manual ingest)\n\n"
        + "\n\n".join(sections) + "\n"
    )
    # Append if file already exists from a scheduled run today
    if out_path.exists():
        out_path.write_text(out_path.read_text(encoding="utf-8") + "\n\n" + body,
                            encoding="utf-8")
    else:
        out_path.write_text(body, encoding="utf-8")
    print(f"\n✓ Written: {out_path}\n")


SCOUT_PROMPT = """\
You are Cannoli scouting a library of technical books for relevance to
Tiramisu, a CLI for AI-assisted dev workflows. Tiramisu cares about:
code review, prompt engineering, agent design, Python idioms, software
architecture, distributed systems, testing.

Below is a numbered list of books (filename only, plus the parent folder
category). For each, rate it 1-5:
  5 = definitely worth ingesting; concrete patterns we'd adopt
  4 = highly relevant; specific techniques apply
  3 = somewhat relevant; might surface a useful idea
  2 = tangential; would skip
  1 = irrelevant; not about what Tiramisu cares about

Output one line per book, EXACTLY in this format:
  N: relevance=K <brief reason, max 10 words>

Where N is the input number. Examples:
  3: relevance=5 canonical software design book; ingest now
  7: relevance=2 dated Java 1.4 enterprise patterns
  12: relevance=1 graphics programming, off-topic

BOOKS:
{batch}

Output (one line per book, in input order):"""


def scout_library(path: Path, batch_size: int = 50,
                  max_results: int = 30, quiet: bool = False) -> Path | None:
    """
    Scan a directory tree for PDFs and rank each by filename relevance to
    Tiramisu. CHEAP -- only filenames sent to Haiku, no PDF reads. Cost
    scales linearly with batches: ~$0.01 per 50 books.

    Writes a scout_YYYY-MM-DD.md file with top-N candidates ranked, each
    with a paste-able `t research ingest "<path>"` command.
    """
    import re as _re

    if not path.exists():
        print(f"[scout] path not found: {path}")
        return None
    if not path.is_dir():
        print(f"[scout] not a directory: {path}")
        return None

    def log(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    log(f"\n🐶 Cannoli is scouting: {path}\n")
    log("  Enumerating PDFs (no API calls yet)...")

    pdfs: list[dict] = []
    for f in path.rglob("*.pdf"):
        try:
            size = f.stat().st_size
        except Exception:
            continue
        try:
            rel = f.relative_to(path)
            category = rel.parts[0] if len(rel.parts) > 1 else "uncategorized"
        except Exception:
            category = "uncategorized"
        pdfs.append({
            "name":     f.name,
            "path":     str(f),
            "category": category,
            "size_mb":  size / 1024 / 1024,
        })

    if not pdfs:
        log(f"  No PDFs found.")
        return None

    log(f"  Found {len(pdfs)} PDF(s). Scoring in batches of {batch_size}...")
    n_batches = (len(pdfs) + batch_size - 1) // batch_size
    log(f"  Estimated {n_batches} Haiku call(s), ~${n_batches * 0.007:.2f} total.\n")

    scored: list[dict] = []
    for i in range(0, len(pdfs), batch_size):
        batch = pdfs[i:i + batch_size]
        batch_no = i // batch_size + 1
        log(f"  ↳ batch {batch_no}/{n_batches}  ({len(batch)} books)")

        listing = "\n".join(
            f"{j + 1}. [{p['category']}] {p['name']} ({p['size_mb']:.1f} MB)"
            for j, p in enumerate(batch)
        )

        try:
            response = invoke(
                prompt=SCOUT_PROMPT.format(batch=listing),
                model=FAST_MODEL,
                max_tokens=1500,
                temperature=0.1,
            )
        except Exception as e:
            log(f"     [warn] batch failed: {e}")
            continue

        # Parse lines: "N: relevance=K reason"
        pattern = _re.compile(r"^\s*(\d+)\s*:\s*relevance\s*=\s*([1-5])\s*(.*)$",
                              _re.IGNORECASE)
        seen_idx = set()
        for line in response.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if idx in seen_idx or idx < 0 or idx >= len(batch):
                continue
            seen_idx.add(idx)
            scored.append({
                **batch[idx],
                "score":  int(m.group(2)),
                "reason": m.group(3).strip(),
            })

    if not scored:
        log("\n  No books were scored. Check that FAST_MODEL is reachable.")
        return None

    # Rank: highest score first; ties broken by smaller size (cheaper to ingest)
    scored.sort(key=lambda p: (-p["score"], p["size_mb"]))
    top = scored[:max_results]

    # Aggregate stats by score for the summary
    by_score: dict[int, int] = {}
    for p in scored:
        by_score[p["score"]] = by_score.get(p["score"], 0) + 1

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = RESEARCH_DIR / f"scout_{today}.md"
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Library scout -- {today}",
        f"",
        f"**Scanned:** `{path}`",
        f"**Total PDFs:** {len(pdfs)}",
        f"**Scored:** {len(scored)} (in {n_batches} Haiku batch{'es' if n_batches != 1 else ''})",
        f"**Score distribution:** "
        + ", ".join(f"{s}/5 → {by_score.get(s, 0)}" for s in (5, 4, 3, 2, 1)),
        f"",
        f"Top {len(top)} candidates ranked below. Pick what's worth ingesting "
        f"and paste the `Ingest command` for each. Books rated 4-5 are usually "
        f"worth your attention; below 3 is probably noise.",
        f"",
        f"---",
        f"",
    ]
    for p in top:
        lines.append(f"## {p['name']}")
        lines.append(f"**Relevance:** {p['score']}/5  &nbsp; **Category:** {p['category']}"
                     f"  &nbsp; **Size:** {p['size_mb']:.1f} MB")
        if p.get("reason"):
            lines.append(f"**Why:** {p['reason']}")
        lines.append("")
        lines.append(f"**Ingest command:** `t research ingest \"{p['path']}\"`")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    log(f"\n✓ Scout report written to: {out_path}")
    log(f"  Score breakdown: " +
        ", ".join(f"{s}/5={by_score.get(s, 0)}" for s in (5, 4, 3, 2, 1)))
    log(f"\n  Open the file or run `t research show-scout` to see the top "
        f"{max_results} ranked candidates.\n")

    return out_path


def show_latest_scout() -> None:
    """Render the newest scout_*.md via rich Markdown."""
    files = sorted(
        RESEARCH_DIR.glob("scout_*.md") if RESEARCH_DIR.exists() else [],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not files:
        print("\nNo scout report yet. Run `t research scout <path>` first.\n")
        return
    text = files[0].read_text(encoding="utf-8")
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console().print(Markdown(text))
    except Exception:
        print(text)


def library_list() -> None:
    """Show what's in the library, with hash-cache status."""
    cache = _load_hash_cache()
    files = _enumerate_library_files()
    if not files:
        print(f"\nLibrary is empty.")
        print(f"  User dir: {USER_LIBRARY}")
        repo = repo_library_path()
        print(f"  Repo dir: {repo}  ({'exists' if repo.exists() else 'not created'})")
        print(f"\n  Drop .pdf / .md / .txt / .rst files into either to have "
              f"Cannoli read them weekly.\n")
        return
    print(f"\nLibrary ({len(files)} file(s)):\n")
    for f in files:
        key = str(f.resolve())
        ingested = "ingested" if cache.get(key) == _file_sha256(f) else "PENDING"
        size_kb  = f.stat().st_size / 1024
        loc      = "user" if f.is_relative_to(USER_LIBRARY) else "repo"
        print(f"  [{ingested:8}]  ({loc})  {f.name}  ({size_kb:.0f} KB)")
    print()


# -------- sources management --------

def _write_user_sources(sources: list[dict]) -> None:
    """Save the user's sources.json. Creates parent dir if needed."""
    path = user_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, indent=2), encoding="utf-8")


def sources_list() -> None:
    """Print the active sources for this run, with their origin."""
    user_path = user_sources_path()
    repo_path = repo_sources_path()

    if user_path.exists():
        origin = f"user file ({user_path})"
    else:
        origin = "hardcoded defaults (no user file yet)"

    base_sources = _load_json_list(user_path) or list(DEFAULT_SOURCES)
    repo_extra   = _load_json_list(repo_path) or []

    print(f"\nActive sources ({len(base_sources) + len(repo_extra)} total)\n")

    if base_sources:
        print(f"From {origin}:")
        for i, s in enumerate(base_sources, 1):
            print(f"  {i}. {s['name']}")
            print(f"     url:   {s['url']}")
            focus = s.get("focus", "")
            if focus:
                wrapped = (focus[:140] + "...") if len(focus) > 140 else focus
                print(f"     focus: {wrapped}")
        print()

    if repo_extra:
        print(f"From repo file ({repo_path}):")
        for i, s in enumerate(repo_extra, 1):
            print(f"  +{i}. {s['name']}  ({s['url']})")
        print()

    if not user_path.exists():
        print("To customize: `t research sources reset` writes the defaults to "
              "your user file so you can edit them.\n")


def sources_add(url: str, name: str | None, focus: str | None) -> None:
    """Append a source to the user's sources.json. Creates from defaults if missing."""
    if not url:
        print("Usage: t research sources add <url> [name] [focus]")
        sys.exit(1)

    name = name or url
    focus = focus or "general updates worth watching"

    current = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)

    # Don't add duplicates by URL
    if any(s["url"] == url for s in current):
        print(f"  source with url={url!r} already present; not added.")
        return

    current.append({"name": name, "url": url, "focus": focus})
    _write_user_sources(current)
    print(f"\n  added: {name}")
    print(f"          {url}")
    print(f"  total: {len(current)} source(s) in {user_sources_path()}\n")


def sources_remove(name_or_url: str) -> None:
    """Remove a source by name or URL match."""
    if not name_or_url:
        print("Usage: t research sources remove <name-or-url>")
        sys.exit(1)

    current = _load_json_list(user_sources_path()) or list(DEFAULT_SOURCES)
    needle = name_or_url.lower()

    remaining = [
        s for s in current
        if needle not in s["name"].lower() and needle not in s["url"].lower()
    ]

    if len(remaining) == len(current):
        print(f"  no source matched {name_or_url!r}; nothing removed.")
        return

    _write_user_sources(remaining)
    print(f"\n  removed {len(current) - len(remaining)} source(s) "
          f"matching {name_or_url!r}.")
    print(f"  total now: {len(remaining)} source(s) in {user_sources_path()}\n")


def sources_reset() -> None:
    """Write DEFAULT_SOURCES to the user file so they can edit from a clean baseline."""
    _write_user_sources(list(DEFAULT_SOURCES))
    print(f"\n  wrote {len(DEFAULT_SOURCES)} default source(s) to "
          f"{user_sources_path()}.")
    print("  Edit that file freely; or run `t research sources add <url>` to extend.\n")


def sources_show() -> None:
    """Dump the raw user sources.json (for grep / copy)."""
    path = user_sources_path()
    if path.exists():
        print(path.read_text(encoding="utf-8"))
    else:
        print("# No user sources.json yet -- using hardcoded defaults.")
        print(json.dumps(DEFAULT_SOURCES, indent=2))


# -------- CLI --------

def _sources_subcmd(rest: list[str]) -> None:
    """Dispatch `t research sources ...` subcommands."""
    if not rest or rest[0] == "list":
        sources_list()
    elif rest[0] == "add":
        url   = rest[1] if len(rest) > 1 else None
        name  = rest[2] if len(rest) > 2 else None
        focus = rest[3] if len(rest) > 3 else None
        sources_add(url, name, focus)
    elif rest[0] == "remove":
        sources_remove(rest[1] if len(rest) > 1 else None)
    elif rest[0] == "reset":
        sources_reset()
    elif rest[0] == "show":
        sources_show()
    else:
        print(f"Unknown sources action: {rest[0]!r}")
        print("Valid: list | add <url> [name] [focus] | remove <name-or-url> | reset | show")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Cannoli -- autonomous research")
    parser.add_argument("action", nargs="?", default="show")
    parser.add_argument("rest", nargs=argparse.REMAINDER,
                        help="Trailing args for subcommands (e.g. sources add <url>)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output (for background runs)")
    args = parser.parse_args()

    if args.action == "show":
        show_latest()
    elif args.action == "run":
        run_research(quiet=args.quiet)
    elif args.action == "discover":
        discover(quiet=args.quiet)
    elif args.action == "ingest":
        if not args.rest:
            print("Usage: t research ingest <file-or-directory>")
            sys.exit(1)
        ingest_path_cli(args.rest[0], quiet=args.quiet)
    elif args.action == "grab":
        if not args.rest:
            print("Usage:")
            print("  t research grab <arxiv-id>    -- pull one paper")
            print("  t research grab --all         -- pull every paper from")
            print("                                   the latest candidates file")
            sys.exit(1)
        target = args.rest[0]
        if target in ("--all", "-a", "all"):
            grab_all_from_latest()
        else:
            grab_paper(target)
    elif args.action == "library":
        library_list()
    elif args.action == "scout":
        if not args.rest:
            print("Usage: t research scout <path-to-library>")
            print("  Scans the directory for PDFs, ranks each by relevance.")
            print("  Cheap (filename-only scoring). ~$0.01 per 50 books.")
            sys.exit(1)
        scout_library(Path(args.rest[0]).expanduser(), quiet=args.quiet)
    elif args.action in ("show-scout", "scout-show"):
        show_latest_scout()
    elif args.action == "all":
        run_research(quiet=args.quiet)
        discover(quiet=args.quiet)
        ingest_library(quiet=args.quiet)
    elif args.action == "mute":
        mute_all_pending()
    elif args.action == "list":
        list_all()
    elif args.action == "sources":
        _sources_subcmd(args.rest)
    else:
        print(f"Unknown action: {args.action!r}")
        print("Valid: show | run | discover | grab <id|--all> | "
              "ingest <path> | scout <path> | show-scout | library | "
              "all | mute | list | sources [...]")
        sys.exit(1)


if __name__ == "__main__":
    main()
