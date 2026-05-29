"""Tests for KB hit tracking MVP: _log_search_hits, archive, kb_report, index_files exclusion."""
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

# Ensure second_brain is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestLogSearchHits(TestCase):
    """Tests for _log_search_hits() in brain.py."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE kb_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL, chunk_id TEXT, agent TEXT,
                query TEXT NOT NULL, distance REAL, rank INTEGER,
                result_count INTEGER, created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def _call(self, items, query, agent=None):
        from brain import _log_search_hits
        with patch("brain.DB_PATH", self.db_path):
            _log_search_hits(items, query, agent)

    def test_logs_all_items(self):
        items = [
            {"id": "c1", "metadata": {"source": "/k/a.md"}, "distance": 0.1},
            {"id": "c2", "metadata": {"source": "/k/b.md"}, "distance": 0.3},
        ]
        self._call(items, "test query", agent="cannoli")
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT * FROM kb_hits ORDER BY rank").fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        # rank 0
        self.assertEqual(rows[0][1], "/k/a.md")  # source_file
        self.assertEqual(rows[0][2], "c1")        # chunk_id
        self.assertEqual(rows[0][3], "cannoli")    # agent
        self.assertEqual(rows[0][4], "test query")
        self.assertAlmostEqual(rows[0][5], 0.1)   # distance
        self.assertEqual(rows[0][6], 0)            # rank
        self.assertEqual(rows[0][7], 2)            # result_count
        # rank 1
        self.assertEqual(rows[1][6], 1)

    def test_agent_none(self):
        items = [{"id": "c1", "metadata": {"source": "/k/a.md"}, "distance": 0.2}]
        self._call(items, "q")
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT agent FROM kb_hits").fetchone()
        conn.close()
        self.assertIsNone(row[0])

    def test_empty_items(self):
        self._call([], "q")
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM kb_hits").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_does_not_raise_on_db_error(self):
        """Logging failure must not propagate."""
        from brain import _log_search_hits
        with patch("brain.DB_PATH", "/nonexistent/path.db"):
            # Should not raise
            _log_search_hits(
                [{"id": "c1", "metadata": {"source": "x"}, "distance": 0.1}],
                "q"
            )


class TestArchiveKnowledgeFile(TestCase):
    """Tests for archive_knowledge_file() in brain.py."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.docs_dir = os.path.join(self.tmpdir, "knowledge")
        os.makedirs(self.docs_dir)
        self.index_dir = os.path.join(self.docs_dir, ".second_brain_index")
        os.makedirs(self.index_dir)
        # Write a dummy hash index
        Path(os.path.join(self.index_dir, "file_hashes.json")).write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_moves_file_to_archive(self):
        src = Path(self.docs_dir) / "test.md"
        src.write_text("hello")
        from brain import archive_knowledge_file
        dest = archive_knowledge_file(str(src), self.docs_dir, self.index_dir)
        self.assertFalse(src.exists())
        self.assertTrue(Path(dest).exists())
        self.assertIn("archive", dest)

    def test_creates_archive_dir(self):
        src = Path(self.docs_dir) / "test.md"
        src.write_text("hello")
        from brain import archive_knowledge_file
        archive_knowledge_file(str(src), self.docs_dir, self.index_dir)
        self.assertTrue((Path(self.docs_dir) / "archive").is_dir())

    def test_raises_on_missing_file(self):
        from brain import archive_knowledge_file
        with self.assertRaises(FileNotFoundError):
            archive_knowledge_file("/nonexistent.md", self.docs_dir, self.index_dir)


class TestIndexFilesExcludesArchive(TestCase):
    """Verify index_files skips knowledge/archive/ directory."""

    def test_archive_excluded_from_rglob(self):
        """Simulate the filtering logic used in index_files."""
        tmpdir = tempfile.mkdtemp()
        try:
            docs = Path(tmpdir)
            (docs / "a.md").write_text("# A")
            archive = docs / "archive"
            archive.mkdir()
            (archive / "b.md").write_text("# B")

            md_files = sorted(
                f for ext in ("*.md", "*.txt") for f in docs.rglob(ext)
                if "archive" not in f.parts
            )
            names = [f.name for f in md_files]
            self.assertIn("a.md", names)
            self.assertNotIn("b.md", names)
        finally:
            shutil.rmtree(tmpdir)


class TestKbReport(TestCase):
    """Tests for kb_report.py report output."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE kb_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL, chunk_id TEXT, agent TEXT,
                query TEXT NOT NULL, distance REAL, rank INTEGER,
                result_count INTEGER, created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        os.unlink(self.db_path)
        shutil.rmtree(self.tmpdir)

    def test_report_runs_with_no_data(self):
        """Report should not crash with empty tables and no files."""
        import kb_report
        with patch.object(kb_report, "DB_PATH", self.db_path), \
             patch.object(kb_report, "KNOWLEDGE_DIR", Path(self.tmpdir)):
            kb_report.report()  # should not raise

    def test_most_used_shows_hits(self):
        conn = sqlite3.connect(self.db_path)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(3):
            conn.execute(
                "INSERT INTO kb_hits (source_file, query, created_at) VALUES (?, ?, ?)",
                ("/k/popular.md", "q", now)
            )
        conn.commit()
        conn.close()

        import kb_report
        from io import StringIO
        with patch.object(kb_report, "DB_PATH", self.db_path), \
             patch.object(kb_report, "KNOWLEDGE_DIR", Path(self.tmpdir)), \
             patch("sys.stdout", new_callable=StringIO) as out:
            kb_report.report()
        self.assertIn("popular.md", out.getvalue())
        self.assertIn("3", out.getvalue())


    def test_never_used_shows_old_files_without_hits(self):
        """Files older than grace period with zero hits appear in Never Used."""
        # Create a file and backdate its mtime by 45 days
        old_file = Path(self.tmpdir) / "stale.md"
        old_file.write_text("# Stale")
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        import kb_report
        from io import StringIO
        with patch.object(kb_report, "DB_PATH", self.db_path), \
             patch.object(kb_report, "KNOWLEDGE_DIR", Path(self.tmpdir)), \
             patch("sys.stdout", new_callable=StringIO) as out:
            kb_report.report()
        output = out.getvalue()
        self.assertIn("stale.md", output)
        self.assertIn("NEVER USED", output)

    def test_archive_candidates_shows_old_files_with_no_recent_hits(self):
        """Files older than 30d with zero hits in last 60d appear in Archive Candidates."""
        # Create a file and backdate its mtime by 45 days
        old_file = Path(self.tmpdir) / "archive_me.md"
        old_file.write_text("# Old")
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        # Insert a hit from 90 days ago (outside the 60d window)
        conn = sqlite3.connect(self.db_path)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO kb_hits (source_file, query, created_at) VALUES (?, ?, ?)",
            (str(old_file.resolve()), "old query", old_ts)
        )
        conn.commit()
        conn.close()

        import kb_report
        from io import StringIO
        with patch.object(kb_report, "DB_PATH", self.db_path), \
             patch.object(kb_report, "KNOWLEDGE_DIR", Path(self.tmpdir)), \
             patch("sys.stdout", new_callable=StringIO) as out:
            kb_report.report()
        output = out.getvalue()
        self.assertIn("archive_me.md", output)
        self.assertIn("ARCHIVE CANDIDATES", output)


if __name__ == "__main__":
    main()
