# Portable Agent Config — Non-Amazon Tools

## Tool Mapping (Amazon → Generic)

| Amazon Tool | Replacement | Purpose |
|-------------|-------------|---------|
| Code Reviews (CRUX) | GitHub PRs / GitLab MRs | Code review |
| Taskei / SIM | Linear / Jira / GitHub Issues | Task tracking |
| Brazil workspace | Git repo + package manager | Build system |
| brazil-build | make / gradle / cargo / npm | Build command |
| Pipelines | GitHub Actions / GitLab CI | CI/CD |
| Quip | Notion / Google Docs / Markdown | Documentation |
| Phonetool | Slack profiles / company directory | People lookup |
| Apollo | Kubernetes / ArgoCD / Terraform | Deployment |
| CloudWatch | Datadog / Grafana / Prometheus | Monitoring |
| Odin | Vault / AWS Secrets Manager | Secrets |
| Bindle | GitHub Teams / CODEOWNERS | Permissions |

## Agent Config Template

```json
{
  "agents": {
    "tiramisu": {
      "role": "orchestrator",
      "prompt": "file://prompts/tiramisu.md",
      "tools": ["task_manager", "agent_spawner"],
      "rules": [
        "Never execute tasks directly",
        "Decompose into DAG of steps",
        "Track status through lifecycle"
      ]
    },
    "eclair": {
      "role": "sde",
      "prompt": "file://prompts/eclair.md",
      "tools": ["git", "editor", "build", "test"],
      "style": "file://config/code-style.md",
      "rules": [
        "Follow code style guide exactly",
        "Run tests before presenting results",
        "Surgical changes only"
      ]
    },
    "cookie": {
      "role": "reviewer",
      "prompt": "file://prompts/cookie.md",
      "tools": ["git_diff", "comment"],
      "rules": [
        "Cat persona always",
        "Dedup before posting",
        "Max 1 top-level comment per PR revision"
      ]
    },
    "madeleine": {
      "role": "knowledge_manager",
      "prompt": "file://prompts/madeleine.md",
      "tools": ["knowledge_index", "file_system", "search"],
      "rules": [
        "Triage is idempotent",
        "Batch indexing over per-file",
        "Never modify existing knowledge content on merge"
      ]
    },
    "cannoli": {
      "role": "researcher",
      "prompt": "file://prompts/cannoli.md",
      "tools": ["web_search", "file_read", "knowledge_search"],
      "rules": [
        "Cite sources",
        "Summarize findings concisely",
        "Flag uncertainty explicitly"
      ]
    },
    "mochi": {
      "role": "brainstorm",
      "prompt": "file://prompts/mochi.md",
      "tools": ["knowledge_search", "web_search"],
      "rules": [
        "Challenge assumptions",
        "Present multiple approaches",
        "Identify tradeoffs explicitly"
      ]
    },
    "croissant": {
      "role": "pm",
      "prompt": "file://prompts/croissant.md",
      "tools": ["task_manager", "ticket_system", "calendar"],
      "rules": [
        "Track deadlines",
        "Update ticket status",
        "Never write code"
      ]
    }
  }
}
```

## GitHub-Based Workflow

### PR Creation (replaces CRRevisionCreator)
```bash
# Create branch
git checkout -b feat/my-feature

# Make changes, commit
git add -p  # stage specific files, never git add .
git commit -m "feat(scope): description"

# Push and create PR
git push -u origin feat/my-feature
gh pr create --title "feat(scope): description" --body "$(cat .github/pr-template.md)"
```

### PR Review (replaces Cookie on CRUX)
```bash
# Fetch PR diff
gh pr diff <number>

# Post review comment
gh pr review <number> --comment --body "comment text"

# Approve
gh pr review <number> --approve
```

### Task Management (replaces Taskei)
```bash
# Linear CLI
linear issue create --title "Task" --team "ENG" --assignee "me"
linear issue update <ID> --status "In Progress"

# Or GitHub Issues
gh issue create --title "Task" --label "feature"
gh issue edit <number> --add-label "in-progress"
```

## CI/CD Config (replaces Pipelines)

### GitHub Actions Example
```yaml
name: Build & Test
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make build
      - name: Test
        run: make test
      - name: Lint
        run: make lint
```

## Local Development Setup

### Prerequisites
- Python 3.10+ (for Tiramisu orchestrator)
- SQLite 3 (memory system)
- ChromaDB (knowledge base)
- Git + GitHub CLI (`gh`)
- Your language toolchain (JDK 21, Rust, Node, etc.)

### Bootstrap Script
```bash
#!/bin/bash
# Initialize Tiramisu portable

mkdir -p ~/.tiramisu/{knowledge,knowledge_candidates,prompts,config}
cp prompts/*.md ~/.tiramisu/prompts/
cp config/*.md ~/.tiramisu/config/

# Initialize SQLite
python3 -c "
import sqlite3
db = sqlite3.connect('$HOME/.tiramisu/tiramisu.db')
db.executescript(open('schema.sql').read())
print('Database initialized')
"

# Initialize ChromaDB
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='$HOME/.tiramisu/.second_brain_index')
client.get_or_create_collection('knowledge')
print('Vector store initialized')
"

echo "Tiramisu portable ready at ~/.tiramisu/"
```

## LLM Provider Options

| Provider | Best For | Notes |
|----------|----------|-------|
| Claude API (Anthropic) | Primary agent work | Best reasoning, tool use |
| GPT-4 (OpenAI) | Fallback / comparison | Good for brainstorming |
| Local (Ollama + Llama) | Offline / privacy | Slower, less capable |
| Claude via Cursor / Windsurf / VS Code | IDE integration | Best DX for coding agents |

### API Key Management
```bash
# .env file (never commit)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Or use system keychain
security add-generic-password -a "tiramisu" -s "anthropic" -w "sk-ant-..."
```

## Migration Checklist (Amazon → External)

- [ ] Export task history from SQLite (already portable)
- [ ] Export knowledge base files (already markdown)
- [ ] Update tool configs to use GitHub/Linear/etc.
- [ ] Set up CI/CD (GitHub Actions or similar)
- [ ] Configure LLM API keys
- [ ] Test agent spawning with new tools
- [ ] Rebuild knowledge index for new codebase
- [ ] Update steering docs with new team/project context
