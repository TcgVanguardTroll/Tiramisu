"""
Tests for the deterministic secret scanner Cookie runs over the staged diff
(P3, borrowed from ECC's AgentShield idea).

Contract pinned here (safety surface -> tests first, CLAUDE.md §8):
  - Only ADDED lines (`+`, not the `+++` header) are scanned. Removed and
    context lines are ignored: we only care about secrets being introduced.
  - High-signal patterns match; ordinary code does not (false positives turn
    a warning into noise the user learns to ignore).
  - The matched secret is MASKED in every finding — the scanner must never
    echo the full secret back into terminal/logs.
  - The scanner only ever returns findings; it has no authority to block a
    commit (that stays Cookie's call, and P3 is warning-only).
"""
import pytest

from secrets_scan import scan_diff, mask


def _added(*lines: str) -> str:
    """Build a minimal unified-diff body whose payload lines are additions."""
    header = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n"
    return header + "\n".join("+" + ln for ln in lines) + "\n"


# --------------------------------------------------------------------------
# Detection — one test per high-signal rule
# --------------------------------------------------------------------------

def test_detects_aws_access_key():
    findings = scan_diff(_added('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'))
    assert any(f["rule"] == "aws-access-key" for f in findings)


def test_detects_private_key_header():
    findings = scan_diff(_added("-----BEGIN RSA PRIVATE KEY-----"))
    assert any(f["rule"] == "private-key" for f in findings)


def test_detects_github_pat():
    findings = scan_diff(_added("token = ghp_" + "a" * 36))
    assert any(f["rule"] == "github-token" for f in findings)


def test_detects_github_finegrained_pat():
    findings = scan_diff(_added("t = github_pat_" + "A1b2" * 20 + "ab"))
    assert any(f["rule"] == "github-token" for f in findings)


def test_detects_slack_token():
    # Assembled from fragments so no contiguous token literal sits in this
    # file — otherwise GitHub push protection flags our own test fixture
    # (which it did on the first push: the scanner works, on us too).
    token = "xoxb-" + "123456789012" + "-" + "abcdEFGHijklMNOP"
    findings = scan_diff(_added(f"SLACK = {token}"))
    assert any(f["rule"] == "slack-token" for f in findings)


def test_detects_google_api_key():
    findings = scan_diff(_added('k = "AIza' + "B" * 35 + '"'))
    assert any(f["rule"] == "google-api-key" for f in findings)


def test_detects_sk_style_key():
    findings = scan_diff(_added('client = X("sk-' + "a" * 32 + '")'))
    assert any(f["rule"] == "secret-key" for f in findings)


def test_detects_slack_webhook():
    findings = scan_diff(_added(
        "url = https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXX"))
    assert any(f["rule"] == "slack-webhook" for f in findings)


def test_detects_generic_secret_assignment():
    findings = scan_diff(_added('password = "hunter2supersecret"'))
    assert any(f["rule"] == "hardcoded-secret" for f in findings)


# --------------------------------------------------------------------------
# Scoping — only added content, never removed/context/headers
# --------------------------------------------------------------------------

def test_ignores_removed_lines():
    diff = ("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +0,0 @@\n"
            '-AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    assert scan_diff(diff) == []


def test_ignores_context_lines():
    diff = ("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n"
            ' AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    assert scan_diff(diff) == []


def test_ignores_plusplusplus_header():
    # A file literally named like a token shouldn't trip via the +++ header.
    diff = "diff --git a/f b/f\n--- a/f\n+++ b/AKIAIOSFODNN7EXAMPLE.py\n@@ -0,0 +1 @@\n+x = 1\n"
    assert scan_diff(diff) == []


# --------------------------------------------------------------------------
# Precision — ordinary code must not trip the scanner
# --------------------------------------------------------------------------

def test_no_false_positive_on_normal_code():
    diff = _added(
        "def add(a, b):",
        "    return a + b",
        'password_field = form.get("password")',
        "api_key = config.get(\"key\")",
        "API_TIMEOUT = 30",
    )
    assert scan_diff(diff) == []


def test_placeholder_values_are_not_flagged():
    diff = _added(
        'password = "changeme"',
        'token = "${GITHUB_TOKEN}"',
        'api_key = "your-api-key-here"',
        'secret = "example-secret-value"',
    )
    assert scan_diff(diff) == []


# --------------------------------------------------------------------------
# Masking + shape
# --------------------------------------------------------------------------

def test_mask_hides_the_middle():
    masked = mask("AKIAIOSFODNN7EXAMPLE")
    assert masked != "AKIAIOSFODNN7EXAMPLE"
    assert "AKIA" in masked          # keep a recognizable prefix
    assert "EXAMPLE" not in masked   # hide the tail content
    assert "*" in masked


def test_finding_excerpt_does_not_contain_raw_secret():
    secret = "AKIAIOSFODNN7EXAMPLE"
    findings = scan_diff(_added(f'AWS_KEY = "{secret}"'))
    assert findings
    for f in findings:
        assert secret not in f["excerpt"]


def test_empty_diff_returns_empty():
    assert scan_diff("") == []
    assert scan_diff(None) == []


def test_multiple_findings_collected():
    diff = _added(
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"',
        "gh = ghp_" + "b" * 36,
    )
    rules = {f["rule"] for f in scan_diff(diff)}
    assert {"aws-access-key", "github-token"} <= rules
