# Tiramisu — Working on the project

If you (or an AI agent) are about to change code in this repo, these are the
docs that actually matter, in order of read-importance:

1. **[CLAUDE.md](../CLAUDE.md)** — the project's load-bearing invariants (the
   nine architectural rules that hold the codebase together). Anyone writing
   code here should have read this first.

2. **[INVARIANTS.md](INVARIANTS.md)** — the rules the test suite enforces:
   path sandboxing, confirmation gating, router fallbacks, persona
   uniqueness, steering composition order, memory failure isolation,
   schema discipline, cross-platform CI. Each section says what to do if
   you find yourself wanting to relax the rule.

3. **[agents/README.md](../agents/README.md)** — how to write a Tiramisu
   persona. The template, the anti-patterns, and how the personas compose
   with the shared steering docs.

4. **[engineering-principles.md](../steering/engineering-principles.md)** — universal
   design principles, distilled from canonical books. These get auto-injected
   into every agent's system prompt, so they're not just suggestions — they
   shape what the agents themselves do.

5. **[code-style.md](../steering/code-style.md)** — per-language style. Filtered by
   the file extensions in scope, then injected into the system prompt.

6. **[communication-style.md](../steering/communication-style.md)** — tone, commit
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

Tiramisu has a **108-test pytest suite** in `tests/` covering safety
invariants, router behavior, persona uniqueness, steering composition,
memory CRUD + migrations, and research source config. It runs in ~10s
with no API calls (the `mock_invoke` fixture replaces `llm.invoke`).

```sh
pip install -r requirements-dev.txt
pytest tests/ -v
```

CI runs the full matrix on every push and PR — Ubuntu / macOS / Windows
× Python 3.12 / 3.13. See `.github/workflows/test.yml`. The CI badge at
the top of the README reflects current status.

When you add tests:

- Put them in `tests/` (mirror the source layout — `test_<module>.py`)
- Use pytest fixtures from `conftest.py` instead of rolling your own
  monkey-patching: `tmp_tiramisu_home`, `tmp_workspace`, `mock_invoke`,
  `clear_steering_cache`. The fixtures handle the import-time capture
  gotchas (see the docstrings).
- Never call the real Anthropic API. `mock_invoke` patches every
  consumer module's local `invoke` reference (not just `llm.invoke`),
  because `from llm import invoke` captures the function at import time.
- Read [INVARIANTS.md](INVARIANTS.md) before adding tests on the
  dangerous tool surfaces (`chat.py`, `implement.py`) — the rules
  there are not negotiable and the existing tests show the patterns.

When you change `learnings.db` schema:

- **Append a migration to `MIGRATIONS` in `scripts/memory.py`.** Never
  edit an existing migration — users with old DBs have already applied
  the original version.
- Add a test in `tests/test_memory.py` that asserts the new column /
  index actually exists after migration.
- See [INVARIANTS.md §7](INVARIANTS.md#7-schema-discipline) for the
  full ruleset.

## Commit hygiene

- Conventional commits: `type(scope): subject`
- Imperative subject under 72 chars, no trailing period
- Body explains the **why**, not the diff
- Include `Co-Authored-By:` for AI-assisted commits — be honest, don't hide it
- One logical change per commit
