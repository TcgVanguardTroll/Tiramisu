# Tiramisu GitHub Publishing Plan

## Safe to Publish (no Amazon IP)
- `second_brain/` (brain.py, memory.py, cli.py, reranker.py, kb_report.py)
- `queue/runner.py`
- `hooks/`
- `scripts/llm.py`
- `scripts/extract_session_learnings.py`
- `scripts/ingest-ide-sessions/` (Rust crate)

## Needs Stripping (remove Amazon references)
- `agents/` prompts (eclair.md, mochi.md, madeleine.md, etc.)
- `scripts/scholar.py`
- `aim-package/` agent configs
- `.kiro/agents/` JSON configs

## DO NOT Publish
- `knowledge/` (Amazon internal docs)
- `scripts/cr_*` (Amazon code review tools)
- `.kiro/steering/` (Amazon domain knowledge)
- `threads/`, `shared_workspace/reviews/`, `state/`

## Target Structure for GitHub
```
tiramisu/
├── README.md
├── second_brain/
├── queue/
├── hooks/
├── agents/
├── scripts/ (llm.py, extract_session_learnings.py, scholar.py genericized)
├── ingest-ide-sessions/ (Rust)
├── knowledge/ (empty with README)
├── requirements.txt
└── setup.sh
```

## Effort: ~2-4 hours
## When: During leave, before Aug 12
