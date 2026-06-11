# Agent personas

This directory holds the **system prompts** for each Tiramisu agent. Each
file is loaded by `scripts/steering.py` and composed with the universal
steering docs at call time. A persona file defines **who the agent is and
what they do**. It must not define how they do it (that's the script's job)
or universal coding rules (those live in `engineering-principles.md` /
`code-style.md`).

---

## The pattern — every persona file follows this shape

```markdown
# <Name> — <one-line role>

<One paragraph persona. Pastry/pet theme. Voice. Worldview.>

## Your job

- <Bulleted responsibilities. Specific verbs.>
- <…>

## Communication style

- <How they speak / write. Concrete adjectives.>
- <Specific patterns: "leads with conclusion, then evidence" etc.>

## Voice examples

- "<sample line in this agent's voice>"
- "<another sample>"
- "<a sample of how they push back>"

## What <name> does NOT do

- <Explicit non-responsibilities. The boundary with other agents.>
- <…>

> Status: planned / active
```

The status line at the bottom is optional. Use `planned -- not yet wired
into the t CLI` for agents whose CLI surface doesn't exist yet.

---

## Why each section matters

| Section | Why it matters |
|---|---|
| **Persona paragraph** | Gives the agent personality and a worldview. Personality makes the role memorable; worldview makes responses consistent across calls. |
| **Your job** | Bounds the agent. If a request asks for something outside this list, the agent should push back. |
| **Communication style** | Controls *how* the response reads. Specific adjectives ("direct, occasionally annoyed") beat generic ones ("helpful"). |
| **Voice examples** | Show the model exactly what its output looks like. The most effective part of the prompt — concrete examples outperform any amount of abstract instruction. Include at least one "pushing back" example so the agent knows it has permission to disagree. |
| **What X does NOT do** | Crisp role boundaries. Stops scope creep. If two agents start doing the same thing, fix it here. |

---

## Anti-patterns to avoid when writing a persona

| ❌ Don't | ✅ Do |
|---|---|
| Inline universal coding rules ("always use guard clauses") | Put universal rules in `engineering-principles.md` |
| Inline a per-language style rule ("Python public functions need docstrings") | Put per-language rules in `code-style.md` |
| Describe HOW the agent works ("reads `git diff --cached` then…") | Describe WHAT they do; HOW lives in the script |
| Use corporate filler ("strives to provide…") | Use concrete verbs and specific examples |
| Bullet-list every detail ("flags long lines, flags magic numbers, flags …") | Capture the principle, let the engineering docs supply the details |
| Generic voice ("clear and concise") | Specific voice ("direct, slightly annoyed, slow-blinks approval") |
| Persona changes that contradict other agents | Coordinate via the "does NOT do" boundary |

---

## When to add a new persona

Add a new persona when there's a **distinct job** the existing crew doesn't
cover. Don't add a new agent for a feature variant of an existing role.

| Existing agent | Add a new one when… |
|---|---|
| Cookie reviews code | …you need a *different* kind of review (e.g. security audit, design review). Otherwise extend Cookie. |
| Éclair writes code | …you need a different writing style (e.g. test-only writer, infra-only writer). |
| Croissant scopes tasks | …you need a different planning style (e.g. SRE incident response). |
| Madeleine surfaces patterns | …you need a different kind of insight (e.g. dependency drift, code-aging metrics). |

If unsure, write the persona first as a "planned" stub and iterate.

---

## After creating a new persona

1. Add the agent to `scripts/personas.py` — pet emoji + pastry emoji + color
2. Add the row to the README crew table
3. If the persona is going to be invoked from the CLI, also follow the
   "Add a new `t <command>`" recipe in `CLAUDE.md` §5
4. Mark as `planned` in the persona file until step 3 is done

---

## Worked example: read `cookie.md` and `eclair.md`

These two are the most-used personas. They're the best reference for the
pattern. Read them top-to-bottom — they each fit on one screen — then
write your new persona at the same density.
