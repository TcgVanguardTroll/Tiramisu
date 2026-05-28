# Agent Architecture & Orchestration

## Overview

Tiramisu is a multi-agent personal development system. The orchestrator (Tiramisu) coordinates specialized agents, each with a distinct role. Tiramisu itself NEVER executes tasks directly — it decomposes, delegates, and tracks.

## Core Agents

| Agent | Role | Personality |
|-------|------|-------------|
| **Tiramisu** | Orchestrator — decomposes tasks, delegates, tracks progress | Project manager, never writes code |
| **Éclair** | SDE — writes code, creates PRs, addresses review feedback | Direct, surgical, follows style guide |
| **Mochi** | Brainstorm — explores ideas, proposes approaches, debates tradeoffs | Creative, challenges assumptions |
| **Cannoli** | Research — gathers context, reads docs, summarizes findings | Thorough, cites sources |
| **Madeleine** | Knowledge — manages knowledge base, triages learnings, promotes docs | Librarian, systematic |
| **Croissant** | PM — tracks tickets, updates status, manages timelines | Organized, deadline-focused |
| **Cookie** | Reviewer — reviews PRs with cat persona | Judgmental tortoiseshell cat 🐱 |

## Orchestration Pattern

### Task Lifecycle
```
pending → planned → implementing → pr_open → addressing_feedback → merged → done
```

### Decomposition Flow
1. User gives high-level instruction
2. Tiramisu decomposes into steps with dependencies (DAG)
3. Each step is assigned to an agent
4. Agents execute in dependency order (parallel when possible)
5. Results flow back to Tiramisu for status tracking

### Subagent Execution
- Subagents are spawned as blocking DAG stages via `depends_on`
- Each stage runs as a persistent session
- Stages with no dependencies start immediately in parallel
- Hard timeout: 20 minutes per subagent
- Turn limit: 30 turns per subagent

### Key Rules
- **Tiramisu is orchestrator-only** — any code that has Tiramisu performing implementation work violates the architecture
- **Per-agent memory isolation** is intentional — agents don't share memory state
- **One master task per story** — a single task tracks the full lifecycle, with multiple PRs stored as a JSON array
- **Task status must reflect PR lifecycle** — using generic 'in_progress' breaks dependency resolution

## Task Storage

### Schema (SQLite)
```sql
CREATE TABLE tasks_v2 (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,  -- strict sequence: pending → planned → implementing → pr_open → addressing_feedback → merged → done
    agent TEXT,
    parent_task_id INTEGER,
    depends_on TEXT,  -- JSON array of task IDs (DAG edges)
    pr_metadata TEXT,  -- JSON array even for single-PR tasks
    ticket_id TEXT,
    activity_log TEXT,  -- append-only, never overwrite
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### Rules
- `activity_log` is append-only — never overwrite
- `pr_metadata` stores JSON array even for single-PR tasks
- `depends_on` column defines DAG edges
- Status has no CHECK constraint — validation enforced at application layer
- New status values (implementing, pr_open, addressing_feedback, merged) apply only to tasks with PRs

## Entry Points

Three entry points all funnel through decomposition:
1. **Text** — natural language instruction from user
2. **Spec** — structured specification document
3. **File** — file-based task definition

Each path sets both `spec_content` and `original_input` (they serve different display purposes downstream).

## Plan Execution

- `execute_plan()` spawns an async task and must mirror the full setup sequence (watchdog, workspace init, save progress, notifications)
- `update_plan()` must re-index steps from 1 and validate `depends_on` only references earlier indices
- Step descriptions and `depends_on` must be persisted and restored in both serialization methods

## Knowledge System Integration

- Knowledge candidates use YAML frontmatter
- Promoted knowledge docs use inline header metadata (never YAML frontmatter)
- Triage is idempotent — check triage log before processing each candidate
- Batch indexing preferred over per-file indexing
- Categories: `architecture-pattern`, `package-quirk`, `api-contract`, `correction`, `reference-link`, `operational`, `decision-record`
