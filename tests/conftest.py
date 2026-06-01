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

    Sets BOTH the env var AND patches already-imported modules' module-level
    constants. The env var alone is not enough: `memory.py` does
        TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", ...))
        DB_PATH = TIRAMISU_HOME / "learnings.db"
    at import time, so if memory was already imported by a previous test (or
    by another test file via the import cache), the env var change has no
    effect on its DB_PATH. Same story for research.py.
    """
    home = tmp_path / "tiramisu_home"
    home.mkdir()
    monkeypatch.setenv("TIRAMISU_HOME", str(home))

    # Patch already-imported modules' captured paths.
    for mod_name, attrs in (
        ("memory",   ("TIRAMISU_HOME", "DB_PATH")),
        ("research", ("TIRAMISU_HOME",)),
    ):
        try:
            mod = __import__(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "TIRAMISU_HOME"):
            monkeypatch.setattr(mod, "TIRAMISU_HOME", home)
        if "DB_PATH" in attrs and hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", home / "learnings.db")

    yield home


@pytest.fixture
def clear_steering_cache():
    """
    Steering's `_read` is `@lru_cache`d so tests that mutate files between
    calls need to invalidate the cache, otherwise the second call returns the
    cached pre-mutation content.
    """
    try:
        import steering
        steering._read.cache_clear()
    except ImportError:
        pass
    yield
    try:
        import steering
        steering._read.cache_clear()
    except ImportError:
        pass


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
