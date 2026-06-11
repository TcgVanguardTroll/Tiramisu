"""
Run-budget guardrail in Eclair's agentic loop (implement.run_agent).

Eclair is the only agent that runs autonomously for up to 50 iterations;
the budget is the cap that turns a runaway run into a clean stop. Pinned
here:

  - a run whose estimated cost crosses the budget stops early
  - a cheap run is never interrupted by the budget
  - budget 0 disables the gate entirely
"""
from types import SimpleNamespace

import pytest

import implement
from implement import AgentState, run_agent


def _usage(out_tok):
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=out_tok,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _tool_use_message(out_tok):
    """A message that keeps the loop going: one benign glob tool call."""
    block = SimpleNamespace(type="tool_use", id="toolu_1",
                            name="glob", input={"pattern": "*.nothing"})
    return SimpleNamespace(content=[block], usage=_usage(out_tok))


class FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(())

    def get_final_message(self):
        return self._message


class FakeClient:
    """Always returns the same tool-calling message; counts iterations."""

    def __init__(self, out_tok):
        self.calls = 0
        self._out_tok = out_tok
        self.messages = self

    def stream(self, **kwargs):
        self.calls += 1
        return FakeStream(_tool_use_message(self._out_tok))


@pytest.fixture
def quiet_logging(monkeypatch):
    monkeypatch.setattr(implement, "_log_api_usage", lambda usage, model: None)


def _run(monkeypatch, out_tok, budget_usd, max_iterations=5):
    client = FakeClient(out_tok)
    monkeypatch.setattr(implement, "_client", lambda: client)
    state = AgentState(auto_writes=True, auto_shell=True)
    run_agent(
        initial_messages=[{"role": "user", "content": "Task: test"}],
        system="system prompt",
        state=state,
        max_iterations=max_iterations,
        budget_usd=budget_usd,
    )
    return client


def test_run_stops_when_budget_exceeded(monkeypatch, quiet_logging, tmp_workspace):
    # 1M output tokens/iteration on Sonnet = $15/iteration vs a $1 budget:
    # the gate must fire after the first iteration.
    client = _run(monkeypatch, out_tok=1_000_000, budget_usd=1.00)
    assert client.calls == 1


def test_cheap_run_is_not_interrupted(monkeypatch, quiet_logging, tmp_workspace):
    # ~$0.0008/iteration vs a $5 budget: all iterations run.
    client = _run(monkeypatch, out_tok=50, budget_usd=5.00, max_iterations=3)
    assert client.calls == 3


def test_budget_zero_disables_the_gate(monkeypatch, quiet_logging, tmp_workspace):
    client = _run(monkeypatch, out_tok=1_000_000, budget_usd=0, max_iterations=3)
    assert client.calls == 3
