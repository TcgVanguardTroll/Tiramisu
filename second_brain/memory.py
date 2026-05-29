"""Thin sqlite3 wrappers for the agent memory table in tiramisu.db, with ChromaDB semantic search."""

import fcntl
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

DB_PATH = str(Path(__file__).resolve().parent.parent / "tiramisu.db")
CHROMA_DIR = str(Path(__file__).resolve().parent / ".memory_index")
COLLECTION_NAME = "memories"
EMBED_MODEL = "all-MiniLM-L6-v2"

AGENT_TIERS = {
    "tiramisu": 1, "croissant": 1,
    "cannoli": 2, "mochi": 2, "eclair": 2,
    "madeleine": 3, "brioche": 3,
}

_model_cache = None
_chroma_cache = None


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _get_model():
    global _model_cache
    if _model_cache is None:
        _model_cache = SentenceTransformer(EMBED_MODEL)
    return _model_cache


def _get_collection():
    global _chroma_cache
    if _chroma_cache is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _chroma_cache = client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
    return _chroma_cache


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


OUTBOX_PATH = os.path.expanduser("~/.meshclaw/workspace/memory_outbox.jsonl")


def _write_outbox(agent, category, content, memory_type, scope, importance, confidence, source_job, timestamp):
    """Append a JSON line to the MeshClaw outbox. Fails silently."""
    try:
        os.makedirs(os.path.dirname(OUTBOX_PATH), exist_ok=True)
        entry = json.dumps({"agent": agent, "category": category, "content": content,
                            "memory_type": memory_type, "scope": scope, "importance": importance,
                            "confidence": confidence, "source_job": source_job or "", "timestamp": timestamp})
        with open(OUTBOX_PATH, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(entry + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------

def save_memory(agent: str, category: str, content: str, source_job: str = None,
                memory_type: str = "semantic", scope: str = "agent",
                importance: float = 0.5, job_name: str = None,
                confidence: float = 0.8):
    """Insert a memory row and embed in ChromaDB.
    
    Confidence thresholds:
      >= 0.80: auto-save (default behavior)
      0.50-0.79: saved with [needs_review] prefix
      < 0.50: should not be saved (caller should discard)
    """
    if confidence < 0.50:
        return None  # discard low-confidence candidates
    if confidence < 0.80 and not content.startswith("[needs_review]"):
        content = f"[needs_review] {content}"
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO memory (agent, category, content, source_job,
               memory_type, scope, importance, job_name, confidence, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (agent, category, content, source_job, memory_type, scope, importance, job_name, confidence),
        )
        mem_id = cur.lastrowid
    embed_memory(mem_id, content, {
        "agent": agent, "memory_type": memory_type,
        "scope": scope, "importance": importance,
        "confidence": confidence,
    })
    _write_outbox(agent, category, content, memory_type, scope, importance, confidence, source_job, datetime.now().isoformat())
    return mem_id


def get_memories(agent: str = None, category: str = None, limit: int = 50) -> list[dict]:
    """Retrieve memories with optional agent/category filters."""
    sql, params = "SELECT * FROM memory WHERE 1=1", []
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_memories_by_type_scope(memory_type: str, scope: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM memory WHERE memory_type=? AND scope=? ORDER BY importance DESC LIMIT ?",
            (memory_type, scope, limit),
        ).fetchall()]


def get_memories_by_type(memory_type: str, order_by: str = "created_at DESC", limit: int = 50) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM memory WHERE memory_type=? ORDER BY {order_by} LIMIT ?",
            (memory_type, limit),
        ).fetchall()]


def search_memories(query_text: str, agent: str = None) -> list[dict]:
    """Simple LIKE search on content."""
    sql = "SELECT * FROM memory WHERE content LIKE ?"
    params = [f"%{query_text}%"]
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_memory(memory_id: int, **kwargs):
    """Update arbitrary columns on a memory row."""
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [memory_id]
    with _conn() as conn:
        conn.execute(f"UPDATE memory SET {sets} WHERE id=?", vals)


