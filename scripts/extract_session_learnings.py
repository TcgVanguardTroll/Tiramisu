#!/usr/bin/env python3
"""
Extract learnings from recent kiro-cli sessions into steering docs.

Runs after ingest_sessions.py. Reads the latest session transcripts,
identifies patterns/corrections/decisions, and appends them to the
appropriate steering file.

Categories:
- code_style: coding patterns, checkstyle fixes, test conventions → code style.md
- corrections: things that failed and the fix → memories.md (Corrections & Lessons)
- decisions: architectural or tooling choices → memories.md (Decisions Made)
- domain: system knowledge learned → domain steering files
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TIRAMISU_ROOT = Path(__file__).resolve().parent.parent
CLI_SESSIONS_DIR = Path.home() / ".kiro/sessions/cli"
IDE_SESSIONS_DIR = Path.home() / "Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent"
STEERING_DIR = Path.home() / ".kiro/steering"
STATE_FILE = TIRAMISU_ROOT / "state/extract_learnings_state.json"
LOG_FILE = TIRAMISU_ROOT / "logs/extract_learnings.log"

# Only process sessions from the last 30 minutes (matches cron interval)
MAX_AGE_MINUTES = 30

# Minimum session size to bother extracting from (skip trivial interactions)
MIN_SESSION_LINES = 20


def log(msg):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed_sessions": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_recent_sessions():
    """Find session files modified in the last MAX_AGE_MINUTES from both CLI and IDE."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_AGE_MINUTES)
    sessions = []

    # CLI sessions: ~/.kiro/sessions/cli/*.jsonl
    if CLI_SESSIONS_DIR.exists():
        for f in CLI_SESSIONS_DIR.glob("*.jsonl"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                line_count = sum(1 for _ in open(f))
                if line_count >= MIN_SESSION_LINES:
                    sessions.append(f)

    # IDE sessions: ~/Library/.../kiro.kiroagent/<hash>/*.chat
    if IDE_SESSIONS_DIR.exists():
        for f in IDE_SESSIONS_DIR.rglob("*.chat"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                size = f.stat().st_size
                if size > 5000:  # Skip trivial IDE sessions
                    sessions.append(f)

    return sessions


def extract_learnings(session_path):
    """Use Bedrock to extract learnings from a session transcript."""
    # Read last 200 lines (enough context without overwhelming)
    lines = session_path.read_text().splitlines()[-200:]
    transcript = "\n".join(lines)

    prompt = f"""Analyze this kiro-cli session transcript and extract any learnings that should be persisted.

TRANSCRIPT (last 200 lines):
{transcript}

OUTPUT FORMAT — respond with ONLY a JSON object, no markdown:
{{
  "code_style": ["pattern 1", "pattern 2"],
  "corrections": ["lesson 1"],
  "decisions": ["decision 1"],
  "anti_patterns": ["anti-pattern 1"],
  "in_progress": "brief description of what was being worked on (1 sentence)"
}}

RULES:
- code_style: new coding conventions, checkstyle fixes, test patterns discovered
- corrections: things that failed and the working fix (format: "X doesn't work — do Y instead")
- decisions: tooling or architectural choices made
- anti_patterns: things the AI suggested that the user rejected or corrected, with the reason WHY it was wrong (format: "AI suggested X but this is wrong because Y — correct approach is Z")
- in_progress: what the user was actively working on at the end of the session (for context continuity)

CRITICAL — anti_patterns detection:
Look for these signals that the AI gave bad advice:
- User says "no", "that's not right", "don't do that", "that won't work", "not needed"
- User corrects the AI's suggestion with a different approach
- AI proposes a complex solution and user says to keep it simple
- AI suggests changing languages/tools and user says current approach is fine
- AI over-engineers when user wanted minimal change
- AI suggests something that contradicts the codebase's existing patterns

For each anti_pattern, capture: what was suggested, why it's wrong, what's correct.

OTHER RULES:
- Only include genuinely NEW learnings — skip obvious things
- Each entry should be one concise line (like a bullet point)
- If nothing new was learned, return empty arrays
- Do NOT include learnings about the extraction process itself"""

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from llm import invoke
        output = invoke(prompt, max_tokens=512)
        start = output.find("{")
        end = output.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(output[start:end])
    except Exception as e:
        log(f"  Extraction failed: {type(e).__name__}: {e}")
        return None


def append_to_steering(learnings):
    """Append extracted learnings to the appropriate steering files."""
    changes = []

    code_style_file = STEERING_DIR / "code style.md"
    memories_file = STEERING_DIR / "memories.md"

    if learnings.get("code_style"):
        # Don't auto-append to code style — too risky. Log for manual review.
        for item in learnings["code_style"]:
            log(f"  [CODE_STYLE candidate] {item}")
            changes.append(f"code_style: {item}")

    if learnings.get("corrections"):
        content = memories_file.read_text()
        marker = "## Corrections & Lessons"
        if marker in content:
            new_items = "\n".join(f"- {item}" for item in learnings["corrections"])
            content = content.replace(marker, f"{marker}\n{new_items}")
            memories_file.write_text(content)
            for item in learnings["corrections"]:
                log(f"  [CORRECTION added] {item}")
                changes.append(f"correction: {item}")

    if learnings.get("decisions"):
        content = memories_file.read_text()
        marker = "## Decisions Made"
        if marker in content:
            new_items = "\n".join(f"- {item}" for item in learnings["decisions"])
            content = content.replace(marker, f"{marker}\n{new_items}")
            memories_file.write_text(content)
            for item in learnings["decisions"]:
                log(f"  [DECISION added] {item}")
                changes.append(f"decision: {item}")

    if learnings.get("anti_patterns"):
        content = memories_file.read_text()
        marker = "## Corrections & Lessons"
        if marker in content:
            new_items = "\n".join(f"- ANTI-PATTERN: {item}" for item in learnings["anti_patterns"])
            content = content.replace(marker, f"{marker}\n{new_items}")
            memories_file.write_text(content)
            for item in learnings["anti_patterns"]:
                log(f"  [ANTI-PATTERN added] {item}")
                changes.append(f"anti_pattern: {item}")

    # Write in-progress context for session continuity
    if learnings.get("in_progress"):
        in_progress_file = TIRAMISU_ROOT / "state" / "last_session_context.txt"
        in_progress_file.write_text(learnings["in_progress"])
        log(f"  [IN_PROGRESS] {learnings['in_progress']}")
        changes.append(f"in_progress: {learnings['in_progress']}")

    return changes


def main():
    log("Extract learnings starting...")
    state = load_state()
    sessions = get_recent_sessions()

    if not sessions:
        log("  No recent sessions to process.")
        save_state(state)
        return

    for session in sessions:
        session_id = session.stem
        if session_id in state["processed_sessions"]:
            continue

        log(f"  Processing session: {session_id}")
        learnings = extract_learnings(session)

        if learnings:
            changes = append_to_steering(learnings)
            if changes:
                log(f"  Extracted {len(changes)} learning(s)")
            else:
                log(f"  No new learnings found")
        else:
            log(f"  Extraction returned nothing")

        state["processed_sessions"].append(session_id)
        # Keep only last 50 session IDs to prevent unbounded growth
        state["processed_sessions"] = state["processed_sessions"][-50:]

    save_state(state)
    log("Extract learnings complete.")


if __name__ == "__main__":
    main()
