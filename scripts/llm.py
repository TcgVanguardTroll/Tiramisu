"""Shared LLM utility for Tiramisu scripts. Uses the Anthropic API directly."""
import inspect
import os
import sys
from pathlib import Path

_ENV_FILE = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu")) / ".env"

# DEFAULT_MODEL: quality-tier model for tasks where output quality matters more
# than latency or cost -- PR review, implementation, reflection, scope planning.
# FAST_MODEL: cheaper, faster model for hot-path tasks called on every commit --
# commit-message drafts, pre-commit reviews, single-shot preference classification.
DEFAULT_MODEL  = "claude-sonnet-4-6"
FAST_MODEL     = "claude-haiku-4-5"

# Anthropic pricing per 1M tokens (USD), as of 2026.
# cache_write = 1.25x base input rate, cache_read = 0.10x base input rate.
# Unknown models fall back to DEFAULT_MODEL rates -- cost is approximate, not billed.
_COSTS = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
}


def _caller_script():
    """Identify which Tiramisu script called the LLM (for the usage log)."""
    for frame in inspect.stack():
        fname = Path(frame.filename).resolve()
        # Skip frames inside this file -- find the actual caller
        if fname.name == "llm.py":
            continue
        # Anything inside our scripts/ or hooks/ counts
        return fname.stem
    return "unknown"


def _calc_cost(model, in_tok, out_tok, cache_write_tok=0, cache_read_tok=0):
    """Best-effort cost estimate. Falls back to Sonnet rates if model is unknown."""
    rates = _COSTS.get(model) or _COSTS[DEFAULT_MODEL]
    return (
        in_tok          * rates["input"]
        + out_tok       * rates["output"]
        + cache_write_tok * rates["cache_write"]
        + cache_read_tok  * rates["cache_read"]
    ) / 1_000_000


def _log_api_usage(usage, model):
    """Fire-and-forget usage capture. Never raises."""
    try:
        in_tok          = getattr(usage, "input_tokens", 0) or 0
        out_tok         = getattr(usage, "output_tokens", 0) or 0
        cache_write_tok = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read_tok  = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = _calc_cost(model, in_tok, out_tok, cache_write_tok, cache_read_tok)
        script = _caller_script()

        from memory import log_token_usage
        log_token_usage(script, model, in_tok, out_tok,
                        cache_write_tok, cache_read_tok, cost,
                        repo_path=os.getcwd())
    except Exception as e:
        print(f"[tiramisu] usage log warning: {type(e).__name__}: {e}", file=sys.stderr)


_ALLOWED_ENV_KEYS = {"ANTHROPIC_API_KEY", "TIRAMISU_HOME", "TIRAMISU_RENDER",
                     "TIRAMISU_SPINNER", "TIRAMISU_NO_RENDER"}


def _load_env():
    """Read TIRAMISU_HOME/.env into os.environ. Fail-soft: never raise."""
    if not _ENV_FILE.exists():
        return
    try:
        text = _ENV_FILE.read_text(encoding="utf-8-sig")
    except OSError as e:
        print(f"[tiramisu] warning: could not read {_ENV_FILE}: {e}", file=sys.stderr)
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k in _ALLOWED_ENV_KEYS or k.startswith("TIRAMISU_"):
            os.environ[k] = v.strip()


API_TIMEOUT_SEC = 120


def _client():
    _load_env()
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to ~/.tiramisu/.env"
        )
    return anthropic.Anthropic(api_key=key, timeout=API_TIMEOUT_SEC)


def invoke(
    prompt,
    system=None,
    model=DEFAULT_MODEL,
    max_tokens=1024,
    temperature=0.3,
) -> str:
    """Call Claude and return the text response."""
    client = _client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        # Mark the system prompt as cacheable. First call writes the cache (small
        # extra cost); subsequent calls within the 5-minute TTL hit it for free.
        # The hooks fire frequently enough that this is a net win even with the
        # write overhead on cold calls.
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    resp = client.messages.create(**kwargs)
    _log_api_usage(resp.usage, model)
    # Iterate blocks rather than assuming content[0] is text -- responses can
    # lead with non-text blocks (e.g. thinking) depending on model settings.
    return "".join(b.text for b in resp.content if b.type == "text")


