#!/usr/bin/env python3
"""agentSpawn hook — fast ingest of new kiro-cli session files into transcripts table. No LLM. Milliseconds."""
import json, os, sys, sqlite3
from glob import glob
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "tiramisu.db")
SESSIONS_DIR = os.path.expanduser("~/.kiro/sessions/cli/")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def detect_agent(jsonl_path: str) -> str:
    """Parse first Prompt message to guess which agent was invoked.
    Prefers the agent being directly addressed (e.g. 'tell eclair to...')
    over incidental mentions."""
    AGENTS = ("tiramisu", "mochi", "cannoli", "eclair", "madeleine", "croissant", "brioche")
    # Patterns that indicate direct addressing: "tell X", "ask X", "@X", "/agent X"
    import re
    DIRECT_PATTERNS = [re.compile(rf"(?:tell|ask|@|/agent\s+){name}\b") for name in AGENTS]
    try:
        with open(jsonl_path) as f:
            for line in f:
                msg = json.loads(line.strip())
                if msg.get("kind") == "Prompt":
                    data = msg.get("data", {})
                    for c in data.get("content", []):
                        if c.get("kind") == "text":
                            text = c["data"].lower()
                            # First pass: check for direct addressing patterns
                            for i, pat in enumerate(DIRECT_PATTERNS):
                                if pat.search(text):
                                    return AGENTS[i]
                            # Fallback: first agent name mentioned
                            for name in AGENTS:
                                if name in text:
                                    return name
                    break
    except Exception:
        pass
    return "tiramisu"


def ingest_new_sessions() -> int:
    """Find unprocessed session JSONL files, store metadata in SQLite. Returns count of unextracted."""
    if not os.path.isdir(SESSIONS_DIR):
        return 0

    with _conn() as conn:
        known = {r["session_id"] for r in conn.execute("SELECT session_id FROM transcripts").fetchall()}

        new_count = 0
        for json_file in glob(os.path.join(SESSIONS_DIR, "*.json")):
            session_id = os.path.basename(json_file).replace(".json", "")
            if session_id in known:
                continue

            jsonl_file = json_file.replace(".json", ".jsonl")
            if not os.path.exists(jsonl_file):
                continue

            try:
                meta = json.load(open(json_file))
            except Exception:
                continue

            msg_count = sum(1 for _ in open(jsonl_file))
            agent = detect_agent(jsonl_file)

            conn.execute(
                """INSERT OR IGNORE INTO transcripts
                   (session_id, agent, cwd, title, message_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, agent, meta.get("cwd"), meta.get("title"),
                 msg_count, meta.get("created_at")),
            )
            new_count += 1

        unextracted = conn.execute("SELECT COUNT(*) FROM transcripts WHERE extracted=0").fetchone()[0]
    return unextracted


if __name__ == "__main__":
    # When run as hook, read stdin event (ignored) and print status
    if not sys.stdin.isatty():
        sys.stdin.read()  # consume hook event
    count = ingest_new_sessions()
    if count:
        print(f"\n📚 {count} session(s) available for learning. Say \"learn from recent sessions\" to process them.")
    # Auto-dispatch threshold: if >10 unextracted, queue Madeleine extraction request
    if count > 10:
        with _conn() as conn:
            existing = conn.execute(
                "SELECT id FROM messages WHERE from_member='system' AND to_member='tiramisu' "
                "AND job='auto-extract' AND created_at > datetime('now', '-1 day')"
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO messages (from_member, to_member, job, message) VALUES (?, ?, ?, ?)",
                    ("system", "tiramisu", "auto-extract",
                     f"Threshold exceeded: {count} unextracted sessions. Dispatch Madeleine for memory extraction."),
                )
                print(f"⚡ Auto-dispatch: queued Madeleine extraction request ({count} sessions backlogged)")
