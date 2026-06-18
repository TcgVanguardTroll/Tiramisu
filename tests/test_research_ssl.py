"""
SSL context selection for Cannoli's HTTP fetches.

research_common._ssl_context() is the single point that decides how
Cannoli verifies TLS for every research fetch (watched sources, GitHub /
HN / arxiv discovery). It has to satisfy three different environments:

  1. Normal machines  -> verify against modern roots (certifi), because
     Windows' built-in store is often too stale to validate Let's Encrypt
     and other modern chains.
  2. Corporate / sandbox proxies that intercept TLS with their own root CA
     -> trust an explicit CA bundle the user points us at, the CORRECT way
     to make verification succeed behind such a proxy.
  3. Last-resort escape hatch -> TIRAMISU_INSECURE_SSL lets a user who
     understands the risk disable verification so research degrades to
     "works but unverified" instead of "every fetch errors".

The safety contract pinned here: verification is ON by default, and the
ONLY way to turn it off is the explicit, loudly-warned env var. A missing
or malformed CA-bundle path must never silently drop verification -- it
falls back to a *verifying* context.
"""
import ssl

import pytest

import research_common
from research_common import _ssl_context


# --------------------------------------------------------------------------
# Default: verification is ON
# --------------------------------------------------------------------------

def test_default_context_verifies(monkeypatch):
    monkeypatch.delenv("TIRAMISU_INSECURE_SSL", raising=False)
    monkeypatch.delenv("TIRAMISU_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# --------------------------------------------------------------------------
# Escape hatch: TIRAMISU_INSECURE_SSL disables verification (and ONLY that)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "On"])
def test_insecure_env_disables_verification(monkeypatch, val):
    monkeypatch.setenv("TIRAMISU_INSECURE_SSL", val)
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_insecure_env_falsey_keeps_verification(monkeypatch, val):
    monkeypatch.setenv("TIRAMISU_INSECURE_SSL", val)
    monkeypatch.delenv("TIRAMISU_CA_BUNDLE", raising=False)
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_insecure_warns_once(monkeypatch, capsys):
    monkeypatch.setenv("TIRAMISU_INSECURE_SSL", "1")
    monkeypatch.setattr(research_common, "_warned_insecure", False)
    _ssl_context()
    _ssl_context()
    err = capsys.readouterr().err
    # Warned, but only once across repeated calls in a process.
    assert err.count("TIRAMISU_INSECURE_SSL") == 1
    assert "verification" in err.lower() or "verify" in err.lower()


# --------------------------------------------------------------------------
# Custom CA bundle: trust the proxy's root the right way
# --------------------------------------------------------------------------

def test_ca_bundle_env_is_used(monkeypatch, tmp_path):
    # A real, parseable CA file: reuse certifi's bundle so load_verify_locations
    # succeeds. The point is that pointing at it doesn't raise and keeps
    # verification on.
    import certifi
    bundle = tmp_path / "corp-root.pem"
    bundle.write_text(open(certifi.where()).read(), encoding="utf-8")

    monkeypatch.delenv("TIRAMISU_INSECURE_SSL", raising=False)
    monkeypatch.setenv("TIRAMISU_CA_BUNDLE", str(bundle))
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_missing_ca_bundle_falls_back_to_verifying_context(monkeypatch, tmp_path):
    # A path that doesn't exist must NOT silently disable verification.
    monkeypatch.delenv("TIRAMISU_INSECURE_SSL", raising=False)
    monkeypatch.setenv("TIRAMISU_CA_BUNDLE", str(tmp_path / "nope.pem"))
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_insecure_takes_precedence_over_ca_bundle(monkeypatch, tmp_path):
    # If both are set, the explicit insecure flag wins (it's the louder,
    # more deliberate signal).
    import certifi
    bundle = tmp_path / "corp-root.pem"
    bundle.write_text(open(certifi.where()).read(), encoding="utf-8")
    monkeypatch.setenv("TIRAMISU_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("TIRAMISU_INSECURE_SSL", "1")
    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
