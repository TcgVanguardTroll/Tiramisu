"""Shared LLM utility for Tiramisu scripts. Uses Bedrock directly via bw-bedrock profile."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import boto3

_client = None
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
DB_PATH = str(Path(__file__).resolve().parent.parent / "tiramisu.db")

# Cost per 1M tokens (USD) — Claude Sonnet 4 with 64% discount
_COSTS = {
    "us.anthropic.claude-sonnet-4-6": {"input": 1.08, "output": 5.40},
    "us.anthropic.claude-haiku-3-5": {"input": 0.29, "output": 1.25},
}


def _get_client():
    global _client
    if _client is None:
        os.environ.setdefault("AWS_PROFILE", "bw-bedrock")
        _client = boto3.client("bedrock-runtime", region_name="us-west-2")
    return _client


def _caller_script():
    """Identify which script called invoke()."""
    for frame_info in reversed(sys._current_frames().values().__class__.__mro__):
        pass  # fallback below
    # Walk the stack to find the top-level script
    import inspect
    for frame in inspect.stack():
        fname = frame.filename
        if "tiramisu/scripts/" in fname or "tiramisu/hooks/" in fname:
            return Path(fname).stem
    return "unknown"


def _log_usage(script, model, input_tokens, output_tokens):
    """Log token usage to SQLite. Fire-and-forget."""
    try:
        costs = _COSTS.get(model, {"input": 1.0, "output": 5.0})
        cost_usd = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute(
            "INSERT INTO token_usage (script, model, input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?)",
            (script, model, input_tokens, output_tokens, cost_usd),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def invoke(prompt, system=None, model=DEFAULT_MODEL, max_tokens=1024, temperature=0.3):
    """Invoke Claude via Bedrock. Returns the text response. Logs token usage."""
    messages = [{"role": "user", "content": prompt}]
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        body["system"] = system
    resp = _get_client().invoke_model(modelId=model, body=json.dumps(body))
    result = json.loads(resp["body"].read())

    # Extract token usage and log
    usage = result.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    script = _caller_script()
    _log_usage(script, model, input_tokens, output_tokens)

    return result["content"][0]["text"]