def delete_memory(memory_id: int):
    """Delete a memory and remove its embedding."""
    with _conn() as conn:
        conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
    try:
        _get_collection().delete(ids=[str(memory_id)])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ChromaDB embedding (Phase 1)
# ---------------------------------------------------------------------------

def embed_memory(memory_id: int, content: str, metadata: dict):
    """Embed a memory in ChromaDB for semantic search."""
    col = _get_collection()
    # ChromaDB metadata values must be str/int/float/bool
    clean_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v
                  for k, v in metadata.items()}
    model = _get_model()
    embedding = model.encode(content).tolist()
    col.upsert(ids=[str(memory_id)], documents=[content],
               metadatas=[clean_meta], embeddings=[embedding])


def search_memories_semantic(query: str, agent: str = None, memory_type: str = None,
                             scope: str = None, top_k: int = 20) -> list[dict]:
    """Semantic search on memories with optional filters."""
    where_filters = {}
    if agent:
        where_filters["agent"] = agent
    if memory_type:
        where_filters["memory_type"] = memory_type
    if scope:
        where_filters["scope"] = scope

    col = _get_collection()
    model = _get_model()
    embedding = model.encode(query).tolist()
    results = col.query(
        query_embeddings=[embedding], n_results=top_k,
        where=where_filters if where_filters else None,
    )
    # Flatten into list of dicts
    out = []
    if results and results["ids"] and results["ids"][0]:
        for i, mid in enumerate(results["ids"][0]):
            out.append({
                "id": int(mid),
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else None,
            })
    return out


def backfill_embeddings():
    """Embed all existing memories that lack embeddings."""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM memory").fetchall()
    count = 0
    for r in rows:
        r = dict(r)
        embed_memory(r["id"], r["content"], {
            "agent": r["agent"], "memory_type": r.get("memory_type", "semantic"),
            "scope": r.get("scope", "agent"),
            "importance": r.get("importance", 0.5),
        })
        count += 1
    return count


# ---------------------------------------------------------------------------
# Tier-based loading (Phase 2)
# ---------------------------------------------------------------------------

def load_memories_for_agent(agent: str, task_context: str = None) -> str:
    """Load memories respecting tier and token budget."""
    tier = AGENT_TIERS.get(agent, 2)
    budget = 4000 if tier in (1, 3) else 8000
    memories = []
    seen_ids = set()
    used_tokens = 0

    def _add(mem_list):
        nonlocal used_tokens
        for m in mem_list:
            mid = m["id"]
            if mid in seen_ids:
                continue
            tokens = _estimate_tokens(m["content"])
            if used_tokens + tokens > budget:
                continue
            memories.append(m)
            seen_ids.add(mid)
            used_tokens += tokens

    # 1. Always: core/global memories
    _add(get_memories_by_type_scope("core", "global"))

    # 2. Tier-specific
    if tier == 1:
        _add(get_memories_by_type("job-log", order_by="created_at DESC", limit=20))
    elif tier == 2 and task_context:
        relevant = search_memories_semantic(task_context, top_k=30)
        # Convert semantic results to full rows
        ids = [r["id"] for r in relevant if r["id"] not in seen_ids]
        if ids:
            placeholders = ",".join("?" * len(ids))
            with _conn() as conn:
                rows = conn.execute(
                    f"SELECT * FROM memory WHERE id IN ({placeholders}) ORDER BY importance DESC", ids
                ).fetchall()
            _add([dict(r) for r in rows])

    # 3. Agent-specific memories (fill remaining budget)
    _add(get_memories(agent=agent, limit=100))

    # Bump access stats for all loaded memories
    if memories:
        ids = [m["id"] for m in memories]
        placeholders = ",".join("?" * len(ids))
        with _conn() as conn:
            conn.execute(
                f"UPDATE memory SET access_count = access_count + 1, last_accessed = ? WHERE id IN ({placeholders})",
                [datetime.now().isoformat()] + ids,
            )

    return _format_memories(memories, agent, used_tokens)


