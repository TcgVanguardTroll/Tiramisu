# Tiramisu Invariants

This is the human-readable counterpart to the test suite. Each section
states an invariant the code *must* uphold, why a regression would matter,
and points to the test that enforces it plus the code where it lives. If
you're adding a feature and you find yourself relaxing one of these rules,
stop and re-read the **Why it matters** column first.

`tests/` runs in ~10s and gates every PR via `.github/workflows/test.yml`.
254 tests across 17 modules enforce the surfaces below.

---

## 1. Path sandboxing

**Invariant.** `chat.py`'s file-write tools (`edit_file`, `write_file`)
refuse any path that resolves outside the current working directory.
`../../etc/passwd` and absolute paths to system files are blocked
*before* the file is read or written.

**Why it matters.** Tiramisu hands an LLM-driven agent the ability to
write to disk. If the model hallucinates `~/.ssh/config` or
`C:\Windows\System32\drivers\etc\hosts` as a target, the sandbox is the
only thing standing between the hallucination and real damage. A
confirmation prompt is *not* enough — a user who's been clicking 'y' for
20 routine edits will hit 'y' on the 21st without reading.

**Enforced by.** `tests/test_safety.py` —
`test_validate_path_rejects_absolute_path_outside_cwd`,
`test_validate_path_blocks_dotdot_escape`,
`test_edit_file_refuses_path_outside_cwd_even_with_yes`,
`test_write_file_refuses_path_outside_cwd_even_with_yes`.

**Lives in.** `scripts/chat.py` — `_validate_path()` and the calls to
it from `execute_tool()`.

---

## 2. Confirmation gating

**Invariant.** Every state-changing tool (`edit_file`, `write_file`,
`run_shell`) prompts the user before executing. Empty input falls back
to the documented default (`y` for file edits, `n` for shell). Ctrl+C
and EOF both mean "no" regardless of the default.

**Why it matters.** The shell tool can do anything `write_file` can do,
plus delete files, exfiltrate data, and call out to the network. The
empty-input-defaults-to-no rule for shell is the single most
load-bearing safety guarantee in the codebase: a piped `echo "" | t
chat` must never run a shell command without an explicit 'y'.

**Enforced by.** `tests/test_safety.py` —
`test_confirm_*` (8 tests covering Y/n/empty/Ctrl+C/EOF/case/garbage),
`test_shell_command_defaults_to_no_on_empty_input`.

**Lives in.** `scripts/chat.py` — `_confirm()` and the shell-tool
default of `default_yes=False`.

---

## 3. Router defensive parsing

