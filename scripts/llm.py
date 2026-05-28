"""Shared LLM utility for Tiramisu scripts. Uses the Anthropic API directly."""
import os
from pathlib import Path

_ENV_FILE = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu")) / ".env"

# Update this to the latest model you want to use by default.
DEFAULT_MODEL  = "claude-sonnet-4-5"
FAST_MODEL     = "claude-haiku-3-5"


def _load_env():
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
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
        # Use prompt caching for system prompts — saves latency + cost on repeat calls
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
