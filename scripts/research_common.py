"""
Shared config + HTTP plumbing for Cannoli's research subsystem.

research.py (watched sources + CLI), research_discovery.py (GitHub / HN /
arxiv scouting), and research_library.py (local-library ingestion) all
import from here. This module must stay leaf-level: it imports nothing
from the other research modules, so there are no cycles.
"""
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
RESEARCH_DIR  = TIRAMISU_HOME / ".research"
CACHE_DIR     = RESEARCH_DIR / "cache"      # last-fetched copies
USER_LIBRARY  = TIRAMISU_HOME / "library"
READ_MARKER   = ".read"                     # suffix appended once user has seen findings
STALE_DAYS    = 7
HTTP_TIMEOUT  = 20                          # seconds per source
MAX_SRC_CHARS = 30000                       # truncate huge pages


def _ssl_context():
    """SSL context that works on Windows where Python's default cert store
    often lacks modern CA roots. Uses certifi (already shipped via the
    anthropic dep) when available; falls back to the system default."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str) -> str:
    """Download a URL with a user agent, return text (truncated to MAX_SRC_CHARS)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Tiramisu-Cannoli/1.0 (+research)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                    context=_ssl_context()) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return f"[error fetching {url}: {e}]"
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_SRC_CHARS:
        text = text[:MAX_SRC_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text
