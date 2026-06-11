"""
Cannoli's open discovery -- GitHub Trending + HackerNews + arxiv.

The exploration layer that complements the curated watched sources in
research.py. No API keys required -- all endpoints are free public APIs.

Output goes to ~/.tiramisu/.research/candidates_YYYY-MM-DD.md, separate from
findings_*.md. Candidates are URLs the user might want to ADD as permanent
watched sources (via `t research sources add <url>`), not deltas from
existing watched sources. `grab` pulls an arxiv PDF into the library so the
ingestion layer (research_library.py) picks it up on the next run.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL
from research_common import (
    TIRAMISU_HOME, RESEARCH_DIR, HTTP_TIMEOUT, USER_LIBRARY,
    _ssl_context, _fetch,
)

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
    """Path of the arxiv subdir under the user library."""
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
