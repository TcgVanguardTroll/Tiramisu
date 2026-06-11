"""
Router behavior in dispatch.py.

The router is the single point that turns natural language into one of the
~8 known t-commands. If it silently routes to an unknown command, the
subcommand exec will fail. If it crashes on a malformed LLM response, the
user's REPL dies. So: defensive parsing + safe fallbacks are load-bearing.

All tests mock llm.invoke -- no real API calls. See conftest.py for the
fixture.
"""
import pytest


# --------------------------------------------------------------------------
# Contract surface: ROUTES dict shape
# --------------------------------------------------------------------------

def test_routes_dict_has_known_commands():
    """The set of valid routes is the contract between dispatch and the
    shims. If a command is removed from ROUTES, the router can't pick it."""
    from dispatch import ROUTES
    required = {"task", "implement", "scan", "review", "pr", "chat",
                "learn", "reflect", "help"}
    missing = required - set(ROUTES.keys())
    assert not missing, f"required commands missing from ROUTES: {missing}"


def test_research_is_routable():
    """Cannoli has a CLI surface (`t research` in both dispatchers), so the
    natural-language entry must be able to reach it."""
    from dispatch import ROUTES
    assert "research" in ROUTES


def test_routes_have_non_empty_descriptions():
    """Each route's description goes into the router prompt. Empty
    descriptions would make the LLM unable to pick correctly."""
    from dispatch import ROUTES
    for cmd, desc in ROUTES.items():
        assert desc, f"route {cmd!r} has empty description"
        assert len(desc) > 10, f"route {cmd!r} has suspiciously short description"


# --------------------------------------------------------------------------
# route(): defensive parsing of the LLM response
# --------------------------------------------------------------------------

def test_router_returns_canonical_command(mock_invoke):
    from dispatch import route
    mock_invoke.set("implement")
    assert route("add a logout button") == "implement"


def test_router_returns_task_for_scoping(mock_invoke):
    from dispatch import route
    mock_invoke.set("task")
    assert route("scope adding dark mode") == "task"


def test_router_returns_chat_for_questions(mock_invoke):
    from dispatch import route
    mock_invoke.set("chat")
    assert route("what does this code do?") == "chat"


def test_router_falls_back_to_task_on_unknown_command(mock_invoke):
    """If the LLM returns something not in ROUTES, route() should fall back
    to 'task' (scoping first is the safe default per docstring)."""
    from dispatch import route
    mock_invoke.set("xyzgibberish")
    assert route("anything") == "task"


def test_router_strips_trailing_punctuation(mock_invoke):
    """LLM occasionally returns 'implement.' or 'implement,'. Must be stripped."""
    from dispatch import route
    mock_invoke.set("implement.")
    assert route("anything") == "implement"

    mock_invoke.set("task,")
    assert route("anything") == "task"

    mock_invoke.set("chat;")
    assert route("anything") == "chat"


def test_router_strips_whitespace(mock_invoke):
    """LLM might emit '  implement  ' with leading/trailing spaces."""
    from dispatch import route
    mock_invoke.set("  implement  ")
    assert route("anything") == "implement"


def test_router_takes_first_word_only(mock_invoke):
    """LLM might return 'implement -- because the user said so'.
    The router takes the first whitespace-separated token."""
    from dispatch import route
    mock_invoke.set("implement -- because the user asked")
    assert route("anything") == "implement"


def test_router_case_insensitive(mock_invoke):
    """LLM might capitalize ('Implement', 'TASK')."""
    from dispatch import route
    mock_invoke.set("Implement")
    assert route("anything") == "implement"

    mock_invoke.set("TASK")
    assert route("anything") == "task"


def test_router_strips_quotes(mock_invoke):
    """LLM might wrap the answer in quotes."""
    from dispatch import route
    mock_invoke.set('"implement"')
    assert route("anything") == "implement"


def test_router_strips_parens_and_brackets(mock_invoke):
    """LLM might emit '(implement)' or '[implement]'."""
    from dispatch import route
    mock_invoke.set("(implement)")
    assert route("anything") == "implement"


# --------------------------------------------------------------------------
# route(): deterministic fast path for exact command words
# --------------------------------------------------------------------------

def test_router_exact_command_word_skips_llm(monkeypatch):
    """A bare command word routes deterministically -- no API call at all."""
    import llm
    import dispatch

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("LLM must not be called for exact command words")

    # Patch both -- see conftest.mock_invoke for why
    monkeypatch.setattr(llm, "invoke", must_not_be_called)
    monkeypatch.setattr(dispatch, "invoke", must_not_be_called)

    for cmd in ("scan", "pr", "review", "reflect", "help", "research"):
        assert dispatch.route(cmd) == cmd


def test_router_fast_path_normalizes_case_and_whitespace(monkeypatch):
    import llm
    import dispatch

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("LLM must not be called for exact command words")

    monkeypatch.setattr(llm, "invoke", must_not_be_called)
    monkeypatch.setattr(dispatch, "invoke", must_not_be_called)

    assert dispatch.route("  SCAN  ") == "scan"


def test_router_multiword_input_still_uses_llm(mock_invoke):
    """'help me think through X' is a chat question, not `t help` -- any
    input that isn't exactly a command word must reach the LLM router."""
    from dispatch import route
    mock_invoke.set("chat")
    assert route("help me think through the auth flow") == "chat"


# --------------------------------------------------------------------------
# route(): never crashes
# --------------------------------------------------------------------------

def test_router_falls_back_on_api_error(monkeypatch):
    """If the LLM call itself raises, route() must not propagate the error.
    Otherwise the REPL dies when the API is down."""
    import llm
    import dispatch

    def raise_error(*args, **kwargs):
        raise RuntimeError("API unavailable")

    # Patch both -- see conftest.mock_invoke for why
    monkeypatch.setattr(llm, "invoke", raise_error)
    monkeypatch.setattr(dispatch, "invoke", raise_error)

    result = dispatch.route("anything")
    assert result == "task", (
        "expected route() to fall back to 'task' on API failure, "
        f"got {result!r}"
    )


def test_router_falls_back_on_empty_response(mock_invoke):
    """If the LLM returns empty string, fall back to 'task'."""
    from dispatch import route
    mock_invoke.set("")
    assert route("anything") == "task"


def test_router_falls_back_on_whitespace_only_response(mock_invoke):
    """Whitespace-only response should fall back, not crash on .split()[0]."""
    from dispatch import route
    mock_invoke.set("   \n\t  ")
    assert route("anything") == "task"
