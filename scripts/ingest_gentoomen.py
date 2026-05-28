#!/usr/bin/env python3
"""
Convert downloaded Gentoomen Library PDFs to .txt files in knowledge/gentoomen/.
Skips iCloud stubs (not-yet-downloaded files) and already-converted files.
Safe to re-run — idempotent.
"""
import os
import sys
import ctypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

LIBRARY_DIR = Path(r"C:\iCloudDrive\Desktop\Gentoomen Library")
ROOT        = Path(__file__).resolve().parent.parent
TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
OUTPUT_DIR  = TIRAMISU_HOME / "knowledge" / "gentoomen"

PRIORITY_CATEGORIES = [
    "Programming", "Algorithms", "Data Structures", "Software Engineering",
    "Databases", "Artificial Intelligence", "Security", "Networking",
    "Operating Systems",
]

MAX_PAGES  = 60    # cap per PDF to keep chunks manageable
MIN_CHARS  = 200   # skip pages with almost no text (scanned images)
MAX_WORKERS = 4    # parallel PDF conversions

# Windows file attribute flags for iCloud/OneDrive stubs
_RECALL_ON_DATA_ACCESS = 0x00400000
_OFFLINE               = 0x00001000


def is_downloaded(path: Path) -> bool:
    """Return False if the file is a cloud-provider stub not yet synced locally."""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF:
            return False
        return not (attrs & _RECALL_ON_DATA_ACCESS or attrs & _OFFLINE)
    except Exception:
        return True  # non-Windows: assume downloaded


def pdf_to_text(pdf_path: Path, max_pages: int = MAX_PAGES) -> str:
    import fitz
    data = pdf_path.read_bytes()
    doc  = fitz.open(stream=data, filetype="pdf")
    pages = min(len(doc), max_pages)
    parts = []
    for i in range(pages):
        text = doc[i].get_text("text").strip()
        if len(text) >= MIN_CHARS:
            parts.append(text)
    doc.close()
    return "\n\n".join(parts)


def output_path(pdf_path: Path) -> Path:
    rel  = pdf_path.relative_to(LIBRARY_DIR)
    safe = "__".join(rel.parts[:-1] + (rel.stem,)) + ".txt"
    return OUTPUT_DIR / safe


def collect_pdfs() -> list[Path]:
    all_pdfs = list(LIBRARY_DIR.rglob("*.pdf"))
    priority, rest = [], []
    for p in all_pdfs:
        parts_lower = [x.lower() for x in p.parts]
        if any(cat.lower() in parts_lower for cat in PRIORITY_CATEGORIES):
            priority.append(p)
        else:
            rest.append(p)
    return priority + rest


def convert_one(pdf: Path) -> tuple[str, str]:
    """Returns (status, message): status is 'done'|'skip'|'stub'|'error'."""
    if not is_downloaded(pdf):
        return "stub", pdf.name
    out = output_path(pdf)
    if out.exists():
        return "skip", pdf.name
    try:
        text = pdf_to_text(pdf)
        if not text.strip():
            return "skip", f"{pdf.name} (no extractable text)"
        header = (
            f"# {pdf.stem}\n"
            f"<!-- category: reference-link | source: gentoomen "
            f"| tags: {pdf.parent.name.lower()} -->\n\n"
        )
        out.write_text(header + text, encoding="utf-8")
        return "done", pdf.name
    except Exception as e:
        return "error", f"{pdf.name}: {e}"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfs  = collect_pdfs()
    total = len(pdfs)

    # Quick summary before starting
    stubs = sum(1 for p in pdfs if not is_downloaded(p))
    print(f"[ingest-gentoomen] {total} PDFs found — {stubs} iCloud stubs (skipped), "
          f"{total - stubs} to process", flush=True)

    done = skipped = errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(convert_one, p): p for p in pdfs}
        for i, future in enumerate(as_completed(futures), 1):
            status, msg = future.result()
            if status == "done":
                done += 1
                print(f"[ingest-gentoomen] [{i}/{total}] OK {msg}", flush=True)
            elif status == "error":
                errors += 1
                print(f"[ingest-gentoomen] [{i}/{total}] ERROR {msg}", file=sys.stderr, flush=True)
            # stub/skip: silent

    print(f"\n[ingest-gentoomen] Done. Converted: {done} | Skipped: {skipped + (stubs)} | Errors: {errors}")
    print(f"[ingest-gentoomen] Output: {OUTPUT_DIR}")
    if done:
        cli = ROOT / "second_brain" / "cli.py"
        print(f"[ingest-gentoomen] Now run: python3 {cli} index {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