def _format_memories(memories: list[dict], agent: str, tokens_used: int) -> str:
    if not memories:
        return f"No memories found for {agent}."
    lines = [f"## {agent}'s memories ({len(memories)} entries, ~{tokens_used} tokens)"]
    for m in memories:
        cat = m.get("category", "?")
        lines.append(f"- [{cat}] {m['content']}")
    lines.append(
        "\nIMPORTANT: Save new decisions, preferences, corrections, and facts using python3 with: "
        "from second_brain.memory import save_memory; save_memory('<agent>', '<category>', '<content>', source_job='<job>')"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deduplication & Conflict Resolution
# ---------------------------------------------------------------------------

def check_duplicate(content: str, agent: str = None, threshold_high: float = 0.90,
                    threshold_low: float = 0.70) -> dict:
    """Check if a candidate memory is a duplicate.
    
    Returns: {"action": "update"|"review"|"add", "match_id": int|None, "similarity": float}
    """
    results = search_memories_semantic(content, agent=agent, top_k=3)
    if not results:
        return {"action": "add", "match_id": None, "similarity": 0.0}
    best = results[0]
    similarity = 1.0 - best["distance"]  # cosine distance → similarity
    if similarity > threshold_high:
        return {"action": "update", "match_id": best["id"], "similarity": similarity}
    elif similarity > threshold_low:
        return {"action": "review", "match_id": best["id"], "similarity": similarity}
    return {"action": "add", "match_id": None, "similarity": similarity}


def supersede_memory(old_id: int, new_id: int):
    """Mark old memory as superseded by new one. Never deletes."""
    with _conn() as conn:
        conn.execute("UPDATE memory SET superseded_by=?, importance=0.1, updated_at=datetime('now') WHERE id=?",
                     (new_id, old_id))


def refresh_memory(memory_id: int):
    """Reinforce a memory — reset importance and bump access count."""
    with _conn() as conn:
        conn.execute("""UPDATE memory SET access_count = access_count + 1,
                       last_accessed = datetime('now'), updated_at = datetime('now')
                       WHERE id = ?""", (memory_id,))


def apply_temporal_decay(days_threshold: int = 30, decay_amount: float = 0.1,
                         min_importance: float = 0.1):
    """Decay importance of memories not accessed recently. Run periodically."""
    with _conn() as conn:
        conn.execute("""
            UPDATE memory SET importance = MAX(?, importance - ?), updated_at = datetime('now')
            WHERE last_accessed < datetime('now', ? || ' days')
              AND importance > ?
              AND superseded_by IS NULL
              AND category != 'correction'
        """, (min_importance, decay_amount, f"-{days_threshold}", min_importance))
        affected = conn.total_changes
    return affected


def get_needs_review(agent: str = None) -> list[dict]:
    """Get all memories flagged for review."""
    sql = "SELECT * FROM memory WHERE content LIKE '[needs_review]%'"
    params = []
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def promote_memory(memory_id: int, new_confidence: float = 0.85, new_importance: float = 0.9):
    """Promote a needs_review memory to full confidence."""
    with _conn() as conn:
        row = conn.execute("SELECT content FROM memory WHERE id=?", (memory_id,)).fetchone()
        if row:
            content = dict(row)["content"].replace("[needs_review] ", "")
            conn.execute("""UPDATE memory SET content=?, confidence=?, importance=?,
                           updated_at=datetime('now') WHERE id=?""",
                         (content, new_confidence, new_importance, memory_id))
            # Re-embed with cleaned content
            embed_memory(memory_id, content, {
                "confidence": new_confidence, "importance": new_importance,
            })


# ---------------------------------------------------------------------------
# Partners (unchanged)
# ---------------------------------------------------------------------------

def get_partners(team: str = None, relationship: str = None) -> list[dict]:
    sql, params = "SELECT * FROM partners WHERE 1=1", []
    if team:
        sql += " AND team = ?"
        params.append(team)
    if relationship:
        sql += " AND relationship = ?"
        params.append(relationship)
    sql += " ORDER BY id"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
