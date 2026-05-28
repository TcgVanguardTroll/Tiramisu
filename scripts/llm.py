"""Shared LLM utility for Tiramisu scripts. Uses the Anthropic API directly."""
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
    print()
    return "".join(full)
