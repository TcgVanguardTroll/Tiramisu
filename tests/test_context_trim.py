"""
Context trimming in Eclair's agentic loop (implement.trim_tool_history).

A 50-iteration `t implement` run accumulates tool results (file reads up
to 30K chars each). trim_tool_history elides the oldest results once the
total exceeds a budget. The invariants pinned here:

  - under budget: nothing is touched
  - over budget: oldest rounds elided, the most recent rounds never are
  - structure survives: tool_use_id pairing and message shape unchanged
  - the initial prompt (string content) is never touched
"""
from implement import (
    trim_tool_history,
    ELIDED_NOTE,
    KEEP_RECENT_TOOL_ROUNDS,
    MAX_TOOL_RESULT_CHARS,
)


def _tool_round(round_idx: int, result_chars: int):
    """One assistant tool_use turn + the paired user tool_result turn."""
    tu_id = f"toolu_{round_idx}"
    assistant = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tu_id,
                     "name": "read_file", "input": {"path": f"f{round_idx}.py"}}],
    }
    user = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tu_id,
                     "content": "x" * result_chars}],
    }
    return [assistant, user]


def _history(n_rounds: int, result_chars: int):
    messages = [{"role": "user", "content": "Task: do the thing"}]
    for i in range(n_rounds):
        messages.extend(_tool_round(i, result_chars))
    return messages


def test_under_budget_is_untouched():
    messages = _history(n_rounds=3, result_chars=1000)
    assert trim_tool_history(messages) == 0
    assert all(ELIDED_NOTE not in str(m["content"]) for m in messages)


def test_over_budget_elides_oldest_rounds():
    # 10 rounds x 30K chars = 300K, well over the 120K budget
    messages = _history(n_rounds=10, result_chars=30_000)
    elided = trim_tool_history(messages)
    assert elided == 10 - KEEP_RECENT_TOOL_ROUNDS

    tool_rounds = [m for m in messages
                   if m["role"] == "user" and isinstance(m["content"], list)]
    for m in tool_rounds[:-KEEP_RECENT_TOOL_ROUNDS]:
        assert m["content"][0]["content"] == ELIDED_NOTE


def test_recent_rounds_are_never_elided():
    messages = _history(n_rounds=10, result_chars=30_000)
    trim_tool_history(messages)

    tool_rounds = [m for m in messages
                   if m["role"] == "user" and isinstance(m["content"], list)]
    for m in tool_rounds[-KEEP_RECENT_TOOL_ROUNDS:]:
        assert m["content"][0]["content"] == "x" * 30_000


def test_tool_use_ids_survive_trimming():
    """The API rejects a tool_result whose tool_use_id has no matching
    tool_use block -- trimming must only touch content, never structure."""
    messages = _history(n_rounds=10, result_chars=30_000)
    trim_tool_history(messages)

    for m in messages[1:]:
        if m["role"] == "user":
            block = m["content"][0]
            assert block["type"] == "tool_result"
            assert block["tool_use_id"].startswith("toolu_")


def test_initial_prompt_is_never_touched():
    messages = _history(n_rounds=10, result_chars=30_000)
    trim_tool_history(messages)
    assert messages[0]["content"] == "Task: do the thing"


def test_few_rounds_over_budget_does_not_crash():
    """If everything is recent (fewer rounds than the keep window), there
    is nothing safe to elide -- the call must be a clean no-op."""
    big = MAX_TOOL_RESULT_CHARS + 1
    messages = _history(n_rounds=2, result_chars=big)
    assert trim_tool_history(messages) == 0
