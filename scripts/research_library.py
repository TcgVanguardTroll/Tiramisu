"""
Cannoli's local-library ingestion + scout.

Drop files into ~/.tiramisu/library/ (user-wide) or <repo>/.tiramisu/library/
(project-specific) and Cannoli will read them on the weekly background run,
skipping unchanged files via content-hash cache.

Supported file types:
  .pdf  -- sent to Anthropic API as a document block (native PDF support)
  .md / .txt / .rst -- sent as text

Limits:
  - PDFs over ~100 pages or 32MB will fail at the API boundary; auto-split
    via pypdf handles that (see _ingest_pdf).
  - Each file gets DEFAULT_MODEL -- quality matters more than speed because
    we only re-process when content changes.
"""
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, DEFAULT_MODEL, FAST_MODEL
from research_common import RESEARCH_DIR, USER_LIBRARY

LIBRARY_HASH_DB = RESEARCH_DIR / "library_hashes.json"
INGESTIBLE_EXTS = {".pdf", ".md", ".markdown", ".txt", ".rst"}
MAX_TEXT_CHARS  = 80000   # ~20k tokens, well under any model limit


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

    from llm import _client, _log_api_usage
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
    """Read a text-based file (.md, .txt, .rst) and summarize via DEFAULT_MODEL."""
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
