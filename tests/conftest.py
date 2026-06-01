"""
Shared pytest fixtures for the Tiramisu test suite.

Phase 1 priorities (see CLAUDE.md §8 and Phase plan in commit history):
  - Mock the Anthropic API so we never burn tokens during tests
  - Provide an isolated TIRAMISU_HOME so we never touch the user's real DB
  - Provide an isolated cwd so path-sandbox tests have a known root
"""
import sys
from pathlib import Path

import pytest

# Make scripts/ and hooks/ importable from anywhere in the test suite.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hooks"))


@pytest.fixture
def tmp_tiramisu_home(monkeypatch, tmp_path):
    """
    Isolate ~/.tiramisu for a test. Sets TIRAMISU_HOME to a fresh tmp dir
    so `memory.py` writes to a throwaway SQLite file, not the user's real
    learnings.db.
    """
    home = tmp_path / "tiramisu_home"
    home.mkdir()
    monkeypatch.setenv("TIRAMISU_HOME", str(home))
    yield home


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    """
    Isolate the working directory for a test. Used by path-sandbox tests
    that need a known cwd to assert that escapes (../, absolute paths) are
    rejected.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    yield workspace


@pytest.fixture
def mock_invoke(monkeypatch):
    """
    Replace llm.invoke with a controllable fake. Returns a controller object
    with .set("response text") that subsequent calls will return.

    Important: this also patches every *consumer* module's local `invoke`
    reference, because Python's `from llm import invoke` captures the function
    object at import time. Patching only `llm.invoke` would not redirect
    callers that already did `from llm import invoke` at module load. The
    consumer list below is the audit set of every module that imports
    invoke from llm; if you add a new one, add it here too.

    Use case: testing dispatch.route() without burning real Haiku tokens.

        def test_something(mock_invoke):
            mock_invoke.set("implement")
            from dispatch import route
            assert route("...") == "implement"
    """
    state = {"response": "implement"}

    def fake_invoke(prompt, system=None, model=None,
                    max_tokens=None, temperature=None):
        return state["response"]

    # Patch llm.invoke first
    import llm
    monkeypatch.setattr(llm, "invoke", fake_invoke)

    # Then patch each consumer's local reference. Importing the module first
    # ensures the bound name exists before we replace it.
    for mod_name in ("dispatch", "learn", "research"):
        try:
            mod = __import__(mod_name)
            if hasattr(mod, "invoke"):
                monkeypatch.setattr(mod, "invoke", fake_invoke)
        except ImportError:
            pass

    class Controller:
        def set(self, response: str) -> None:
            state["response"] = response

        @property
        def response(self) -> str:
            return state["response"]

    return Controller()
