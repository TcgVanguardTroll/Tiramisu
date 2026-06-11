"""
Direct unit tests for llm.py -- the single chokepoint every agent's API
traffic flows through.

History lesson: invoke() accepted a `temperature` parameter for months
without forwarding it to the API, and nothing caught it because nothing
tested the request kwargs directly. These tests pin the request shape:

  - temperature is forwarded
  - the system prompt is wrapped with a cache_control breakpoint
  - text extraction iterates blocks (doesn't assume content[0] is text)
  - the thinking flag adds adaptive thinking to streaming calls
  - cost math matches the rate table; unknown models fall back

No real API calls: _client / _plain_stream are monkeypatched.
"""
from types import SimpleNamespace

import pytest


def _fake_usage(in_tok=100, out_tok=50, cache_write=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _thinking_block():
    return SimpleNamespace(type="thinking", thinking="...")


class FakeMessages:
    def __init__(self, content):
        self._content = content
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(content=self._content, usage=_fake_usage())


class FakeClient:
    def __init__(self, content):
        self.messages = FakeMessages(content)


@pytest.fixture
def fake_client(monkeypatch):
    """Patch llm._client to return a kwargs-capturing fake; silence the
    usage logger so tests never touch learnings.db."""
    import llm
    client = FakeClient([_text_block("hello")])
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm, "_log_api_usage", lambda usage, model: None)
    return client


# --------------------------------------------------------------------------
# invoke(): request shape
# --------------------------------------------------------------------------

def test_invoke_forwards_temperature(fake_client):
    """The router and learn classifier pass 0.0 for determinism. This was
    silently dropped once -- never again."""
    from llm import invoke
    invoke("prompt", temperature=0.0)
    assert fake_client.messages.last_kwargs["temperature"] == 0.0


def test_invoke_wraps_system_with_cache_control(fake_client):
    from llm import invoke
    invoke("prompt", system="you are a test")
    system = fake_client.messages.last_kwargs["system"]
    assert system[0]["text"] == "you are a test"
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_invoke_omits_system_when_none(fake_client):
    from llm import invoke
    invoke("prompt")
    assert "system" not in fake_client.messages.last_kwargs


def test_invoke_passes_model_and_max_tokens(fake_client):
    from llm import invoke
    invoke("prompt", model="claude-haiku-4-5", max_tokens=10)
    kwargs = fake_client.messages.last_kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 10


# --------------------------------------------------------------------------
# invoke(): response parsing
# --------------------------------------------------------------------------

def test_invoke_extracts_text_across_blocks(monkeypatch):
    """Responses can lead with non-text blocks (thinking). content[0].text
    would crash or return garbage -- extraction must iterate by type."""
    import llm
    client = FakeClient([_thinking_block(), _text_block("the "), _text_block("answer")])
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm, "_log_api_usage", lambda usage, model: None)
    assert llm.invoke("prompt") == "the answer"


# --------------------------------------------------------------------------
# streaming helpers: thinking flag
# --------------------------------------------------------------------------

def _captured_stream_kwargs(monkeypatch, **call_kwargs):
    import llm
    captured = {}

    def fake_plain_stream(client, kwargs, model):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(llm, "_client", lambda: object())
    monkeypatch.setattr(llm, "_plain_stream", fake_plain_stream)
    monkeypatch.setenv("TIRAMISU_RENDER", "stream")
    llm.invoke_stream_markdown("prompt", **call_kwargs)
    return captured


def test_stream_markdown_thinking_flag_adds_adaptive(monkeypatch):
    kwargs = _captured_stream_kwargs(monkeypatch, thinking=True)
    assert kwargs["thinking"] == {"type": "adaptive"}


def test_stream_markdown_default_has_no_thinking(monkeypatch):
    kwargs = _captured_stream_kwargs(monkeypatch)
    assert "thinking" not in kwargs


# --------------------------------------------------------------------------
# cost math
# --------------------------------------------------------------------------

def test_calc_cost_matches_rate_table():
    from llm import _calc_cost
    # 1M input + 1M output on Haiku 4.5 = $1.00 + $5.00
    assert _calc_cost("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.00)


def test_calc_cost_includes_cache_rates():
    from llm import _calc_cost
    cost = _calc_cost("claude-sonnet-4-6", 0, 0,
                      cache_write_tok=1_000_000, cache_read_tok=1_000_000)
    # cache_write 1.25x of $3 + cache_read 0.10x of $3
    assert cost == pytest.approx(3.75 + 0.30)


def test_calc_cost_unknown_model_falls_back_to_default():
    from llm import _calc_cost, DEFAULT_MODEL
    unknown = _calc_cost("claude-mystery-9", 1_000_000, 0)
    default = _calc_cost(DEFAULT_MODEL, 1_000_000, 0)
    assert unknown == pytest.approx(default)


def test_cost_of_reads_usage_object():
    from llm import cost_of, _calc_cost
    usage = _fake_usage(in_tok=1000, out_tok=2000, cache_write=300, cache_read=400)
    assert cost_of(usage, "claude-sonnet-4-6") == pytest.approx(
        _calc_cost("claude-sonnet-4-6", 1000, 2000, 300, 400)
    )


def test_cost_of_tolerates_missing_fields():
    """Usage objects from mocks or older SDKs may lack cache fields."""
    from llm import cost_of
    bare = SimpleNamespace(input_tokens=10, output_tokens=5)
    assert cost_of(bare, "claude-haiku-4-5") > 0
