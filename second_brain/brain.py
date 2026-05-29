"""
second_brain/brain.py — Core library for local knowledge management.
Ingest → Chunk → Embed → Index → Retrieve → Synthesize
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import chromadb
import frontmatter
from sentence_transformers import SentenceTransformer

from type_config import (CHUNK_PARAMS, DEFAULT_CHUNK, TYPE_HINTS,
                         extract_type_metadata, _extract_tables)
from reranker import rerank_with_decay, personalized_rerank
from user_profile import load_profile, expand_query, get_profile_embedding
from index_personalization import tag_relevance, adjusted_chunk_params, extract_enhanced_metadata

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EMBED_MODEL = "all-MiniLM-L6-v2"
CHROMA_DIR = ".second_brain_index"
COLLECTION = "documents"
CHUNK_SIZE = 512  # chars
CHUNK_OVERLAP = 80  # chars
TOP_K = 5
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

logger = logging.getLogger(__name__)

# M4: Guard against infinite loop if overlap >= chunk size — clamp step to at least 1
if CHUNK_OVERLAP >= CHUNK_SIZE:
    logger.warning("CHUNK_OVERLAP (%d) >= CHUNK_SIZE (%d), clamping overlap to %d",
                   CHUNK_OVERLAP, CHUNK_SIZE, CHUNK_SIZE - 1)
    CHUNK_OVERLAP = CHUNK_SIZE - 1

# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

def parse_markdown(filepath: str) -> dict:
    """Parse a markdown file, extracting frontmatter metadata and body."""
    path = Path(filepath)
    if path.stat().st_size > MAX_FILE_SIZE:
        logger.warning("Skipping oversized file: %s", filepath)
        return {"metadata": {}, "body": "", "path": str(path)}
    try:
        post = frontmatter.load(str(path))
    except Exception:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"metadata": {}, "body": text, "path": str(path)}

    meta = dict(post.metadata)
    # Normalize date to string
    if "date" in meta:
        d = meta["date"]
        if isinstance(d, datetime):
            meta["date"] = d.strftime("%Y-%m-%d")
        else:
            meta["date"] = str(d)
    # Normalize participants to comma-separated lowercase string
    if "participants" in meta:
        p = meta["participants"]
        if isinstance(p, list):
            meta["participants"] = ",".join(s.strip().lower() for s in p)
        else:
            meta["participants"] = str(p).lower()
    # Normalize tags
    if "tags" in meta:
        t = meta["tags"]
        if isinstance(t, list):
            meta["tags"] = ",".join(str(s).strip().lower() for s in t)
        else:
            meta["tags"] = str(t).lower()
    if "type" in meta:
        val = meta["type"]
        # YAML parses "1:1" as sexagesimal int 61 — fix it
        if val == 61 or str(val) == "61":
            meta["type"] = "1:1"
        else:
            meta["type"] = str(val).lower()

    return {"metadata": meta, "body": post.content, "path": str(path)}


# ---------------------------------------------------------------------------
# Markdown-aware chunking
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _build_meta(metadata: dict, filepath: str, header: str) -> dict:
    return {
        "source": filepath,
        "section": header,
        "date": metadata.get("date", ""),
        "participants": metadata.get("participants", ""),
        "tags": metadata.get("tags", ""),
        "type": metadata.get("type", ""),
    }


def _split_on_headers(body: str) -> list[tuple]:
    sections = []
    last_end = 0
    current_header = ""
    for m in _HEADER_RE.finditer(body):
        if last_end < m.start():
            sections.append((current_header, body[last_end:m.start()].strip()))
        current_header = m.group(2).strip()
        last_end = m.end()
    if last_end < len(body):
        sections.append((current_header, body[last_end:].strip()))
    if not sections:
        sections = [("", body.strip())]
    return sections


def _subsplit(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts = []
    start = 0
    step = max(1, size - overlap)
    while start < len(text):
        parts.append(text[start:start + size])
        start += step
    return parts


def chunk_markdown(body: str, metadata: dict, filepath: str, profile: dict = None) -> list[dict]:
    """Split markdown into chunks using type-specific params, with profile-aware enrichment."""
    doc_type = metadata.get("type", "")
    params = CHUNK_PARAMS.get(doc_type, DEFAULT_CHUNK)
    fm = metadata  # frontmatter dict for metadata extraction

    # Check doc-level relevance to decide chunk sizing
    doc_relevance, doc_rel_score = "", 0.0
    if profile:
        doc_relevance, doc_rel_score = tag_relevance(body, profile)
        if doc_relevance and doc_rel_score > 0.1:
            # Find project priority and adjust chunk size
            for proj in profile.get("active_projects", []):
                if proj["name"] == doc_relevance:
                    params = adjusted_chunk_params(params, proj.get("priority", "low"))
                    break

    # No-split types with size guard
    if params["size"] is None:
        max_size = params.get("max_size", 1500)
        if len(body.strip()) <= max_size:
            meta = _build_meta(metadata, filepath, "")
            meta.update(extract_type_metadata(body, doc_type, fm))
            _apply_relevance(meta, body, profile, doc_relevance, doc_rel_score)
            return [{"text": body.strip(), "metadata": meta}]
        params = DEFAULT_CHUNK  # fallback

    sections = _split_on_headers(body)
    chunks = []
    for header, text in sections:
        if not text.strip():
            continue
        # Table preservation for ops-review
        if doc_type == "ops-review":
            table_chunks, non_table_text = _extract_tables(text)
        else:
            table_chunks, non_table_text = [], text

        for tbl in table_chunks:
            meta = _build_meta(metadata, filepath, header)
            meta.update(extract_type_metadata(tbl, doc_type, fm))
            _apply_relevance(meta, tbl, profile, doc_relevance, doc_rel_score)
            chunks.append({"text": tbl, "metadata": meta})

        if non_table_text.strip():
            parts = _subsplit(non_table_text.strip(), params["size"], params["overlap"])
            for part in parts:
                if not part.strip():
                    continue
                meta = _build_meta(metadata, filepath, header)
                meta.update(extract_type_metadata(part, doc_type, fm))
                _apply_relevance(meta, part, profile, doc_relevance, doc_rel_score)
                chunks.append({"text": part.strip(), "metadata": meta})
    return chunks


def _apply_relevance(meta: dict, text: str, profile: dict,
                     doc_relevance: str, doc_rel_score: float):
    """Tag chunk with relevance and enhanced metadata if profile exists."""
    if not profile:
        meta["relevance"] = ""
        meta["relevance_score"] = 0.0
        return
    # Per-chunk relevance (may differ from doc-level for multi-topic docs)
    rel, score = tag_relevance(text, profile)
    if not rel:
        rel, score = doc_relevance, doc_rel_score * 0.5  # inherit doc-level at half weight
    meta["relevance"] = rel
    meta["relevance_score"] = score
    # Enhanced metadata for relevant chunks
    if rel and score > 0.1:
        meta.update(extract_enhanced_metadata(text, profile))


# ---------------------------------------------------------------------------
# Content hashing for incremental indexing
# ---------------------------------------------------------------------------

def file_hash(filepath: str):
    """Return SHA-256 hash of file contents, or None if file is oversized."""
    if Path(filepath).stat().st_size > MAX_FILE_SIZE:
        print(f"Skipping oversized file: {filepath}", file=sys.stderr)
        return None
    return hashlib.sha256(Path(filepath).read_bytes()).hexdigest()


def load_hash_index(index_dir: str) -> dict:
    p = Path(index_dir) / "file_hashes.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_hash_index(index_dir: str, hashes: dict):
    p = Path(index_dir) / "file_hashes.json"
    p.write_text(json.dumps(hashes, indent=2))


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def get_collection(index_dir: str):
    client = chromadb.PersistentClient(path=index_dir)
    col = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL},
    )
    # Verify model compatibility
    stored_model = (col.metadata or {}).get("embed_model")
    if stored_model and stored_model != EMBED_MODEL:
        logger.warning("Model mismatch: index uses '%s', current is '%s'. Re-index with --force.", stored_model, EMBED_MODEL)
    return col


_model_cache = None

def get_model():
    global _model_cache
    if _model_cache is None:
        _model_cache = SentenceTransformer(EMBED_MODEL)
    return _model_cache


def _batch_delete_sources(collection, sources: list):
    """Delete all chunks for multiple source files in one ChromaDB call."""
    if not sources:
        return
    try:
        if len(sources) == 1:
            existing = collection.get(where={"source": sources[0]})
        else:
            existing = collection.get(where={"source": {"$in": sources}})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        # Fallback to per-file if $in not supported
        for src in sources:
            try:
                existing = collection.get(where={"source": src})
                if existing and existing["ids"]:
                    collection.delete(ids=existing["ids"])
            except Exception:
                pass


def index_files(docs_dir: str, index_dir: str = None, force: bool = False) -> dict:
    """Index markdown files with batched embeddings for performance. Returns stats dict."""
    from concurrent.futures import ThreadPoolExecutor

    docs_path = Path(docs_dir).resolve()
    if index_dir is None:
        index_dir = str(docs_path / CHROMA_DIR)
    os.makedirs(index_dir, exist_ok=True)

    md_files = sorted(
        f for ext in ("*.md", "*.txt") for f in docs_path.rglob(ext)
        if "archive" not in f.parts
    )
    if not md_files:
        return {"total": 0, "indexed": 0, "skipped": 0, "chunks": 0}

    hashes = load_hash_index(index_dir)
    model = get_model()
    collection = get_collection(index_dir)
    profile = load_profile()

    stats = {"total": len(md_files), "indexed": 0, "skipped": 0, "chunks": 0}

    # Phase 1: Hash check + parse + chunk in parallel (CPU-bound, no GIL issue for I/O)
    pending = []  # (filepath_str, hash, chunks)

    def _parse_one(fp):
        fp_str = str(fp)
        h = file_hash(fp_str)
        if h is None:
            return None
        if not force and hashes.get(fp_str) == h:
            return None
        doc = parse_markdown(fp_str)
        chunks = chunk_markdown(doc["body"], doc["metadata"], fp_str, profile)
        if not chunks:
            return (fp_str, fp.stem, h, [])
        return (fp_str, fp.stem, h, chunks)

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
        results = list(pool.map(_parse_one, md_files))

    for result in results:
        if result is None:
            stats["skipped"] += 1
        elif not result[3]:
            # File parsed but produced no chunks
            hashes[result[0]] = result[2]
            stats["skipped"] += 1
        else:
            pending.append(result)

    if not pending:
        # Still need to clean up deleted files
        current_files = {str(fp) for fp in md_files}
        deleted = [fp for fp in list(hashes.keys()) if fp not in current_files]
        if deleted:
            _batch_delete_sources(collection, deleted)
            for fp in deleted:
                del hashes[fp]
        save_hash_index(index_dir, hashes)
        return stats

    # Phase 2: Batch delete old chunks for all pending files in one pass
    sources_to_delete = [fp_str for fp_str, _, _, _ in pending]
    _batch_delete_sources(collection, sources_to_delete)

    # Phase 3: Batch all embeddings in one call (major speedup — single GPU/SIMD pass)
    all_texts = []
    file_boundaries = []  # (start_idx, end_idx) per file
    for fp_str, stem, h, chunks in pending:
        start = len(all_texts)
        all_texts.extend(c["text"] for c in chunks)
        file_boundaries.append((start, len(all_texts)))

    all_embeddings = model.encode(all_texts, show_progress_bar=False, batch_size=64).tolist()

    # Phase 4: Write to ChromaDB per file
    for i, (fp_str, stem, h, chunks) in enumerate(pending):
        start, end = file_boundaries[i]

        texts = [c["text"] for c in chunks]
        metas = [c["metadata"] for c in chunks]
        ids = [f"{stem}_{j}_{h[:8]}" for j in range(len(chunks))]
        embeddings = all_embeddings[start:end]

        collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
        hashes[fp_str] = h
        stats["indexed"] += 1
        stats["chunks"] += len(chunks)

        # Also index into FTS5 for keyword search
        _index_fts5(chunks, fp_str)

    # Clean up entries for deleted files
    current_files = {str(fp) for fp in md_files}
    deleted = [fp for fp in list(hashes.keys()) if fp not in current_files]
    if deleted:
        _batch_delete_sources(collection, deleted)
        for fp in deleted:
            _remove_fts5(fp)
            del hashes[fp]

    save_hash_index(index_dir, hashes)
    return stats


def index_single_file(filepath: str, index_dir: str, force: bool = False) -> dict:
    """Index (or re-index) a single markdown file. Returns stats dict."""
    fp = Path(filepath).resolve()
    if not fp.exists() or fp.suffix not in (".md", ".txt"):
        return {"indexed": 0, "skipped": 1, "chunks": 0}

    os.makedirs(index_dir, exist_ok=True)
    hashes = load_hash_index(index_dir)
    fp_str = str(fp)
    h = file_hash(fp_str)
    if h is None:
        return {"indexed": 0, "skipped": 1, "chunks": 0}
    if not force and hashes.get(fp_str) == h:
        return {"indexed": 0, "skipped": 1, "chunks": 0}

    collection = get_collection(index_dir)
    model = get_model()
    profile = load_profile()

    # Remove old chunks
    try:
        existing = collection.get(where={"source": fp_str})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass

    doc = parse_markdown(fp_str)
    chunks = chunk_markdown(doc["body"], doc["metadata"], fp_str, profile)
    if not chunks:
        hashes[fp_str] = h
        save_hash_index(index_dir, hashes)
        return {"indexed": 0, "skipped": 1, "chunks": 0}

    texts = [c["text"] for c in chunks]
    metas = [c["metadata"] for c in chunks]
    ids = [f"{fp.stem}_{i}_{h[:8]}" for i in range(len(chunks))]
    embeddings = model.encode(texts, show_progress_bar=False).tolist()

    collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
    hashes[fp_str] = h
    save_hash_index(index_dir, hashes)
    return {"indexed": 1, "skipped": 0, "chunks": len(chunks)}


def remove_deleted_file(filepath: str, index_dir: str):
    """Remove a deleted file's chunks from the index."""
    fp_str = str(Path(filepath).resolve())
    hashes = load_hash_index(index_dir)
    if fp_str not in hashes:
        return
    collection = get_collection(index_dir)
    try:
        existing = collection.get(where={"source": fp_str})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass
    del hashes[fp_str]
    save_hash_index(index_dir, hashes)


