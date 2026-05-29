"""
second_brain/kb_report.py — On-demand KB usage report.
Usage: python3 second_brain/kb_report.py
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "tiramisu.db")
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
GRACE_DAYS = 30
ARCHIVE_DAYS = 60


def _get_all_knowledge_files() -> dict[str, datetime]:
    """Return {absolute_path: mtime} for all knowledge files, excluding archive/."""
    files = {}
    for ext in ("*.md", "*.txt"):
        for f in KNOWLEDGE_DIR.rglob(ext):
            if "archive" not in f.parts:
                files[str(f.resolve())] = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
    return files


def report():
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc)

    # --- Most Used (top 10 by hit count) ---
    rows = conn.execute(
        "SELECT source_file, COUNT(*) as hits FROM kb_hits WHERE COALESCE(is_test, 0) = 0 GROUP BY source_file ORDER BY hits DESC LIMIT 10"
    ).fetchall()
    total_hits = conn.execute("SELECT COUNT(*) FROM kb_hits WHERE COALESCE(is_test, 0) = 0").fetchone()[0]
    print("=" * 60)
    print("MOST USED (top 10 files by hit count)")
    print("=" * 60)
    if rows:
        for source, hits in rows:
            pct = (hits / total_hits * 100) if total_hits else 0
            flag = " ⚠️ >30%" if pct > 30 else ""
            print(f"  {hits:>5} ({pct:4.1f}%)  {Path(source).name}{flag}")
    else:
        print("  No hits recorded yet.")

    # Build set of files that have any hits
    hit_files = {r[0] for r in conn.execute("SELECT DISTINCT source_file FROM kb_hits WHERE COALESCE(is_test, 0) = 0").fetchall()}

    # --- Never Used ---
    all_files = _get_all_knowledge_files()
    grace_cutoff = now - timedelta(days=GRACE_DAYS)
    never_used = [
        (fp, mtime) for fp, mtime in all_files.items()
        if fp not in hit_files and mtime < grace_cutoff
    ]
    print(f"\n{'=' * 60}")
    print(f"NEVER USED (zero hits, older than {GRACE_DAYS}d grace period)")
    print("=" * 60)
    if never_used:
        for fp, mtime in sorted(never_used, key=lambda x: x[1]):
            age = (now - mtime).days
            print(f"  {age:>4}d old  {Path(fp).name}")
    else:
        print("  None — all files either have hits or are within grace period.")

    # --- Archive Candidates ---
    archive_cutoff = now - timedelta(days=ARCHIVE_DAYS)
    # Files with zero hits in last 60 days AND older than 30 days
    candidates = []
    for fp, mtime in all_files.items():
        if mtime >= grace_cutoff:
            continue  # too new
        recent = conn.execute(
            "SELECT COUNT(*) FROM kb_hits WHERE source_file = ? AND COALESCE(is_test, 0) = 0 AND created_at > ?",
            (fp, archive_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
        ).fetchone()[0]
        if recent == 0:
            age = (now - mtime).days
            candidates.append((fp, age))

    print(f"\n{'=' * 60}")
    print(f"ARCHIVE CANDIDATES (zero hits in {ARCHIVE_DAYS}d, older than {GRACE_DAYS}d)")
    print("=" * 60)
    if candidates:
        for fp, age in sorted(candidates, key=lambda x: -x[1]):
            print(f"  {age:>4}d old  {Path(fp).name}")
    else:
        print("  No archive candidates.")

    # --- Zero-Result Queries ---
    zero_rows = conn.execute(
        "SELECT query, agent, created_at FROM kb_hits WHERE source_file = 'NO_RESULTS' AND COALESCE(is_test, 0) = 0 "
        "ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    zero_count = conn.execute(
        "SELECT COUNT(*) FROM kb_hits WHERE source_file = 'NO_RESULTS' AND COALESCE(is_test, 0) = 0"
    ).fetchone()[0]
    print(f"\n{'=' * 60}")
    print(f"ZERO-RESULT QUERIES ({zero_count} total, showing last 20)")
    print("=" * 60)
    if zero_rows:
        for query, agent, ts in zero_rows:
            agent_str = f" [{agent}]" if agent else ""
            print(f"  {ts[:16]}  {query[:80]}{agent_str}")
    else:
        print("  No zero-result queries recorded.")

    conn.close()


if __name__ == "__main__":
    report()
