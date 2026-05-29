#!/usr/bin/env bash
# Bootstrap Tiramisu portable — run once after cloning
set -euo pipefail

TIRAMISU_HOME="${TIRAMISU_HOME:-$HOME/.tiramisu}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Bootstrapping Tiramisu at $TIRAMISU_HOME"

# --- directories ---
mkdir -p "$TIRAMISU_HOME"/{knowledge,knowledge_candidates,prompts,config,state}

# --- copy agent prompts ---
if [ -d "$SCRIPT_DIR/agents" ]; then
    cp "$SCRIPT_DIR"/agents/*.md "$TIRAMISU_HOME/prompts/"
    echo "    Copied agent prompts"
fi

# --- copy config docs ---
for f in code-style.md communication-style.md engineering-principles.md agent-architecture.md memory-system.md; do
    [ -f "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" "$TIRAMISU_HOME/config/"
done
echo "    Copied config docs"

# --- Python deps ---
echo "==> Installing Python dependencies"
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# --- SQLite schema ---
echo "==> Initialising SQLite database"
python3 - <<'EOF'
import sqlite3, os, sys
db_path = os.path.expanduser("~/.tiramisu/tiramisu.db")
script_dir = os.environ.get("SCRIPT_DIR", ".")
schema_path = os.path.join(script_dir, "schema.sql")
db = sqlite3.connect(db_path)
with open(schema_path) as f:
    db.executescript(f.read())
db.close()
print(f"    Database ready at {db_path}")
EOF

# --- ChromaDB vector store ---
echo "==> Initialising ChromaDB vector store"
python3 - <<'EOF'
import chromadb, os
index_path = os.path.expanduser("~/.tiramisu/.second_brain_index")
client = chromadb.PersistentClient(path=index_path)
client.get_or_create_collection("knowledge")
print(f"    Vector store ready at {index_path}")
EOF

# --- .env template ---
ENV_FILE="$TIRAMISU_HOME/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'ENVEOF'
# Tiramisu environment — fill in your keys, never commit this file
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...          # optional fallback
TIRAMISU_HOME=~/.tiramisu
ENVEOF
    echo "    Created .env template at $ENV_FILE — add your API keys"
fi

echo ""
echo "Tiramisu portable ready."
echo "  Home:      $TIRAMISU_HOME"
echo "  Database:  $TIRAMISU_HOME/tiramisu.db"
echo "  Knowledge: $TIRAMISU_HOME/knowledge/"
echo ""
echo "Next: edit $ENV_FILE and add your ANTHROPIC_API_KEY"