def archive_knowledge_file(filepath: str, docs_dir: str, index_dir: str = None):
    """Move a knowledge file to knowledge/archive/ and remove from index."""
    src = Path(filepath).resolve()
    docs_path = Path(docs_dir).resolve()
    if index_dir is None:
        index_dir = str(docs_path / CHROMA_DIR)
    archive_dir = docs_path / "archive"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / src.name
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")
    src.rename(dest)
    remove_deleted_file(str(src), index_dir)
    return str(dest)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _parse_temporal_query(query: str) -> dict:
    """Extract temporal/participant/type/semantic hints from query."""
    filters = {}
    q_lower = query.lower()

    # Participant extraction
    m = re.search(r"\bwith\s+([a-z][a-z\s]*[a-z])\b", q_lower)
    if m:
        filters["participants"] = m.group(1).strip()

    # Type hints — check extended vocabulary
    for doc_type, keywords in TYPE_HINTS.items():
        if any(kw in q_lower for kw in keywords):
            filters["type"] = doc_type
            break

    # Decision/action detection
    if re.search(r"\b(decide|decided|decision|agree|agreed)\b", q_lower):
        filters["_filter_decisions"] = True
    if re.search(r"\b(action item|todo|to-do|task)\b", q_lower):
        filters["_filter_actions"] = True

    # Section-header theme matching — extract likely topic keywords
    # Strip common query words, keep nouns/topics for section matching
    _STOP = {"what", "did", "we", "i", "the", "a", "an", "in", "my", "last",
             "recent", "latest", "about", "from", "with", "do", "does", "is",
             "are", "was", "were", "has", "have", "been", "discuss", "discussed",
             "decide", "decided", "agree", "agreed", "any", "all", "our"}
    words = [w for w in re.findall(r"[a-z]+", q_lower) if w not in _STOP and len(w) > 2]
    # Remove type hint words already captured
    if "type" in filters:
        type_words = set()
        for kw in TYPE_HINTS.get(filters["type"], []):
            type_words.update(kw.split())
        words = [w for w in words if w not in type_words]
    if words:
        filters["_theme_words"] = words

    # Temporal signals
    if "last" in q_lower or "recent" in q_lower or "latest" in q_lower:
        filters["_sort_recent"] = True

    return filters


