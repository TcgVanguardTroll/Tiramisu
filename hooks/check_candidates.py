#!/usr/bin/env python3
"""Check for pending knowledge candidates on Madeleine spawn. Auto-dispatches when threshold exceeded."""
import glob, os, sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES_DIR = os.path.join(_ROOT, 'shared_workspace', 'knowledge_candidates')
EXCLUDE = {'TEMPLATE.md', 'triage_log.md', 'README.md'}
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tiramisu.db')
THRESHOLD = 3

if os.path.isdir(CANDIDATES_DIR):
    candidates = [f for f in glob.glob(os.path.join(CANDIDATES_DIR, '*.md'))
                  if os.path.basename(f) not in EXCLUDE]
    if candidates:
        count = len(candidates)
        print(f'[knowledge-triage] {count} candidate(s) pending review in shared_workspace/knowledge_candidates/')
        if count >= THRESHOLD:
            conn = sqlite3.connect(DB_PATH)
            existing = conn.execute(
                "SELECT id FROM messages WHERE from_member='system' AND to_member='tiramisu' "
                "AND job='auto-triage' AND created_at > datetime('now', '-1 day')"
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO messages (from_member, to_member, job, message) VALUES (?, ?, ?, ?)",
                    ("system", "tiramisu", "auto-triage",
                     f"{count} pending knowledge candidates need triage. Dispatch Madeleine for knowledge-triage."),
                )
                conn.commit()
                print(f"⚡ Auto-dispatch: queued Madeleine triage request ({count} candidates)")
            conn.close()
