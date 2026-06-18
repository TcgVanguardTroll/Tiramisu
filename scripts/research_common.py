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


_TRUTHY = {"1", "true", "yes", "on"}

_warned_insecure = False


def _warn_insecure_once() -> None:
    """Print the insecure-TLS warning at most once per process."""
    global _warned_insecure
    if _warned_insecure:
        return
    print("[cannoli] TIRAMISU_INSECURE_SSL is set -- TLS verification is "
          "DISABLED for research fetches. Use only behind a trusted proxy "
          "you control; never on an untrusted network.", file=sys.stderr)
    _warned_insecure = True


def _ssl_context():
    """SSL context for Cannoli's HTTP fetches, chosen by environment.

    Precedence (first match wins):
      1. TIRAMISU_INSECURE_SSL truthy -> unverified context. The escape
         hatch for TLS-intercepting proxies / sandboxes whose root CA we
         can't otherwise trust. Warns once; never silently insecure.
      2. A CA bundle from TIRAMISU_CA_BUNDLE / SSL_CERT_FILE /
         REQUESTS_CA_BUNDLE -> verify against it. This is the CORRECT way
         to work behind a corporate proxy: trust its root explicitly. A
         missing or unparseable bundle falls through (never drops verify).
      3. certifi's bundle -> modern roots, fixes Windows' stale store.
      4. The system default trust store.
    """
    import ssl

    if os.environ.get("TIRAMISU_INSECURE_SSL", "").strip().lower() in _TRUTHY:
        _warn_insecure_once()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    for var in ("TIRAMISU_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        bundle = os.environ.get(var)
        if bundle and Path(bundle).is_file():
            try:
                return ssl.create_default_context(cafile=bundle)
            except (ssl.SSLError, OSError):
                # Unparseable bundle: fall through to a verifying default
                # rather than failing or, worse, dropping verification.
                break

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
