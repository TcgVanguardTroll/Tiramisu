#!/usr/bin/python3.9
"""
second_brain/cli.py — CLI interface for the local second brain.

Usage:
    python cli.py index <docs_dir> [--force]
    python cli.py query <docs_dir> "your question here" [--top-k 5] [--no-llm]
    python cli.py watch <docs_dir>
"""

import argparse
import sys
import textwrap
import time

from brain import index_files, search_brain, index_single_file, remove_deleted_file
from user_profile import load_profile, format_profile


def cmd_index(args):
    print(f"Indexing markdown files in: {args.docs_dir}")
    if args.force:
        print("Force re-index: all files will be re-embedded.")
    stats = index_files(args.docs_dir, index_dir=args.index_dir, force=args.force)
    print(f"Done. Files: {stats['total']} | Indexed: {stats['indexed']} | "
          f"Skipped (unchanged): {stats['skipped']} | Chunks created: {stats['chunks']}")


def cmd_query(args):
    """One-shot query. Prints clean text to stdout, exits non-zero on error."""
    q = " ".join(args.question)
    if not q.strip():
        print("Error: empty query.", file=sys.stderr)
        sys.exit(1)

    profile = None
    if not args.no_profile:
        profile = load_profile(args.profile)
        if profile:
            print(f"[profile] Loaded profile for {profile.get('name', '?')}", file=sys.stderr)

    try:
        result = search_brain(q, args.docs_dir, index_dir=args.index_dir,
                       top_k=args.top_k, synthesize=not args.no_llm,
                       profile=profile, agent=getattr(args, 'agent', None),
                       is_test=getattr(args, 'test', False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # If LLM synthesis available, print just the answer
    if result["synthesis"]:
        print(result["synthesis"])
        sys.exit(0)

    # Fallback: print retrieved chunks as plain text
    if not result["results"]:
        print("No results found.", file=sys.stderr)
        sys.exit(1)

    for i, item in enumerate(result["results"], 1):
        meta = item["metadata"]
        src = meta.get("source", "?")
        date = meta.get("date", "")
        section = meta.get("section", "")
        header = f"[{i}] {src}"
        if date:
            header += f" ({date})"
        if section:
            header += f" | {section}"
        print(header)
        print(item["text"][:500])
        print()


def cmd_profile(args):
    """Show/validate the user profile."""
    profile = load_profile(args.profile)
    if not profile:
        print("No profile found. Create second_brain/profile.yaml to enable personalization.",
              file=sys.stderr)
        sys.exit(1)
    print(format_profile(profile))


def cmd_watch(args):
    """Watch a directory for .md changes and auto-index."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    from pathlib import Path

    docs_dir = args.docs_dir
    index_dir = args.index_dir
    if index_dir is None:
        from brain import CHROMA_DIR
        index_dir = str(Path(docs_dir).resolve() / CHROMA_DIR)

    # Initial index
    print(f"[watch] Initial index of {docs_dir}")
    stats = index_files(docs_dir, index_dir=index_dir, force=False)
    print(f"[watch] Indexed {stats['indexed']} files, "
          f"skipped {stats['skipped']}, {stats['chunks']} chunks")

    class Handler(FileSystemEventHandler):
        def _handle(self, event):
            if event.is_directory:
                return
            path = event.src_path
            if not (path.endswith(".md") or path.endswith(".txt")):
                return
            print(f"[watch] Change detected: {path}")
            try:
                s = index_single_file(path, index_dir=index_dir)
                print(f"[watch] Re-indexed: {s['indexed']} files, {s['chunks']} chunks")
            except Exception as e:
                print(f"[watch] Error indexing: {e}", file=sys.stderr)

        def on_created(self, event):
            self._handle(event)

        def on_modified(self, event):
            self._handle(event)

        def on_deleted(self, event):
            if event.is_directory or not (event.src_path.endswith(".md") or event.src_path.endswith(".txt")):
                return
            print(f"[watch] Deleted: {event.src_path}")
            try:
                remove_deleted_file(event.src_path, index_dir=index_dir)
            except Exception as e:
                print(f"[watch] Error cleaning up after delete: {e}", file=sys.stderr)

    observer = Observer()
    observer.schedule(Handler(), docs_dir, recursive=True)
    observer.start()
    print(f"[watch] Watching {docs_dir} for changes... (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[watch] Stopping.")
        observer.stop()
    observer.join()


def main():
    parser = argparse.ArgumentParser(
        description="Second Brain — local knowledge management system",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p_idx = sub.add_parser("index", help="Index markdown files")
    p_idx.add_argument("docs_dir", help="Directory containing markdown files")
    p_idx.add_argument("--force", action="store_true", help="Re-index all files")
    p_idx.add_argument("--index-dir", default=None, help="Custom index directory")

    # query
    p_q = sub.add_parser("query", help="Query your knowledge base")
    p_q.add_argument("docs_dir", help="Directory containing markdown files")
    p_q.add_argument("question", nargs="+", help="Your question")
    p_q.add_argument("--top-k", type=int, default=5, help="Number of results")
    p_q.add_argument("--no-llm", action="store_true", help="Skip LLM synthesis")
    p_q.add_argument("--no-profile", action="store_true", help="Disable profile personalization")
    p_q.add_argument("--profile", default=None, help="Path to profile YAML")
    p_q.add_argument("--index-dir", default=None, help="Custom index directory")
    p_q.add_argument("--agent", default=None, help="Agent name for KB hit attribution")
    p_q.add_argument("--test", action="store_true", help="Mark query as test (excluded from analytics)")

    # watch
    p_w = sub.add_parser("watch", help="Watch directory and auto-index on changes")
    p_w.add_argument("docs_dir", help="Directory containing markdown files")
    p_w.add_argument("--index-dir", default=None, help="Custom index directory")

    # profile
    p_p = sub.add_parser("profile", help="Show/validate user profile")
    p_p.add_argument("--profile", default=None, help="Path to profile YAML")

    args = parser.parse_args()
    if args.command == "index":
        cmd_index(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "profile":
        cmd_profile(args)


if __name__ == "__main__":
    main()