DB_PATH = str(Path(__file__).resolve().parent.parent / "tiramisu.db")


def _log_search_hits(items: list, query: str, agent: str = None, is_test: bool = False):
    """Log search hits to kb_hits table. Silently fails to never break search."""
    try:
        import sqlite3
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = sqlite3.connect(DB_PATH, timeout=5)
        try:
            result_count = len(items)
            conn.executemany(
                "INSERT INTO kb_hits (source_file, chunk_id, agent, query, distance, rank, result_count, is_test, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (it["metadata"].get("source", ""), it.get("id", ""), agent, query,
                     it.get("distance"), rank, result_count, int(is_test), now)
                    for rank, it in enumerate(items)
                ],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Failed to log search hits", exc_info=True)


def _log_zero_result(query: str, agent: str = None, is_test: bool = False):
    """Log a zero-result query to kb_hits."""
    try:
        import sqlite3
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = sqlite3.connect(DB_PATH, timeout=5)
        try:
            conn.execute(
                "INSERT INTO kb_hits (source_file, chunk_id, agent, query, distance, rank, result_count, is_test, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("NO_RESULTS", "", agent, query, None, 0, 0, int(is_test), now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Failed to log zero-result query", exc_info=True)


_search_cache = {}  # {(query, top_k): (timestamp, result)}
_SEARCH_CACHE_MAX = 32
_SEARCH_CACHE_TTL = 300  # 5 minutes


