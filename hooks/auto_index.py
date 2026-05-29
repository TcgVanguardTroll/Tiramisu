#!/usr/bin/env python3
"""Auto-index knowledge directories on agent spawn. Skips unchanged files."""
import subprocess, sys, os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
TIRAMISU_HOME = Path(os.environ.get("TIRAMISU_HOME", Path.home() / ".tiramisu"))
DIRS = [
    str(TIRAMISU_HOME / "knowledge"),
    str(TIRAMISU_HOME / "knowledge" / "gentoomen"),
    str(TIRAMISU_HOME / "knowledge" / "github-trending"),
]
CLI = str(_ROOT / "second_brain" / "cli.py")

for d in DIRS:
    if os.path.isdir(d):
        r = subprocess.run([sys.executable, CLI, "index", d], capture_output=True, text=True, cwd=os.path.dirname(CLI))
        for line in r.stdout.strip().split("\n"):
            if line.startswith("Done."):
                print(f"[auto-index] {d}: {line}")
        if r.returncode != 0:
            print(f"[auto-index] {d}: ERROR - {r.stderr[-200:]}", file=sys.stderr)
