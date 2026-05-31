#!/usr/bin/env python3
"""
Cookie PR review -- reviews everything between your branch and main.
Reads the full diff, commit log, and changed file contents.

Usage:
    t pr                       # local terminal review
    t pr main                  # compare vs specific base branch
    t pr --post                # ALSO post inline comments to the GitHub PR
    t pr --post --dry-run      # generate and show, but don't actually post
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from llm import invoke_stream_markdown, DEFAULT_MODEL
from steering import load_steering, detect_languages
from personas import pair as persona_pair

MAX_DIFF_CHARS     = 12000
MAX_PER_FILE_CHARS = 4000
MAX_FILES          = 8

SEVERITY_TAGS = {
    "BLOCKER": "🚫 **BLOCKER**",
    "MAJOR":   "⚠️ **Major**",
    "MINOR":   "💭 *Minor*",
    "NIT":     "💭 *nit*",
}


# ----- branch + diff helpers (unchanged) -----

def find_base_branch(specified):
    if specified:
        return specified
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            capture_output=True
        ).returncode == 0:
            return candidate
    return "main"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def get_branch_diff(base):
    diff    = run(["git", "diff", f"{base}...HEAD"])
    log     = run(["git", "log", f"{base}...HEAD", "--oneline"])
    stat    = run(["git", "diff", f"{base}...HEAD", "--stat"])
    files   = [
        f.strip()
        for f in run(["git", "diff", f"{base}...HEAD", "--name-only"]).splitlines()
        if f.strip()
    ]
    return diff, log, files, stat


def build_file_context(files):
    parts = []
    for name in files[:MAX_FILES]:
        p = Path(name)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > MAX_PER_FILE_CHARS:
            content = content[:MAX_PER_FILE_CHARS] + f"\n... [truncated -- {len(content)} chars total]"
        ext = p.suffix.lstrip(".")
        parts.append(f"### {name}\n```{ext}\n{content}\n```")
    return "\n\n".join(parts)


# ----- gh CLI integration -----

def gh_pr_info():
    """Get number, head SHA, base/head refs, url for the current branch's PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json",
             "number,headRefOid,baseRefName,headRefName,url"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def gh_repo_info():
    """Owner login + repo name for the current git remote."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data["owner"]["login"], data["name"]
    except Exception:
        return None


# ----- comment extraction + posting -----

def extract_json_comments(text):
    """Find fenced ```json blocks, parse the last valid one as a list."""
    blocks = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = json.loads(block.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    return []


def split_review(text):
    """Separate the summary (everything before the final JSON block) from the JSON."""
    comments = extract_json_comments(text)
    last_json = re.search(r"```json[\s\S]*?```\s*$", text.strip())
    summary = text[:last_json.start()].rstrip() if last_json else text.strip()
    # Drop any trailing "### Section 2: ..." heading left behind
    summary = re.sub(r"(?im)^#{1,6}\s*Section\s*2.*$", "", summary).rstrip()
    summary = re.sub(r"(?im)^#{1,6}\s*Structured comments.*$", "", summary).rstrip()
    return summary, comments


def format_comment_body(c):
    severity = (c.get("severity") or "").upper()
    tag = SEVERITY_TAGS.get(severity, f"**{severity or 'note'}**")
    body = (c.get("body") or "").strip()
    return f"{tag}\n\n{body}\n\n_— Cookie {persona_pair('cookie')} (Tiramisu AI reviewer)_"


def validate_comments(comments, changed_files):
    """Drop comments with missing/invalid fields or unknown paths."""
    valid = []
    dropped = []
    files_set = set(changed_files)
    for c in comments:
        path = c.get("path")
        line = c.get("line")
        body = c.get("body")
        if not path or line is None or not body:
            dropped.append((c, "missing required field"))
            continue
        if not isinstance(line, int):
            try:
                c["line"] = int(line)
            except (TypeError, ValueError):
                dropped.append((c, "line is not an integer"))
                continue
        if path not in files_set:
            dropped.append((c, f"path '{path}' not in changed files"))
            continue
        valid.append(c)
    return valid, dropped


def post_review(pr_info, owner, repo, comments, summary, has_blockers):
    """POST a single review with inline comments via gh api. Returns (url, error)."""
    event = "REQUEST_CHANGES" if has_blockers else "COMMENT"
    payload = {
        "commit_id": pr_info["headRefOid"],
        "body": summary[:8000] if summary else "Cookie reviewed this PR.",
        "event": event,
    }
    if comments:
        payload["comments"] = [
            {
                "path": c["path"],
                "line": c["line"],
                "side": "RIGHT",
                "body": format_comment_body(c),
            }
            for c in comments
        ]

    result = subprocess.run(
        ["gh", "api", "-X", "POST",
         f"/repos/{owner}/{repo}/pulls/{pr_info['number']}/reviews",
         "--input", "-"],
        input=json.dumps(payload), capture_output=True, text=True
    )

    if result.returncode != 0:
        return None, result.stderr.strip() or "gh api failed"

    try:
        data = json.loads(result.stdout)
        return data.get("html_url"), None
    except json.JSONDecodeError:
        return None, "Could not parse gh api response"


# ----- prompt -----

PR_PROMPT = """\
This is a full PR review -- not a quick LGTM. Be thorough.

## Commits in this PR
{log}

## Full diff
```diff
{diff}
```

## Current state of changed files
{file_context}

Output format -- TWO sections, in this exact order:

### Section 1: Summary
2-4 paragraphs of human-readable analysis covering correctness, design risks,
edge cases, security, and test coverage. End with an overall verdict.

### Section 2: Structured comments
End with a single fenced JSON block. Each comment must have:
  - `path`: file path (must match a changed file exactly)
  - `line`: line number in the NEW version of the file (integer, not the diff line)
  - `severity`: one of "BLOCKER", "MAJOR", "MINOR", "NIT"
  - `body`: comment text -- do NOT put line numbers inside the body

If nothing is worth an inline comment, output `[]`.

Example:
```json
[
  {{
    "path": "src/auth.py",
    "line": 47,
    "severity": "BLOCKER",
    "body": "Null check missing. If `user` is None, this raises AttributeError."
  }}
]
```
"""


# ----- main -----

def parse_args():
    p = argparse.ArgumentParser(description="Cookie PR review")
    p.add_argument("base", nargs="?", default=None,
                   help="Base branch (default: auto-detect main/master)")
    p.add_argument("--post", action="store_true",
                   help="Post inline comments to the GitHub PR")
    p.add_argument("--dry-run", action="store_true",
                   help="With --post: show what would be posted, don't post")
    return p.parse_args()


def main():
    args = parse_args()
    base = find_base_branch(args.base)
    diff, log, files, stat = get_branch_diff(base)

    if not diff:
        print(f"\n[pr] No changes between HEAD and {base}.")
        sys.exit(0)

    if not log:
        print(f"\n[pr] Already up to date with {base}.")
        sys.exit(0)

    file_context = build_file_context(files)

    languages = detect_languages(files)
    system = load_steering(
        agent="cookie",
        languages=languages,
        include_engineering=True,
        include_communication=True,
        include_universal_style=True,
        include_preferences=True,
    )

    lang_label = ("/".join(languages)) if languages else "no language detected"
    print(f"\nCookie is reviewing your PR (vs {base}) [{lang_label}]\n")
    for line in stat.splitlines()[-5:]:
        print(f"  {line}")
    print(f"\n  Commits:")
    for line in log.splitlines()[:8]:
        print(f"    {line}")
    if len(log.splitlines()) > 8:
        print(f"    ... and {len(log.splitlines()) - 8} more")
    print()
    print("-" * 60)

    review_text = invoke_stream_markdown(
        prompt=PR_PROMPT.format(
            log=log[:2000],
            diff=diff[:MAX_DIFF_CHARS],
            file_context=file_context,
        ),
        system=system,
        model=DEFAULT_MODEL,
        max_tokens=3000,
    )

    print("-" * 60)

    if not args.post:
        return

    # ---- --post flow ----
    summary, comments = split_review(review_text)
    comments, dropped = validate_comments(comments, files)
    has_blockers = any(
        (c.get("severity") or "").upper() == "BLOCKER" for c in comments
    )

    pr_info = gh_pr_info()
    if not pr_info:
        print("\n[pr] No open PR found for the current branch.")
        print("     Create one first:  gh pr create")
        sys.exit(1)

    repo_info = gh_repo_info()
    if not repo_info:
        print("\n[pr] Could not detect repo owner/name. Is gh authenticated?")
        sys.exit(1)

    owner, repo = repo_info

    blocker_count = sum(
        1 for c in comments if (c.get("severity") or "").upper() == "BLOCKER"
    )

    print(f"\n{'=' * 60}")
    print(f"  Posting review to PR #{pr_info['number']}  ({owner}/{repo})")
    print(f"  Inline comments: {len(comments)}  ({blocker_count} blockers)")
    print(f"  Verdict: {'REQUEST_CHANGES' if has_blockers else 'COMMENT'}")
    if dropped:
        print(f"  Dropped: {len(dropped)} invalid comment(s)")
    print("=" * 60)

    if dropped:
        print("\n  Dropped comments (won't be posted):")
        for c, reason in dropped[:5]:
            print(f"    - {c.get('path')}:{c.get('line')} -- {reason}")

    if args.dry_run:
        print("\n[dry-run] Would post the following:\n")
        for c in comments:
            sev = (c.get("severity") or "").upper()
            body = (c.get("body") or "").strip().splitlines()[0][:200]
            print(f"  {c.get('path')}:{c.get('line')}  [{sev}]")
            print(f"    {body}\n")
        return

    url, err = post_review(pr_info, owner, repo, comments, summary, has_blockers)
    if url:
        print(f"\n  Posted: {url}\n")
    else:
        print(f"\n[pr] Failed to post review: {err}")
        print("     Common causes: stale commit SHA, line not in diff, no PR write access.")
        sys.exit(1)


if __name__ == "__main__":
    main()