**Invariant.** `dispatch.route()` accepts whatever string Haiku returns
and always produces one of the canonical commands in `ROUTES`. Trailing
punctuation, quotes, parens, surrounding prose, case, and whitespace
are all stripped. Empty / whitespace-only / API-failure responses fall
back to `task` (the safe default — scoping doesn't write files).

**Why it matters.** Haiku is fast and cheap but produces stylistic
variance the router has to absorb. A crash here kills the REPL the
moment the API hiccups, which is exactly when the user *least* wants
to debug their tooling.

**Enforced by.** `tests/test_dispatch_router.py` — 15 tests covering
every malformation we've seen the LLM produce, plus the empty/whitespace/
exception fallbacks.

**Lives in.** `scripts/dispatch.py` — `route()` and the `ROUTES` dict.

---

## 4. Persona uniqueness

**Invariant.** Every agent in `PERSONAS` has unique `pastry` emoji.
Pet emoji are deliberately grouped (multiple 🐶 dogs, multiple 🐱 cats).
Lookups for unknown agent names return empty strings (never raise).

**Why it matters.** The pastry is the visual differentiator promised
by the README. Duplicates would silently make two agents
indistinguishable in output. The unknown-agent-returns-empty rule is
load-bearing because banners concatenate `pet(name) + pastry(name)`
into print statements — a `KeyError` would crash the banner for any
typo'd agent name.

**Enforced by.** `tests/test_personas.py` — 9 tests, especially
`test_pastries_are_unique`, `test_pet_emoji_groups_make_sense`, and
the unknown-agent fallback tests.

**Lives in.** `scripts/personas.py`.

---

## 5. Steering composition order

**Invariant.** `load_steering()` builds the system prompt in this
order, and only this order:

```
  persona  ->  engineering  ->  code-style  ->  communication
           ->  learned  ->  preferences  ->  repo-overrides
```

(`learned` is `steering/learned/*.md` — docs adopted from research via
`t research apply`. It sits before preferences and repo overrides so the
more specific layers still win on conflict.)

Per-repo `.tiramisu/*.md` overrides are last so they win when they
conflict with anything earlier (this is the documented precedence).

**Why it matters.** This composition feeds every agent invocation. A
silent reorder (e.g., preferences moving above engineering principles)
would change every agent's behavior in ways no other test would catch
because the rest of the codebase reads the composed string as opaque.
Repo overrides specifically MUST come last — a user's project-specific
"prefer tabs" rule has to override the universal "prefer 4-space
indent" rule from `code-style.md`.

**Enforced by.** `tests/test_steering.py` —
`test_steering_section_order`, `test_overrides_come_last`,
`test_persona_appears_before_engineering`,
`test_learned_layer_included_and_ordered`, plus toggle and language-filter
tests for each layer.

**Lives in.** `scripts/steering.py` — `load_steering()`.

---

## 6. Memory failure isolation

**Invariant.** Every write function in `memory.py` is wrapped in
`@_safe`. `_safe` catches any exception, logs a warning to stderr, and
returns `None`. Read functions individually wrap their bodies in
`try/except` and return an empty default (`[]` or `{}`).

**Why it matters.** `memory.py` runs from inside git hooks. If a DB
hiccup (corrupted file, disk full, locking contention) propagates up,
**every commit on the user's machine breaks** until they manually
diagnose the DB. The cost of losing one log entry is dwarfed by the
cost of bricking the commit chain. Fail-soft is the rule.

**Enforced by.** `tests/test_memory.py` —
`test_safe_decorator_swallows_exceptions`,
`test_safe_decorator_returns_value_on_success`.

**Lives in.** `scripts/memory.py` — `_safe()` decorator + the
`try/except` blocks in read helpers.

---

## 7. Schema discipline

**Invariant.** Every change to `learnings.db` schema goes through the
migration framework in `memory.py`. Migrations are append-only —
**never edit or delete an applied migration**. Version numbers are
contiguous starting from `2` (v1 is the baseline `SCHEMA`). Migrations
are idempotent: re-running them on an already-migrated DB is a no-op.

**Why it matters.** Users upgrade Tiramisu by `git pull`. Their
existing `learnings.db` has months of data and is on the old schema.
Without versioning, code that reads a new column crashes on their DB;
with versioning, the new column is added before the new code reads
from it. Editing an old migration would silently desync users whose
DB already ran the original version.

**Rules for adding a migration.**
1. Pick the next integer version (`max(MIGRATIONS) + 1`).
2. Write SQL that's safe even if accidentally re-run
   (`CREATE INDEX IF NOT EXISTS`, `ALTER TABLE ADD COLUMN ... DEFAULT`,
   etc.). The framework guards against re-runs but defensive SQL is
   free insurance.
3. Add a test in `tests/test_memory.py` that asserts the new column /
   index / constraint actually exists after migration.
4. Add a test that proves a legacy DB (pre-versioning) is upgraded
   without data loss, similar to
   `test_legacy_db_without_version_table_gets_baselined`.

**Enforced by.** `tests/test_memory.py` — 8 migration tests, especially
`test_migrations_are_idempotent`, `test_migrations_versions_are_contiguous`,
`test_legacy_db_without_version_table_gets_baselined`.

**Lives in.** `scripts/memory.py` — `MIGRATIONS` list, `_apply_migrations()`,
`get_conn()`.

---

## 8. Cross-platform compatibility

**Invariant.** The full test suite passes on Ubuntu, macOS, and Windows
under Python 3.12 and 3.13. CI matrix gates every PR; a failure on any
of the six cells blocks merge.

**Why it matters.** Tiramisu is "Windows-first; macOS / Linux supported
via POSIX shell shims" — that statement was unverified for the first
~38 commits of the repo's life. Now it's enforced. Path-separator bugs,
shell-quoting bugs, line-ending bugs, and Python version drift all
surface in CI instead of in a user's first install.

**Enforced by.** `.github/workflows/test.yml` — `fail-fast: false`
matrix, 3 OS × 2 Python, runs on push / PR / manual dispatch.
Deprecation warnings are promoted to errors so future deprecations
break CI immediately (this caught the `datetime.utcnow()` bug during
Phase 2).

**Lives in.** `.github/workflows/test.yml`.

---

## 9. Research-apply sandbox

**Invariant.** `t research apply` may only (a) edit the three shared
steering files (`engineering-principles.md`, `code-style.md`,
`communication-style.md`) and (b) create new files inside
`steering/learned/`. Proposed targets are an allowlist of bare names —
path-like targets (`../`, absolute, `agents/…`) are rejected, not
normalized. Persona files are never written. Every application requires
an explicit per-edit `y`; empty input, EOF, and Ctrl+C decline. The
`.applied` sidecar makes re-runs idempotent.

**Why it matters.** This is the only surface where LLM-authored text
flows back into the prompts that steer every agent. Without the sandbox,
a hallucinated (or prompt-injected, via a fetched web page) "proposed
update" could rewrite a persona or any file in the repo. The allowlist +
per-edit confirmation keeps the loop "self-improving with a human gate"
rather than self-mutating (CLAUDE.md §4.3).

**Enforced by.** `tests/test_research_apply.py` — target-allowlist,
escape-rejection, learned-dir-confinement, confirmation-default-no, and
all-no-changes-nothing end-to-end tests.

**Lives in.** `scripts/research_apply.py` — `resolve_edit_target()`,
`learned_doc_path()`, `_confirm()`.

---

## 10. `<private>` redaction never persists secrets

**Invariant.** Every free-text field written to `learnings.db` (preferences,
reviews, commit drafts/finals, task plans, override snippets) passes through
`memory.redact_private()` first. Content inside `<private>…</private>` is
replaced with `[redacted]` before insert, so it lands in neither the source
table nor the FTS index. A dangling, unclosed `<private>` redacts to
end-of-string. Redaction is fail-soft: non-strings and tag-free text pass
through unchanged.

**Why it matters.** `learnings.db` is durable and now full-text searchable;
a secret pasted into a preference or commit message would otherwise persist
indefinitely and surface in `t learn search`. `<private>` gives the user a
deterministic, pre-storage kill switch — the redaction happens at the write
boundary, not at display time, so the secret never touches disk.

**Enforced by.** `tests/test_memory_learnings.py` — `test_redact_*`,
`test_preference_stored_with_secret_redacted`,
`test_redacted_content_is_not_searchable`.

**Lives in.** `scripts/memory.py` — `redact_private()` and the write helpers
that call it.

---

## Adding a new invariant

If you're tightening a rule that future code must follow:

1. Write the test first. The invariant doesn't exist until something
   can detect a violation.
2. Add a section here using the same five-line shape: invariant,
   why it matters, enforced by, lives in.
3. If you're adding a new dangerous tool surface (something with
   asymmetric blast radius like the file/shell tools in §1–2),
   prefer to wire it through the existing `_validate_path()` /
   `_confirm()` helpers rather than re-implementing the gates. One
   sandbox per process is easier to audit than three.

If you're relaxing a rule, ask: who is this protection for, and what
do they lose if it goes away? "I'm the only user" is rarely the right
answer — these invariants exist precisely because the LLM is the
unreliable component, not the human.
