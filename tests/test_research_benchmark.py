"""
Tests for Cannoli's benchmark layer (`t research benchmark`): autonomous
"what could Tiramisu borrow from trending repos?" analysis.

No network and no LLM calls -- the GitHub fetch, README fetch, and the
per-repo analysis are all monkeypatched. We pin:
  - report assembly is well-formed and proposal-only,
  - README fetch tries default branch then main/master,
  - repo collection dedupes, sorts by stars, and caps,
  - benchmark() writes a recognized research output,
  - the output is picked up by research.py's pending/list machinery.
"""
import research_benchmark as rb


# --------------------------------------------------------------------------
# _build_report -- pure
# --------------------------------------------------------------------------

def test_build_report_has_header_and_sections():
    body = rb._build_report(["### a/b\nstuff", "### c/d\nmore"],
                            ["claude"], "2026-06-18")
    assert body.startswith("# Cannoli benchmark -- 2026-06-18")
    assert "### a/b" in body and "### c/d" in body


def test_build_report_is_proposal_only():
    body = rb._build_report(["### a/b\nx"], ["claude", "ai-agents"], "2026-06-18")
    assert "Nothing here is applied" in body
    assert "claude, ai-agents" in body  # topics listed


# --------------------------------------------------------------------------
# _readme_for -- branch fallback
# --------------------------------------------------------------------------

def test_readme_for_uses_default_branch(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return "# Real readme" if "/trunk/" in url else "[error 404]"

    monkeypatch.setattr(rb, "_fetch", fake_fetch)
    out = rb._readme_for({"full_name": "o/r", "default_branch": "trunk"})
    assert out == "# Real readme"
    assert calls[0].endswith("/o/r/trunk/README.md")


def test_readme_for_falls_back_to_main(monkeypatch):
    def fake_fetch(url):
        return "# Found" if "/main/" in url else "[error 404]"
    monkeypatch.setattr(rb, "_fetch", fake_fetch)
    out = rb._readme_for({"full_name": "o/r"})  # no default_branch
    assert out == "# Found"


def test_readme_for_returns_none_when_all_unreachable(monkeypatch):
    monkeypatch.setattr(rb, "_fetch", lambda url: "[error 404]")
    assert rb._readme_for({"full_name": "o/r"}) is None


def test_readme_for_no_full_name():
    assert rb._readme_for({}) is None


# --------------------------------------------------------------------------
# _collect_repos -- dedupe / sort / cap
# --------------------------------------------------------------------------

def test_collect_repos_dedupes_and_sorts_by_stars(monkeypatch):
    topic_data = {
        "claude": [
            {"full_name": "o/low", "stargazers_count": 10},
            {"full_name": "o/high", "stargazers_count": 999},
        ],
        "ai-agents": [
            {"full_name": "o/high", "stargazers_count": 999},  # dup
            {"full_name": "o/mid", "stargazers_count": 500},
        ],
    }
    monkeypatch.setattr(rb, "_fetch_github_topic",
                        lambda topic, since: topic_data.get(topic, []))
    repos = rb._collect_repos(["claude", "ai-agents"])
    names = [r["full_name"] for r in repos]
    assert names == ["o/high", "o/mid", "o/low"]  # sorted desc, deduped


def test_collect_repos_caps_at_max(monkeypatch):
    many = [{"full_name": f"o/r{i}", "stargazers_count": i} for i in range(50)]
    monkeypatch.setattr(rb, "_fetch_github_topic", lambda topic, since: many)
    monkeypatch.setattr(rb, "MAX_REPOS", 8)
    assert len(rb._collect_repos(["claude"])) == 8


# --------------------------------------------------------------------------
# benchmark() -- writes a recognized output
# --------------------------------------------------------------------------

def test_benchmark_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(rb, "_collect_repos",
                        lambda topics: [{"full_name": "o/r", "stargazers_count": 5}])
    monkeypatch.setattr(rb, "_analyze",
                        lambda repo: "### o/r\n**Relevance:** 3/5\n")
    out = rb.benchmark(topics=["claude"], quiet=True)
    assert out is not None and out.exists()
    assert out.name.startswith("benchmark_")
    assert "### o/r" in out.read_text(encoding="utf-8")


def test_benchmark_no_repos_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(rb, "_collect_repos", lambda topics: [])
    assert rb.benchmark(topics=["claude"], quiet=True) is None


# --------------------------------------------------------------------------
# research.py integration: benchmark output is a recognized research file
# --------------------------------------------------------------------------

def test_is_research_output_recognizes_benchmark():
    import research
    from pathlib import Path
    assert research._is_research_output(Path("benchmark_2026-06-18.md"))
    assert research._is_research_output(Path("candidates_2026-06-18.md"))
    assert research._is_research_output(Path("findings_2026-06-18.md"))
    assert not research._is_research_output(Path("notes.md"))


def test_benchmark_output_counts_as_pending(tmp_tiramisu_home, monkeypatch):
    import research
    rd = tmp_tiramisu_home / ".research"
    rd.mkdir(parents=True)
    monkeypatch.setattr(research, "RESEARCH_DIR", rd)
    (rd / "benchmark_2026-06-18.md").write_text("# b", encoding="utf-8")
    assert research.pending_count() == 1
