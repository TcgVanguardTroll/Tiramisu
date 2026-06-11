#!/usr/bin/env python3
"""
Brainstorm with Mochi — stress-test an idea BEFORE it becomes a scoped task.

Mochi generates multiple distinct angles, hunts the hidden assumption, and
always names the boring alternative. Use this upstream of `t task`: bounce
the idea first, scope the survivor.

Usage:
    t brainstorm "should auth live in middleware or the handler?"
    t brainstorm                      # interactive prompt
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream_markdown, DEFAULT_MODEL
from steering import load_steering
from personas import pair as persona_pair

MAX_TRANSCRIPT_CHARS = 24_000   # keep follow-up context bounded

PROMPT_TEMPLATE = """\
I want to brainstorm this before committing to anything: {topic}

Give me:

1. **Clarifying questions** — only if the idea is genuinely ambiguous (max 3,
   skip the section entirely if it isn't).
2. **At least 3 distinct angles** — numbered, genuinely different approaches,
   not variations of one. One line each on the main tradeoff.
3. **The hidden assumption** — the thing I'm taking for granted that could
   sink this.
4. **The boring alternative** — the unexciting option that might just be
   correct.
5. **Your pick** — which one you'd choose and why, in 2-3 sentences. Don't
   pretend it's the only choice.
"""

FOLLOWUP_TEMPLATE = """\
We're mid-brainstorm. Here's the conversation so far:

--- TRANSCRIPT ---
{transcript}
--- END TRANSCRIPT ---

My follow-up: {question}

Respond in the same spirit — options over prose walls, name the tradeoff,
flag what you'd pick. If I've decided, stop generating alternatives and
help me sharpen the choice instead.
"""


def followup_loop(transcript: str, system: str) -> None:
    print("\n💬 Keep bouncing — ask a follow-up, push back, or pick a direction.")
    print("   (Enter a blank line to leave)\n")
    while True:
        try:
            question = input("bounce › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print()
        reply = invoke_stream_markdown(
            prompt=FOLLOWUP_TEMPLATE.format(
                transcript=transcript[-MAX_TRANSCRIPT_CHARS:],
                question=question,
            ),
            system=system,
            model=DEFAULT_MODEL,
            max_tokens=8000,
            thinking=True,
        )
        transcript += f"\n\nUSER: {question}\n\nMOCHI: {reply}"
        print()


def main():
    system = load_steering(
        agent="mochi",
        languages=None,
        include_engineering=False,      # Mochi diverges; style rules constrain
        include_universal_style=False,
        include_preferences=True,
    )

    print("=" * 60)
    print(f"{persona_pair('mochi')}  Mochi — Brainstorm Session")
    print("=" * 60)
    print()

    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:]).strip()
        print(f"Topic: {topic}\n")
    else:
        print("What do you want to bounce around? An idea, a decision, a doubt.\n")
        try:
            topic = input("Topic: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

    if not topic:
        print("No topic provided. Exiting.")
        sys.exit(0)

    print(f"\n[mochi] {persona_pair('mochi')} Bouncing...\n")
    print("-" * 60)

    opening = invoke_stream_markdown(
        prompt=PROMPT_TEMPLATE.format(topic=topic),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=8000,
        thinking=True,
    )

    print("-" * 60)

    transcript = f"USER TOPIC: {topic}\n\nMOCHI: {opening}"
    followup_loop(transcript, system)

    print(f"\n{persona_pair('mochi')}  Good bounce. When you've picked a "
          f"direction: `t task \"...\"` to scope it.\n")


if __name__ == "__main__":
    main()
