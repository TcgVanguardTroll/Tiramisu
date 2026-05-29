# Second Brain — Local Knowledge Management System

A fully local, CLI-based "second brain" that ingests your markdown notes, indexes them with semantic search, and answers natural language queries. Zero cloud dependencies.

## Features

- **Markdown ingestion** with YAML frontmatter parsing (date, participants, tags, type)
- **Markdown-aware chunking** that respects header boundaries
- **Semantic search** via sentence-transformers (`all-MiniLM-L6-v2`)
- **Persistent vector storage** via ChromaDB (local, embedded)
- **Temporal queries** — "last 1:1 with Sarah" filters by date + participant metadata
- **Incremental indexing** — only re-embeds new/changed files (MD5 content hashing)
- **Optional LLM synthesis** via Ollama — falls back to raw chunks if unavailable
- **Under 400 lines** of core code. No framework bloat.

## Setup

```bash
pip install -r requirements.txt
```

That's it. No Docker, no config files, no API keys.

## Usage

### 1. Index your notes

```bash
python cli.py index ~/notes
```

Options:
- `--force` — re-index all files (ignore content hashes)
- `--index-dir <path>` — custom location for the ChromaDB index (default: `<docs_dir>/.second_brain_index/`)

### 2. Query your knowledge base

```bash
python cli.py query ~/notes "What did I discuss in my last 1:1 with Sarah?"
```

Options:
- `--top-k 10` — number of results (default: 5)
- `--no-llm` — skip Ollama synthesis, just return retrieved chunks
- `--index-dir <path>` — custom index directory

### Example queries

```bash
python cli.py query ~/notes "What did Sarah say about the API redesign?"
python cli.py query ~/notes "last 1:1 with Sarah"
python cli.py query ~/notes "action items from design review"
python cli.py query ~/notes "code review feedback on auth module" --top-k 10
```

## Expected markdown format

The system handles messy markdown gracefully, but works best with YAML frontmatter:

```markdown
---
date: 2024-03-15
participants: [Sarah, Pranav]
tags: [api, redesign]
type: 1:1
---

# 1:1 with Sarah — March 15

## Discussion
- Talked about API redesign timeline...
```

All frontmatter fields are optional. Files without frontmatter are still indexed.

## How it works

1. **Parse**: Reads `.md` files, extracts YAML frontmatter metadata
2. **Chunk**: Splits on markdown headers, then sub-splits large sections (512 chars, 80 char overlap)
3. **Embed**: Encodes chunks with `all-MiniLM-L6-v2` (384-dim vectors)
4. **Store**: Persists embeddings + metadata in ChromaDB
5. **Retrieve**: Cosine similarity search with optional metadata filters (participant, type, date)
6. **Synthesize**: Optionally sends top chunks to Ollama for a natural language answer

## Incremental indexing

Files are tracked by MD5 hash. On re-run, only new or modified files are re-embedded. Use `--force` to re-index everything.

## LLM synthesis

If Ollama is running with a `llama3` model, the system will synthesize answers from retrieved chunks. If Ollama is not available, it gracefully falls back to showing raw chunks. No crash, no error.

To set up Ollama (optional):
```bash
# Install Ollama from https://ollama.ai
ollama pull llama3
```

## Architecture

```
cli.py      — CLI entry point (argparse). ~70 lines.
brain.py    — Core library. ~250 lines.
              ├── parse_markdown()     — frontmatter + body extraction
              ├── chunk_markdown()     — header-aware splitting
              ├── index_files()        — incremental embed + store
              ├── query()              — retrieve + filter + rank
              └── _synthesize()        — optional Ollama integration
```

## Dependencies

| Package | Purpose |
|---|---|
| chromadb | Local vector database |
| sentence-transformers | Embedding model |
| python-frontmatter | YAML frontmatter parsing |
