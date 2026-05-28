#!/usr/bin/env python3
"""
Tiramisu reflect -- weekly self-improvement cycle.

Reads accumulated learnings and produces:
  1. A factual report on what's happened (stats only, no LLM)
  2. An LLM-generated analysis with proposed preferences and prompt edits

Usage:
    t reflect           # last 30 days
    t reflect 7         # last 7 days
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream, DEFAULT_MODEL
import memory


REFLECT_PROMPT = """\
You are Madeleine, the Tiramisu knowledge keeper. The user has been using their
agents (Cookie for review, Eclair for commit messages, Croissant for task scope)
for {days} days. Here is the accumulated data.

Your job: produce a tight, honest reflection. Sections:

1. **What's working** -- be specific, cite numbers from the data
2. **What's miscalibrated** -- patterns where the agents are off
3. **Proposed preferences to add** -- exact strings to run as `t learn "..."`,
   ONLY if a pattern is clear (>= 3 occurrences). Be conservative.
4. **Proposed agent prompt edits** -- specific, surgical changes. Skip if data is thin.

Be honest if the data is too sparse to draw conclusions. Do not invent patterns.

---

## Cookie review stats
{review_stats}

## Eclair commit draft stats
{draft_stats}

## What the user has overridden in Cookie reviews
{overrides}

## Recent committed messages (the user's actual style)
{recent_finals}

## Active preferences already in place
{preferences}
"""


def format_review_stats(stats):
    reviews = stats.get("reviews", {})
    if not reviews:
        return "  (no reviews logged yet)"
    total = sum(reviews.values())
    lines = [f"  Total reviews: {total}"]
    for outcome, count in sorted(reviews.items()):
        pct = (count / total * 100) if total else 0
        lines.append(f"  - {outcome}: {count} ({pct:.0f}%)")
    return "\n".join(lines)


def format_draft_stats(stats):
    d = stats.get("drafts", {})
    total = d.get("total", 0)
    if total == 0:
        return "  (no commit drafts logged yet)"
    avg_sim = d.get("avg_similarity", 0)
    accepted = d.get("accepted_as_is", 0)
    accept_pct = (accepted / total * 100) if total else 0
    return (
        f"  Total drafts: {total}\n"
        f"  Avg similarity (draft vs final): {avg_sim:.2f}\n"
        f"  Accepted as-is (>=0.85 sim): {accepted} ({accept_pct:.0f}%)"
    )


def format_overrides(stats):
    overrides = stats.get("overrides", [])
    if not overrides:
        return "  (no overrides yet)"
    return "\n".join(f"  - {o[:250]}" for o in overrides[:20])


def format_recent_finals(stats):
    finals = stats.get("recent_finals", [])
    if not finals:
        return "  (no commits logged yet)"
    return "\n\n---\n".join(f.strip()[:400] for f in finals[:10])


def format_preferences():
    prefs = memory.get_active_preferences()
    if not prefs:
        return "  (none -- the user hasn't added any preferences)"
    return "\n".join(f"  [{p['category']}] {p['text']}" for p in prefs)


def main():
    days = 30
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print(f"[reflect] Invalid day count: {sys.argv[1]}")
            sys.exit(1)

    stats = memory.stats_since(days)
    if "error" in stats:
        print(f"[reflect] Could not read memory: {stats['error']}")
        sys.exit(1)

    # Print the factual report
    print(f"\n{'='*60}")
    print(f"  Tiramisu Reflection -- last {days} days")
    print("=" * 60)

    print("\n## Cookie reviews")
    print(format_review_stats(stats))

    print("\n## Eclair drafts")
    print(format_draft_stats(stats))

    print("\n## Recent overrides")
    print(format_overrides(stats))

    print("\n## Active preferences")
    print(format_preferences())

    # If data is genuinely sparse, skip the LLM call
    total_signals = (
        sum(stats.get("reviews", {}).values())
        + stats.get("drafts", {}).get("total", 0)
    )
    if total_signals < 3:
        print("\n" + "-" * 60)
        print("\nNot enough data yet for meaningful analysis.")
        print("Use the agents a bit more and try `t reflect` again.\n")
        return

    # LLM analysis
    print("\n" + "=" * 60)
    print("  Madeleine's analysis")
    print("=" * 60 + "\n")

    agent_file = ROOT / "agents" / "madeleine.md"
    system = agent_file.read_text(encoding="utf-8") if agent_file.exists() else None

    invoke_stream(
        prompt=REFLECT_PROMPT.format(
            days=days,
            review_stats=format_review_stats(stats),
            draft_stats=format_draft_stats(stats),
            overrides=format_overrides(stats),
            recent_finals=format_recent_finals(stats),
            preferences=format_preferences(),
        ),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=2048,
    )
    print()


if __name__ == "__main__":
    main()
