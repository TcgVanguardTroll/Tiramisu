# Tiramisu 🍰

A portable multi-agent orchestration system for personal software development. Tiramisu coordinates a crew of pastry-named pets to decompose, implement, review, and track engineering work — so you don't have to context-switch between every tool yourself.

## The Crew

Every agent is named after a pastry that matches their fur.

| Agent | Pet | Role |
|-------|-----|------|
| 🐕 **Tiramisu** | Red tri mini American Shepherd | Orchestrator — decomposes tasks, delegates, tracks progress. Never writes code. |
| 🐾 **Éclair** | Sleek black ferret | SDE — writes code, creates PRs, addresses review feedback |
| 🐰 **Mochi** | White lop rabbit | Brainstorm — explores approaches, stress-tests assumptions |
| 🐶 **Cannoli** | Beagle | Research — follows every lead to the source, cites everything |
| 🐱 **Madeleine** | Ginger tabby | Knowledge — manages the knowledge base, triages learnings |
| 🐕 **Croissant** | Corgi | PM — herds tickets, timelines, and scope into formation |
| 🐈 **Cookie** | Tortoiseshell cat | Reviewer — judgmental, impeccable taste, zero tolerance for sloppiness |
| 🦮 **Brioche** | Golden retriever | HR — creates new agents when the team needs a new skill |

## Task Lifecycle

```
pending → planned → implementing → pr_open → addressing_feedback → merged → done
```

## Setup

### Prerequisites

- Python 3.10+
- Git + [GitHub CLI](https://cli.github.com/)
- An [Anthropic API key](https://console.anthropic.com/)

### Install

**Linux / macOS:**
```bash
chmod +x setup.sh
./setup.sh
```

**Windows:**
```powershell
.\setup.ps1
```

Both scripts:
1. Create `~/.tiramisu/` directory structure
2. Copy agent prompts and config docs
3. Install Python dependencies (`chromadb`, `anthropic`, etc.)
4. Initialise the SQLite database (`schema.sql`)
5. Initialise the ChromaDB vector store
6. Create a `.env` template for your API keys

### Add Your API Keys

Edit `~/.tiramisu/.env`:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

## Directory Structure

```
tiramisu-portable/
├── agents/                  # Agent prompt files (7 agents)
├── knowledge/               # Promoted knowledge docs (project-specific, gitignored)
├── second_brain/            # Python: brain.py, memory.py, cli.py, reranker.py
├── queue/                   # Task queue runner
├── hooks/                   # Lifecycle hooks
├── scripts/                 # Utilities: llm.py, extract_session_learnings.py
├── ingest-ide-sessions/     # Rust: ingest IDE session logs into knowledge
├── schema.sql               # SQLite schema (tasks_v2, memories, triage_log, kb_hits)
├── requirements.txt         # Python dependencies
├── setup.sh                 # Bootstrap (Linux/macOS)
├── setup.ps1                # Bootstrap (Windows)
├── agent-architecture.md    # System design, task lifecycle, orchestration rules
├── agent-config-template.md # Tool mappings, agent JSON config, CI/CD templates
├── memory-system.md         # SQLite + ChromaDB memory layer design
├── code-style.md            # Java, Python, Rust, TypeScript style guides
├── engineering-principles.md# Design and distributed systems principles
└── communication-style.md   # Commit format, review tone, Cookie's persona
```

## Memory System

Two-layer memory:

| Layer | Technology | What it stores |
|-------|-----------|----------------|
| Structured | SQLite (`tiramisu.db`) | Tasks, status, activity log, memories |
| Semantic | ChromaDB (`.second_brain_index/`) | Knowledge docs, indexed for vector search |

Index your knowledge base:
```bash
python3 second_brain/cli.py index knowledge/
```

## Adapting to a New Company

1. Update agent prompts in `agents/` with your team's tools and conventions
2. Point `croissant.md` at your ticket system (Linear, Jira, GitHub Issues)
3. Replace the GitHub workflow in `agent-config-template.md` if using GitLab or another host
4. Rebuild your knowledge base in `knowledge/` for the new codebase
5. Re-run `python3 second_brain/cli.py index knowledge/` to populate ChromaDB

## Contributing / Extending

- Add a new agent by creating `agents/<name>.md` and registering it in `agent-config-template.md`
- Add knowledge categories by updating the `category` enum in `madeleine.md` and `knowledge/README.md`
- The task lifecycle statuses are enforced at the application layer (no DB constraint) — update `agent-architecture.md` if you add new statuses