def _cache_key(query_text: str, top_k: int, synthesize: bool) -> tuple:
    return (query_text.strip().lower(), top_k, synthesize)


# ---------------------------------------------------------------------------
# FTS5 Hybrid Search
# ---------------------------------------------------------------------------

def _index_fts5(chunks: list, filepath: str):
    """Index chunks into SQLite FTS5 for keyword search."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        # Remove old entries for this file
        conn.execute("DELETE FROM kb_fts WHERE source = ?", (filepath,))
        # Insert new chunks
        conn.executemany(
            "INSERT INTO kb_fts (source, section, content) VALUES (?, ?, ?)",
            [(filepath, c["metadata"].get("section", ""), c["text"]) for c in chunks],
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _remove_fts5(filepath: str):
    """Remove a file's entries from FTS5 index."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("DELETE FROM kb_fts WHERE source = ?", (filepath,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _search_fts5(query_text: str, top_k: int = 5) -> list:
    """Keyword search via FTS5. Returns list of {text, metadata, distance}."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        # FTS5 match query — escape special chars
        safe_query = " ".join(w for w in query_text.split() if len(w) > 1)
        if not safe_query:
            conn.close()
            return []
        rows = conn.execute(
            "SELECT source, section, content, rank FROM kb_fts WHERE kb_fts MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, top_k),
        ).fetchall()
        conn.close()
        results = []
        for source, section, content, rank in rows:
            results.append({
                "id": f"fts_{hash(content) % 100000}",
                "text": content,
                "metadata": {"source": source, "section": section, "search_type": "keyword"},
                "distance": max(0.1, 1.0 + rank),  # Normalize FTS5 rank (negative = better)
            })
        return results
    except Exception:
        return []


def search_brain(query_text: str, docs_dir: str, index_dir: str = None,
          top_k: int = TOP_K, synthesize: bool = True, profile: dict = None,
          agent: str = None, is_test: bool = False) -> dict:
    """Run a semantic search query. Returns dict with results and optional synthesis."""
    import time as _time

    # Check LRU cache
    ck = _cache_key(query_text, top_k, synthesize)
    cached = _search_cache.get(ck)
    if cached and (_time.time() - cached[0]) < _SEARCH_CACHE_TTL:
        # Still log the hit for analytics
        _log_search_hits(cached[1]["results"], query_text, agent, is_test)
        return cached[1]

    docs_path = Path(docs_dir).resolve()
    if index_dir is None:
        index_dir = str(docs_path / CHROMA_DIR)

    model = get_model()
    collection = get_collection(index_dir)

    # Load profile if not passed
    if profile is None:
        profile = load_profile()

    # Query expansion with user context
    expanded_query = expand_query(query_text, profile) if profile else query_text

    hints = _parse_temporal_query(query_text)
    has_filters = "participants" in hints or "type" in hints

    embedding = model.encode([expanded_query], show_progress_bar=False).tolist()

    # Fetch extra results when we need to post-filter by metadata
    fetch_k = top_k * 4 if (has_filters or hints.get("_sort_recent")) else top_k

    # Try type filter in ChromaDB (exact match works), do participant filter in Python
    where = None
    if "type" in hints:
        where = {"type": hints["type"]}

    try:
        results = collection.query(
            query_embeddings=embedding,
            n_results=fetch_k,
            where=where,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
    except Exception as e:
        logger.warning("ChromaDB filter failed (%s), falling back to unfiltered query", e)
        results = collection.query(
            query_embeddings=embedding,
            n_results=fetch_k,
            include=["documents", "metadatas", "distances", "embeddings"],
        )

    if not results["ids"] or not results["ids"][0]:
        # Try FTS5 fallback for keyword queries
        fts_results = _search_fts5(query_text, top_k)
        if fts_results:
            _log_search_hits(fts_results, query_text, agent, is_test)
            return {"results": fts_results, "synthesis": None, "query": query_text}
        _log_zero_result(query_text, agent, is_test)
        return {"results": [], "synthesis": None, "query": query_text}

    items = []
    for i, doc_id in enumerate(results["ids"][0]):
        items.append({
            "id": doc_id,
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
            "embedding": results["embeddings"][0][i],
        })

    # Hybrid: merge FTS5 keyword results (catches exact matches embeddings miss)
    fts_results = _search_fts5(query_text, top_k=3)
    seen_texts = {it["text"][:100] for it in items}
    for fts_item in fts_results:
        if fts_item["text"][:100] not in seen_texts:
            items.append(fts_item)
            seen_texts.add(fts_item["text"][:100])

    # Post-filter by participant (exact match on comma-separated field)
    if "participants" in hints:
        name = hints["participants"]
        items = [it for it in items if name in
                 [p.strip() for p in it["metadata"].get("participants", "").split(",")]]

    # Post-filter: only chunks with decisions metadata
    if hints.get("_filter_decisions"):
        dec_items = [it for it in items if it["metadata"].get("decisions")]
        if dec_items:
            items = dec_items

    # Post-filter: only chunks with action_items metadata
    if hints.get("_filter_actions"):
        act_items = [it for it in items if it["metadata"].get("action_items")]
        if act_items:
            items = act_items

    # Boost chunks whose section header matches theme words
    theme_words = hints.get("_theme_words", [])
    if theme_words:
        for it in items:
            section = it["metadata"].get("section", "").lower()
            if any(w in section for w in theme_words):
                it["distance"] *= 0.8  # 20% boost

    # Boost chunks tagged with relevance at index time
    if profile:
        proj_names = {p["name"].lower() for p in profile.get("active_projects", [])}
        for it in items:
            rel = it["metadata"].get("relevance", "")
            rel_score = float(it["metadata"].get("relevance_score", 0))
            if rel and rel_score > 0:
                # Scale boost by relevance_score: up to 15% distance reduction
                it["distance"] *= max(0.85, 1.0 - 0.15 * rel_score)

    # Temporal decay reranking
    is_temporal = hints.get("_sort_recent", False)
    items = rerank_with_decay(items, is_temporal)

    # Personalized reranking with profile vector + metadata boosts
    if profile:
        profile_emb = get_profile_embedding(profile, model)
        items = personalized_rerank(items, profile_emb, profile, model)

    # Fallback: sort by date if temporal but reranker didn't assign final_score
    if is_temporal and items and "final_score" not in items[0]:
        items.sort(key=lambda x: x["metadata"].get("date", ""), reverse=True)

    items = items[:top_k]

    _log_search_hits(items, query_text, agent, is_test)

    # Optional LLM synthesis via Ollama
    synthesis = None
    if synthesize:
        synthesis = _synthesize(query_text, items, profile)

    result = {"results": items, "synthesis": synthesis, "query": query_text}

    # Cache result (evict oldest if full)
    import time as _time
    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        oldest_key = min(_search_cache, key=lambda k: _search_cache[k][0])
        del _search_cache[oldest_key]
    _search_cache[ck] = (_time.time(), result)

    return result


def _synthesize(query_text: str, items: list, profile: dict = None):
    """Try to synthesize an answer via Ollama HTTP API. Returns None if unavailable."""
    import urllib.request

    context = "\n\n---\n\n".join(
        f"[Source: {it['metadata'].get('source','')} | Date: {it['metadata'].get('date','')}]\n{it['text']}"
        for it in items
    )

    # Build personalized prompt if profile available
    if profile:
        prefs = profile.get("query_preferences", {})
        detail = prefs.get("preferred_detail", "concise")
        persona = prefs.get("synthesis_persona", "a helpful assistant")
        projects = ", ".join(p["name"] for p in profile.get("active_projects", []))
        prompt = (
            f"You are {persona} answering a question for {profile.get('name', 'the user')}, "
            f"{profile.get('role', '')}.\n"
            f"Their active projects: {projects}.\n"
            f"Answer style: {detail}.\n\n"
            f"Question: {query_text}\n\n"
            f"Context:\n{context}\n\n"
            f"Answer:"
        )
    else:
        prompt = (
            f"Based on the following notes, answer the question concisely.\n\n"
            f"Question: {query_text}\n\n"
            f"Context:\n{context}\n\n"
            f"Answer:"
        )
    try:
        payload = json.dumps({
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        text = result.get("response", "").strip()
        return text if text else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return None
