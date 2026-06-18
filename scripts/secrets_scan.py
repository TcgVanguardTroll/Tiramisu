#!/usr/bin/env python3
"""
Deterministic secret scanner for Cookie's pre-commit review (P3).

Borrowed from ECC's AgentShield: before the LLM review, scan the *added*
lines of the staged diff for high-signal credential patterns and surface
them as a warning. It is intentionally:

  - **Deterministic** — regex, no LLM. The warning fires even if the model
    misses it, and costs nothing.
  - **Added-lines only** — we flag secrets being *introduced*, not ones
    already in history or being removed.
  - **High precision** — a noisy scanner is one users learn to ignore, which
    is worse than none (mirrors the false-positive caution in
    docs/INVARIANTS.md §1–2). Placeholder values are skipped.
  - **Warning, not block** — this module only returns findings. Cookie keeps
    sole authority over the commit (CLAUDE.md §4.4: one agent, one job).

Every finding masks the matched secret; the raw value is never echoed back.
"""
import re

# (rule-name, compiled-pattern). Patterns are anchored on shapes that almost
# never occur by accident. Order doesn't matter; all are tried per line.
_PATTERNS = [
    ("aws-access-key",  re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key",     re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github-token",    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    ("github-token",    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("slack-token",     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("slack-webhook",   re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")),
    ("google-api-key",  re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("secret-key",      re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
]

# Generic "secret-looking variable = quoted literal" assignment. Lower
# precision than the shape-based rules above, so guarded by a placeholder
# filter (see _looks_like_placeholder).
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)\b"
    r"\s*[:=]\s*['\"](?P<val>[^'\"\s]{8,})['\"]"
)

# Values that are obviously not real secrets — env interpolation, templates,
# and the usual doc/example fillers. Substring match, case-insensitive.
_PLACEHOLDER_SUBSTRINGS = (
    "$", "{{", "}}", "<", ">", "example", "placeholder", "changeme",
    "your-", "your_", "xxxx", "dummy", "redacted", "sample", "todo",
    "hunter2example",
)


def mask(secret: str) -> str:
    """Mask a secret for display: keep a short recognizable prefix, hide the
    rest. Never returns the full input."""
    if not secret:
        return ""
    if len(secret) <= 6:
        return secret[0] + "*" * (len(secret) - 1)
    keep = min(4, len(secret) - 3)
    return secret[:keep] + "*" * (len(secret) - keep)


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    return any(tok in low for tok in _PLACEHOLDER_SUBSTRINGS)


def _added_lines(diff: str):
    """Yield the content of added lines (`+`), skipping the `+++` file header."""
    for raw in diff.splitlines():
        if raw.startswith("+") and not raw.startswith("+++"):
            yield raw[1:]


def _mask_in_line(line: str, secret: str) -> str:
    excerpt = line.strip()
    if len(excerpt) > 200:
        excerpt = excerpt[:200] + " …"
    return excerpt.replace(secret, mask(secret))


def scan_diff(diff: str | None) -> list[dict]:
    """Return a list of {rule, excerpt} for secrets found in added diff lines.
    The excerpt has the matched secret masked. Empty list when nothing (or
    no diff) is found."""
    if not diff:
        return []

    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def record(rule: str, line: str, secret: str) -> None:
        excerpt = _mask_in_line(line, secret)
        key = (rule, excerpt)
        if key not in seen:
            seen.add(key)
            findings.append({"rule": rule, "excerpt": excerpt})

    for line in _added_lines(diff):
        for rule, pattern in _PATTERNS:
            for m in pattern.finditer(line):
                record(rule, line, m.group(0))
        for m in _ASSIGNMENT.finditer(line):
            value = m.group("val")
            if not _looks_like_placeholder(value):
                record("hardcoded-secret", line, value)

    return findings


def format_findings(findings: list[dict]) -> str:
    """Render findings as a terminal warning block. Empty string if none."""
    if not findings:
        return ""
    lines = [f"\n⚠  Cookie spotted {len(findings)} possible secret(s) in the "
             f"staged diff:"]
    for f in findings:
        lines.append(f"   - [{f['rule']}] {f['excerpt']}")
    lines.append("   These do NOT block the commit, but a committed secret is "
                 "hard to purge from git history — rotate/remove if real.\n")
    return "\n".join(lines)