def invoke_stream(prompt, system=None, model=DEFAULT_MODEL, max_tokens=2048,
                  thinking=False):
    """Stream Claude's response, printing each chunk as it arrives. Returns full text."""
    client = _client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if thinking:
        # Adaptive: the model decides when and how much to think. Thinking
        # tokens count toward max_tokens, so callers enabling this should
        # pass a generous cap.
        kwargs["thinking"] = {"type": "adaptive"}
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    full = []
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full.append(text)
        final = stream.get_final_message()
    print()
    _log_api_usage(final.usage, model)
    return "".join(full)


_MD_INDICATORS = ("```", "## ", "### ", "**", "\n- ", "\n* ", "\n1. ", "\n2. ")


def _worth_rendering(text: str) -> bool:
    """Print a rendered markdown view only when it genuinely adds something:
    response is non-trivial AND has at least one markdown feature."""
    if len(text) < 250:
        return False
    return any(ind in text for ind in _MD_INDICATORS)


def _render_mode() -> str:
    """
    Read TIRAMISU_RENDER to decide how to present streaming responses.

      both     (default) -- stream raw, then print rendered view below a divider
      stream             -- stream raw only, no rendered view
      rendered           -- silent buffer with a spinner, then print rendered only

    TIRAMISU_NO_RENDER=1 is kept as a deprecated alias for `stream`.
    """
    mode = (os.environ.get("TIRAMISU_RENDER") or "").lower().strip()
    if mode in {"both", "stream", "rendered"}:
        return mode
    if os.environ.get("TIRAMISU_NO_RENDER"):
        return "stream"
    return "both"


def _print_rendered_view(text: str) -> None:
    """One-shot Markdown render below a divider. Called after streaming."""
    if not _worth_rendering(text):
        return
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        console = Console()
        console.rule("[dim]rendered[/dim]")
        console.print(Markdown(text))
        console.rule()
    except Exception:
        # Never let pretty-printing break a successful API call
        pass


def _silent_then_render(client, kwargs, model) -> str:
    """
    rendered-only mode: silent buffer with a spinner while the response streams,
    then print a single Markdown render. The spinner is critical -- without it
    the terminal looks frozen for the whole API call.
    """
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        import spinners as _spinners
    except Exception:
        # Fallback: lose the rendering, but never crash
        return _plain_stream(client, kwargs, model)

    console = Console()
    buffer = []

    with console.status("[dim]thinking...[/dim]", spinner=_spinners.chosen()):
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                buffer.append(text)
            final = stream.get_final_message()

    text = "".join(buffer)
    if text.strip():
        console.print(Markdown(text))

    _log_api_usage(final.usage, model)
    return text


def _plain_stream(client, kwargs, model) -> str:
    """Plain streaming, no rendering. Used by stream and both modes (phase 1)."""
    full = []
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full.append(text)
        final = stream.get_final_message()
    print()
    _log_api_usage(final.usage, model)
    return "".join(full)


def invoke_stream_markdown(prompt, system=None, model=DEFAULT_MODEL, max_tokens=2048,
                           thinking=False):
    """
    Streaming response with optional Markdown rendering.

    Behavior is controlled by the TIRAMISU_RENDER env var:
      both     (default) -- stream raw, then print rendered view below a divider
      stream             -- raw only, no rendered view
      rendered           -- silent buffer with spinner, then rendered view only

    The render-only mode avoids any duplication entirely (single render call,
    after streaming completes). The both mode preserves real-time feedback
    while still giving you a polished version to scroll back to.

    Set thinking=True for analysis-heavy calls (review, scoping, reflection):
    the model decides per-request when and how much to think. Thinking tokens
    count toward max_tokens, so pass a generous cap alongside it.

    Returns the raw text for callers that need to parse the response.
    """
    client = _client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

    mode = _render_mode()

    if mode == "rendered":
        # We track token usage inside _silent_then_render so we don't double-log
        return _silent_then_render(client, kwargs, model)

    # mode is "both" or "stream" -- plain stream first
    text = _plain_stream(client, kwargs, model)

    if mode == "both":
        _print_rendered_view(text)

    return text
