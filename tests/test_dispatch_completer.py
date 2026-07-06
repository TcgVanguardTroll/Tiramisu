"""
REPL command autocomplete stays in sync with the routing table.

Tiramisu's REPL is natural-language-first, but it also completes the literal
command vocabulary. That vocabulary must be DERIVED from ROUTES, not a
hand-maintained list — the old PHRASE_STARTERS subset had silently drifted,
leaving brainstorm / pr / chat / learn / reflect / research / onboard with no
tab-completion at all. These tests pin "autocomplete covers every route (+ key
subcommands)" so a new command can't regress it.
"""
import dispatch


def _texts(prefix: str) -> list[str]:
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import InMemoryHistory

    comp = dispatch.TiramisuCompleter(InMemoryHistory())
    doc = Document(prefix, len(prefix))
    return [c.text for c in comp.get_completions(doc, None)]


def test_command_completions_cover_every_route():
    completions = dispatch.command_completions()
    for cmd in dispatch.ROUTES:
        assert any(c.split()[0] == cmd for c in completions), (
            f"{cmd!r} is a route but has no autocomplete entry"
        )


def test_completer_completes_partial_command():
    # 'brain' completed nothing before (missing from the old hand list).
    assert any(t.startswith("brainstorm") for t in _texts("brain"))


def test_completer_completes_known_subcommands():
    assert any(t.startswith("learn search") for t in _texts("learn s"))
    assert any(t.startswith("research benchmark") for t in _texts("research b"))


def test_completer_empty_input_yields_nothing():
    assert _texts("") == []


def test_every_route_is_tab_completable_from_prefix():
    # Anti-drift at the completer level: a short prefix of each command surfaces it.
    for cmd in dispatch.ROUTES:
        prefix = cmd[:3]
        assert any(t.split()[0] == cmd for t in _texts(prefix)), (
            f"typing {prefix!r} did not offer {cmd!r}"
        )
