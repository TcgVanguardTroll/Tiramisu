# Bootstrap Tiramisu portable on Windows - run once after cloning
#Requires -Version 5.1
param(
    [string]$TiramisuHome = "$env:USERPROFILE\.tiramisu"
)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> Bootstrapping Tiramisu at $TiramisuHome"

# --- directories ---
foreach ($dir in @("knowledge","knowledge_candidates","prompts","config","state")) {
    New-Item -ItemType Directory -Force -Path "$TiramisuHome\$dir" | Out-Null
}

# --- copy agent prompts ---
$agentsDir = Join-Path $ScriptDir "agents"
if (Test-Path $agentsDir) {
    Copy-Item "$agentsDir\*.md" "$TiramisuHome\prompts\" -Force
    Write-Host "    Copied agent prompts"
}

# --- copy config docs ---
$configDocs = @("code-style.md","communication-style.md","engineering-principles.md","agent-architecture.md","memory-system.md")
foreach ($doc in $configDocs) {
    $src = Join-Path $ScriptDir $doc
    if (Test-Path $src) { Copy-Item $src "$TiramisuHome\config\" -Force }
}
Write-Host "    Copied config docs"

# --- Python deps ---
Write-Host "==> Installing Python dependencies"
# Resolve python: prefer versioned installs over Microsoft Store stubs
$pythonExe = @("C:\Python314\python.exe","C:\Python313\python.exe","C:\Python312\python.exe","C:\Python311\python.exe","C:\Python310\python.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $pythonExe) { $pythonExe = "python" }
Write-Host "    Using $pythonExe"
& $pythonExe -m pip install -r "$ScriptDir\requirements.txt" --quiet

# --- SQLite schema ---
Write-Host "==> Initialising SQLite database"
$schemaPath = Join-Path $ScriptDir "schema.sql"
& $pythonExe - @"
import sqlite3, os
db_path = os.path.expanduser(r"$TiramisuHome\tiramisu.db")
db = sqlite3.connect(db_path)
with open(r"$schemaPath") as f:
    db.executescript(f.read())
db.close()
print(f"    Database ready at {db_path}")
"@

# --- ChromaDB vector store ---
Write-Host "==> Initialising ChromaDB vector store"
$indexPath = "$TiramisuHome\.second_brain_index"
& $pythonExe - @"
import chromadb
client = chromadb.PersistentClient(path=r"$indexPath")
client.get_or_create_collection("knowledge")
print(f"    Vector store ready at $indexPath")
"@

# --- .env template ---
$envFile = "$TiramisuHome\.env"
if (-not (Test-Path $envFile)) {
    @"
# Tiramisu environment - fill in your keys, never commit this file
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
TIRAMISU_HOME=$TiramisuHome
"@ | Out-File -FilePath $envFile -Encoding utf8
    Write-Host "    Created .env template at $envFile - add your API keys"
}

Write-Host ""
Write-Host "Tiramisu portable ready."
Write-Host "  Home:      $TiramisuHome"
Write-Host "  Database:  $TiramisuHome\tiramisu.db"
Write-Host "  Knowledge: $TiramisuHome\knowledge\"
Write-Host ""
Write-Host "Next: edit $envFile and add your ANTHROPIC_API_KEY"
