#!/usr/bin/env python3
"""
Tiramisu learn -- manually inject a preference that all agents respect.

Usage:
    t learn "I prefer guard clauses over nested ifs"
    t learn "Don't flag function length unless paired with low cohesion"
    t learn "Always type-annotate public Python functions"
    t learn list                # show active preferences
    t learn forget <id>         # deactivate a preference by id
    t learn search <query>      # full-text search everything the crew learned

Tip: wrap secrets in <private>...</private> and they're stripped before
anything is stored.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke, FAST_MODEL
import memory


CATEGORY_PROMPT = """\
Classify this engineering preference into one category. Reply with ONE WORD only:
  - review        (how code should be reviewed)
  - commit_msg    (how commit messages should be written)
  - task_scope    (how tasks should be scoped/planned)
  - style         (coding style, formatting, naming)
  - general       (anything else)

Preference: "{text}"

Category:"""


def categorize(text):
    """Best-effort category from the LLM. Falls back to 'general' on any failure,
    but logs the failure so silent miscategorization is debuggable."""
    try:
        result = invoke(
            prompt=CATEGORY_PROMPT.format(text=text),
            model=FAST_MODEL,
            max_tokens=10,
            temperature=0.0,
        ).strip().lower().split()[0].strip(":.,")
    except Exception as e:
        print(f"[learn] warning: categorization failed ({type(e).__name__}: {e}); "
              f"using 'general'", file=sys.stderr)
        return "general"

    valid = {"review", "commit_msg", "task_scope", "style", "general"}
    return result if result in valid else "general"


def cmd_add(text):
    if len(text) < 5:
        print("[learn] Too short. Give me a meaningful preference.")
        sys.exit(1)

    category = categorize(text)
    result = memory.add_preference(text, category=category, source="manual")
    if result == "duplicate":
        print(f"\n  Already known: {text}")
        print("  (skipped -- this preference is already active. See: t learn list)\n")
        return
    if result is None:
        print("\n  [learn] Could not save that preference (memory unavailable).\n")
        return
    # Show the redacted form so a <private> secret isn't echoed back.
    print(f"\n  Learned [{category}]: {memory.redact_private(text)}\n")
    print("  This will now be respected by every agent invocation.")
    print("  See all with: t learn list\n")


def cmd_search(query):
    query = query.strip()
    if not query:
        print("[learn] Usage: t learn search <query>")
        sys.exit(1)

    results = memory.search_learnings(query)
    if not results:
        print(f"\n  No learnings match {query!r}.")
        print("  (Search covers preferences, reviews, commit messages, and task plans.)\n")
        return

    label = {"preference": "pref", "review": "review", "commit": "commit", "task": "task"}
    print(f"\n  {len(results)} learning(s) matching {query!r}:\n")
    for r in results:
        kind = label.get(r["kind"], r["kind"])
        text = " ".join((r["text"] or "").split())
        if len(text) > 120:
            text = text[:120] + "…"
        print(f"  [{kind:7}] {text}")
    print()


def cmd_list():
    prefs = memory.get_active_preferences()
    if not prefs:
        print("\n  No preferences yet. Add one with:")
        print('    t learn "your preference here"\n')
        return

    by_cat = {}
    for p in prefs:
        by_cat.setdefault(p["category"] or "general", []).append(p)

    print(f"\n  Active preferences ({len(prefs)} total):\n")
    for cat, items in sorted(by_cat.items()):
        print(f"  [{cat}]")
        for p in items:
            print(f"    #{p['id']}  {p['text']}")
        print()


def cmd_forget(pref_id):
    try:
        pref_id = int(pref_id)
    except ValueError:
        print(f"[learn] Not a valid id: {pref_id}")
        sys.exit(1)
    memory.deactivate_preference(pref_id)
    print(f"\n  Forgot preference #{pref_id}\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    args = sys.argv[1:]

    if args[0] == "list":
        cmd_list()
    elif args[0] == "forget" and len(args) >= 2:
        cmd_forget(args[1])
    elif args[0] == "search":
        cmd_search(" ".join(args[1:]))
    else:
        text = " ".join(args).strip()
        cmd_add(text)


if __name__ == "__main__":
    main()
