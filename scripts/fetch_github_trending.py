#!/usr/bin/env python3
"""
Fetch trending GitHub repos and write knowledge docs using the GitHub API only.
Gets the repo file tree in one call, then fetches only the docs and source files
we actually want. No git clone, no disk writes beyond the output file.
"""
import os
import sys
import json
import ssl
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
OUTPUT_DIR    = TIRAMISU_HOME / "knowledge" / "github-trending"
STATE_FILE    = OUTPUT_DIR / ".fetch_state.json"


def _load_dotenv():
    env_file = TIRAMISU_HOME / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
TRENDING_LANGUAGES = ["", "python", "typescript", "rust", "go", "java"]
REPOS_PER_LANGUAGE = 8
MAX_SOURCE_FILES   = 20
MAX_FILE_BYTES     = 50_000
MAX_WORKERS        = 3   # GitHub secondary rate limit is aggressive; keep low

SOURCE_EXTS = {".py", ".rs", ".ts", ".go", ".java", ".js", ".cpp", ".c", ".rb", ".swift", ".kt", ".cs"}
DOC_DIRS    = {"docs", "doc", "documentation", "wiki", ".github", "examples", "example"}
SKIP_DIRS   = {"node_modules", "__pycache__", "target", "dist", "build", ".tox", "vendor", ".next"}


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def gh_get(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tiramisu-knowledge-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[github-trending] API error {url}: {e}", file=sys.stderr)
        return None


def fetch_raw(owner: str, name: str, branch: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tiramisu-knowledge-bot"})
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx()) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_trending(language: str) -> list[dict]:
    since = (date.today() - timedelta(days=7)).isoformat()
    lang  = f"+language:{language}" if language else ""
    url   = (f"https://api.github.com/search/repositories"
             f"?q=created:>{since}{lang}&sort=stars&order=desc&per_page={REPOS_PER_LANGUAGE}")
    data  = gh_get(url)
    return data.get("items", []) if data else []


def select_files(tree_items: list[dict]) -> tuple[list[str], list[str]]:
    docs, srcs = [], []
    for item in tree_items:
        path = item["path"]
        size = item.get("size", 0)
        if size == 0 or size > MAX_FILE_BYTES:
            continue
        parts = path.split("/")
        # Skip generated/vendor directories
        if set(p.lower() for p in parts[:-1]) & SKIP_DIRS:
            continue
        filename  = parts[-1].lower()
        suffix    = Path(filename).suffix
        parent_dirs = {p.lower() for p in parts[:-1]}
        if suffix == ".md":
            in_doc_dir = bool(parent_dirs & DOC_DIRS)
            if filename.startswith("readme") or in_doc_dir or len(parts) == 1:
                docs.append(path)
        elif suffix in SOURCE_EXTS and len(srcs) < MAX_SOURCE_FILES:
            srcs.append(path)
    return docs, srcs


def process_repo(repo: dict, today: str) -> tuple[str, int] | None:
    full_name   = repo["full_name"]
    owner, name = full_name.split("/", 1)
    stars       = repo.get("stargazers_count", 0)
    language    = repo.get("language") or "unknown"
    description = repo.get("description") or ""
    topics      = ", ".join(repo.get("topics", []))
    branch      = repo.get("default_branch", "main")

    tree_data = gh_get(f"https://api.github.com/repos/{full_name}/git/trees/{branch}?recursive=1")
    if not tree_data:
        return None
    tree_items = [i for i in tree_data.get("tree", []) if i["type"] == "blob"]

    doc_paths, src_paths = select_files(tree_items)
    if not doc_paths and not src_paths:
        return None

    sections = [
        f"# {full_name}",
        f"<!-- category: reference-link | tags: github, trending, {language.lower()} | promoted: {today} -->",
        "",
        f"**Stars:** {stars:,}  ",
        f"**Language:** {language}  ",
        f"**Description:** {description}  ",
        f"**Topics:** {topics or 'none'}  ",
        f"**URL:** https://github.com/{full_name}  ",
        "",
        "---",
        "",
    ]

    for path in doc_paths:
        text = fetch_raw(owner, name, branch, path).strip()
        if text:
            sections += [f"## {path}", "", text, ""]

    if src_paths:
        sections += ["## Source Files", ""]
        for path in src_paths:
            text = fetch_raw(owner, name, branch, path).strip()
            if text:
                ext = Path(path).suffix.lstrip(".")
                sections += [f"### `{path}`", f"```{ext}", text, "```", ""]

    content = "\n".join(sections)
    out_path = OUTPUT_DIR / f"{language.lower()}__{owner}__{name}.md"
    out_path.write_text(content, encoding="utf-8")
    return full_name, len(content)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"fetched": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    if not GITHUB_TOKEN:
        print(
            "[github-trending] WARNING: GITHUB_TOKEN not set — limited to 60 API req/hour.\n"
            "  Add to your .env:  GITHUB_TOKEN=ghp_...\n"
            "  Get one at: https://github.com/settings/tokens (no scopes needed for public repos)",
            file=sys.stderr,
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state      = load_state()
    today      = date.today().isoformat()
    done_today = set(state["fetched"].get(today, []))

    all_repos = []
    seen = set()
    for lang in TRENDING_LANGUAGES:
        for r in get_trending(lang):
            fn = r["full_name"]
            if fn not in seen and fn not in done_today:
                seen.add(fn)
                all_repos.append(r)

    print(f"[github-trending] {len(all_repos)} repos to index")
    if not all_repos:
        print("[github-trending] Nothing to do.")
        return

    saved = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_repo, r, today): r["full_name"] for r in all_repos}
        for future in as_completed(futures):
            full_name = futures[future]
            try:
                result = future.result()
                if result:
                    fn, size = result
                    saved.append(fn)
                    print(f"[github-trending] OK {fn} ({size // 1024}KB)", flush=True)
                else:
                    print(f"[github-trending] SKIP {full_name}", file=sys.stderr)
            except Exception as e:
                print(f"[github-trending] ERROR {full_name}: {e}", file=sys.stderr)

    state["fetched"][today] = list(done_today | set(saved))
    for old in sorted(state["fetched"])[:-7]:
        del state["fetched"][old]
    save_state(state)

    print(f"\n[github-trending] Done. Saved {len(saved)} repos to {OUTPUT_DIR}")
    if saved:
        cli = ROOT / "second_brain" / "cli.py"
        print(f"[github-trending] Now run: python3 {cli} index {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
