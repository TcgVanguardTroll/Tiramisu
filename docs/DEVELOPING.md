# Tiramisu — Working on the project

If you (or an AI agent) are about to change code in this repo, these are the
docs that actually matter, in order of read-importance:

1. **[CLAUDE.md](../CLAUDE.md)** — the project's load-bearing invariants (the
   nine architectural rules that hold the codebase together). Anyone writing
   code here should have read this first.

2. **[agents/README.md](../agents/README.md)** — how to write a Tiramisu
   persona. The template, the anti-patterns, and how the personas compose
   with the shared steering docs.

3. **[engineering-principles.md](../engineering-principles.md)** — universal
   design principles, distilled from canonical books. These get auto-injected
   into every agent's system prompt, so they're not just suggestions — they
   shape what the agents themselves do.

4. **[code-style.md](../code-style.md)** — per-language style. Filtered by
   the file extensions in scope, then injected into the system prompt.

5. **[communication-style.md](../communication-style.md)** — tone, commit
   message format, code-review patterns.

## Common recipes

See `CLAUDE.md` §5 for the exact steps. Briefly:

- **Add a new `t <command>`** — write `scripts/<your_command>.py`, wire it
  into both `t.bat` and the POSIX `t` shell script, optionally add to the
  natural-language router in `scripts/dispatch.py`, update the README + `t help`.
- **Add a new agent** — write `agents/<name>.md` following the template in
  `agents/README.md`, register the emoji in `scripts/personas.py`, add to
  the README crew table.
- **Add a new steering layer** — decide if it's universal, per-language, or
  agent-specific; edit the right existing file. Don't create new top-level
  steering files without coordinating with `scripts/steering.py`.

## Testing posture

Tiramisu currently has **no automated test suite**. When you add tests:

- Put them in `tests/` (mirror the source layout)
- Use plain `assert` — no framework needed yet
- Prefer fast, deterministic unit tests on pure functions (`steering._parse_h2_sections`, `personas.pair`, `dispatch.route` with a mock client)
- Avoid tests that hit the real Anthropic API. Mock `llm.invoke` / `invoke_stream_markdown`.

## Commit hygiene

- Conventional commits: `type(scope): subject`
- Imperative subject under 72 chars, no trailing period
- Body explains the **why**, not the diff
- Include `Co-Authored-By:` for AI-assisted commits — be honest, don't hide it
- One logical change per commit
