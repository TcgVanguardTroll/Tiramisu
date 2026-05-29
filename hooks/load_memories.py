#!/usr/bin/env python3
"""agentSpawn hook — loads agent memories with tier-based token budgets."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from second_brain.memory import load_memories_for_agent, get_memories

event = json.loads(sys.stdin.read())
agent = os.environ.get("TIRAMISU_AGENT_NAME", "tiramisu")
load_all = os.environ.get("TIRAMISU_LOAD_ALL", "0") == "1"

if load_all:
    # Legacy mode: dump everything (Mochi uses this)
    # Sort by memory_type priority (core first), then scope (global first), then importance desc
    TYPE_ORDER = {"core": 0, "procedural": 1, "semantic": 2, "episodic": 3, "job-log": 4}
    SCOPE_ORDER = {"global": 0, "agent": 1, "tier2": 2, "job": 3}
    memories = get_memories(agent=None, limit=100)
    memories.sort(key=lambda m: (
        TYPE_ORDER.get(m.get("memory_type", "semantic"), 9),
        SCOPE_ORDER.get(m.get("scope", "agent"), 9),
        -(m.get("importance") or 0.5),
    ))
    if memories:
        print(f"## {agent}'s memories ({len(memories)} entries)")
        for m in memories:
            print(f"- [{m['category']}] {m['content']}")
        print("\nIMPORTANT: Save new decisions, preferences, corrections, and facts using python3 with: "
              "from second_brain.memory import save_memory; save_memory('<agent>', '<category>', '<content>', source_job='<job>')")
    else:
        print(f"No memories found for {agent}.")
else:
    # Tier-based loading
    print(load_memories_for_agent(agent))

# Load open todos (tiramisu only)
if agent == "tiramisu":
    import sqlite3
    db = os.path.join(os.path.dirname(__file__), "..", "tiramisu.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    todos = [dict(r) for r in conn.execute("SELECT * FROM todos WHERE status != 'done' ORDER BY priority='high' DESC, priority='medium' DESC, id").fetchall()]
    conn.close()
    if todos:
        print(f"\n## Open todos ({len(todos)})")
        for t in todos:
            print(f"- [{t['priority']}] #{t['id']}: {t['title']} (assigned: {t['assigned_to'] or 'unassigned'})")
