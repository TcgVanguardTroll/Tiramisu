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
DEFAULT_MODEL  = "claude-sonnet-4-5"
FAST_MODEL     = "claude-haiku-4-5"

# Anthropic pricing per 1M tokens (USD), as of 2026.
# cache_write = 1.25x base input rate, cache_read = 0.10x base input rate.
# Unknown models fall back to DEFAULT_MODEL rates -- cost is approximate, not billed.
_COSTS = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.00,  "cache_write": 1.00,  "cache_read": 0.08},
    "claude-haiku-3-5":  {"input": 0.80, "output": 4.00,  "cache_write": 1.00,  "cache_read": 0.08},
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
        # Anthropic SDK Usage object has these attributes; defaults to 0 if missing.
        in_tok          = getattr(usage, "input_tokens", 0) or 0
        out_tok         = getattr(usage, "output_tokens", 0) or 0
        cache_write_tok = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read_tok  = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = _calc_cost(model, in_tok, out_tok, cache_write_tok, cache_read_tok)
        script = _caller_script()

        # Late import so llm.py stays usable even if memory.py is broken
        from memory import log_token_usage
        log_token_usage(script, model, in_tok, out_tok,
                        cache_write_tok, cache_read_tok, cost)
    except Exception:
        # Logging is best-effort; never break the actual LLM call
        pass


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
        os.environ[k.strip()] = v.strip()


def _client():
    _load_env()
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to ~/.tiramisu/.env"
        )
    return anthropic.Anthropic(api_key=key)


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
    return resp.content[0].text


def invoke_stream(prompt, system=None, model=DEFAULT_MODEL, max_tokens=2048):
    """Stream Claude's response, printing each chunk as it arrives. Returns full text."""
    client = _client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
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


def _print_rendered_view(text: str) -> None:
    """After streaming, print a single clean Markdown render below a divider.
    No live re-render -- one-shot console.print(Markdown(...)) avoids the
    overflow / append problem that broke rich.Live + Markdown on long output.
    Set TIRAMISU_NO_RENDER=1 to suppress (e.g., in CI or piped output)."""
    if os.environ.get("TIRAMISU_NO_RENDER"):
        return
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


def invoke_stream_markdown(prompt, system=None, model=DEFAULT_MODEL, max_tokens=2048):
    """
    Two-phase response:
      Phase 1 -- plain streaming so the user sees text arriving live.
      Phase 2 -- after the stream finishes, print ONE rendered Markdown view
                 below a divider (only if the content has real markdown
                 structure and is long enough to benefit).

    This sidesteps the rich.Live + Markdown overflow bug (the cursor can't
    redraw past terminal height, so each chunk appears to duplicate). Doing
    the render exactly once, after streaming is done, has no such problem.

    Returns the raw text for callers that need to parse the response.
    """
    client = _client()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

    # Phase 1: plain streaming
    full = []
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full.append(text)
        final = stream.get_final_message()
    print()

    text = "".join(full)

    # Phase 2: one-shot rendered view, only if it helps
    _print_rendered_view(text)

    _log_api_usage(final.usage, model)
    return text
