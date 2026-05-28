# Knowledge Base

This directory holds promoted knowledge documents managed by Madeleine.

Files here are project-specific and excluded from version control (see `.gitignore`).

## Structure

Each doc uses inline metadata (no YAML frontmatter):

```markdown
# Title
<!-- category: architecture-pattern | tags: tag1, tag2 | promoted: YYYY-MM-DD -->

Content.
```

## Categories

| Category | Purpose |
|----------|---------|
| `architecture-pattern` | Design decisions, structural choices |
| `package-quirk` | Non-obvious library behavior, gotchas |
| `api-contract` | External API behaviors, rate limits, formats |
| `correction` | Things that were wrong before, now corrected |
| `reference-link` | Pointers to external docs, RFCs, issues |
| `operational` | Runbooks, deploy steps, incident notes |
| `decision-record` | Why a choice was made (ADR-style) |

## Indexing

```bash
python3 second_brain/cli.py index knowledge/
```

Run after adding or removing docs to keep ChromaDB in sync.
Archive = move file out of this directory AND remove from ChromaDB.
