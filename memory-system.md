# Memory System Architecture

## Overview

Two-layer memory system: SQLite for structured data (tasks, preferences, facts) and ChromaDB for semantic search over knowledge documents.

## SQLite Layer

### Purpose
- Task tracking and status
- User preferences and corrections
- Decision log
- Activity history (append-only)

### Key Tables

```sql
-- Core memory store
CREATE TABLE memories (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,  -- 'preference', 'fact', 'correction', 'decision', 'context'
    content TEXT NOT NULL,
    source TEXT,  -- which agent wrote this
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- Task management
CREATE TABLE tasks_v2 (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    agent TEXT,
    parent_task_id INTEGER,
    depends_on TEXT,  -- JSON array
    pr_metadata TEXT,  -- JSON array
    ticket_id TEXT,
    activity_log TEXT,  -- append-only
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Search hit tracking (for knowledge base analytics)
CREATE TABLE kb_hits (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    file_path TEXT NOT NULL,
    agent TEXT,
    score REAL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Rules
- `activity_log` is append-only — code that overwrites it destroys the audit trail
- Hit tracking must NEVER influence search ranking (decision locked)
- Per-agent memory isolation is intentional and load-bearing

## ChromaDB Layer (Vector Search)

### Purpose
- Semantic search over knowledge documents
- Finding relevant context for agent tasks
- Surfacing related past decisions/learnings

### Knowledge Directory Structure
```
knowledge/
├── architecture-patterns/
├── api-contracts/
├── corrections/
├── decision-records/
├── operational/
├── package-quirks/
└── reference-links/
knowledge_candidates/
├── {YYYYMMDD_HHMMSSZ}_{agent}_{topic_slug}.md
└── TEMPLATE.md
knowledge/archive/
└── (archived docs moved here)
```

### Indexing
- CLI: `python3 cli.py index <knowledge_dir>` — batch indexing preferred
- Index directory: `.second_brain_index` subdirectory
- `index_single_file(filepath, index_dir, force)` — index_dir must be the `.second_brain_index` subdirectory
- Explicitly exclude `knowledge/archive/` from indexing
- Exclude `TEMPLATE.md` and `triage_log.md` when globbing candidates

### Knowledge Candidate Format
```markdown
---
category: architecture-pattern  # one of 7 values
agent: eclair
confidence: high
---

# Topic Title

Content here...
```

### Categories (exactly 7)
1. `architecture-pattern`
2. `package-quirk`
3. `api-contract`
4. `correction`
5. `reference-link`
6. `operational`
7. `decision-record`

### Triage Process (Madeleine agent)
1. Madeleine checks `triage_log.md` before processing each candidate (idempotent)
2. Evaluates candidate for promotion to `knowledge/`
3. Dispositions: promote, merge, archive, reject
4. Merge = append new `## <Topic>` section with attribution (never rewrite existing content)
5. Archive = move to `knowledge/archive/` AND remove from ChromaDB index

### Archive Workflow
- Requires BOTH: filesystem move (`knowledge/` → `knowledge/archive/`) AND ChromaDB removal
- `kb_report.py` 'Never Used' section respects 30-day grace period for new files
- 'Archive Candidates' requires: zero hits AND file age > 30 days AND 60+ days inactivity

### Search
```python
def search_brain(query: str, top_k: int = 5, agent: str | None = None) -> list[Result]:
    """Semantic search over knowledge base."""
    # agent parameter is optional, defaults to None
    # Hits are logged but NEVER influence ranking
```

- `_log_search_hits()` must be wrapped in try/except — logging failure must not break search
- Results ranked by vector similarity only (no hit-count boosting)

## Sync Pattern

- Memory syncs bidirectionally between agents every 5 minutes (cron)
- Steering docs auto-regenerated from memory tables via refresh scripts
- Active context updated from task status + recent activity

## Portable Setup (Non-Amazon)

### Requirements
- Python 3.10+
- SQLite 3 (built into Python)
- ChromaDB (`pip install chromadb`)
- Any embedding model (OpenAI, local sentence-transformers, etc.)

### Minimal Bootstrap
```python
import chromadb
import sqlite3

# SQLite for structured memory
db = sqlite3.connect("tiramisu.db")
db.execute("""CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

# ChromaDB for semantic search
client = chromadb.PersistentClient(path=".second_brain_index")
collection = client.get_or_create_collection("knowledge")
```
