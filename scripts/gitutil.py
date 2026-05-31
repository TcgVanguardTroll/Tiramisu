r"""
Cross-platform git executable resolution.

On Windows, subprocess.run(["git", ...]) sometimes fails with FileNotFoundError
even when the user's shell can run `git` fine. The usual cause is that git was
installed for a single user (under %LOCALAPPDATA%\Programs\Git) or installed
without updating the system PATH. Python's spawned subprocess looks at PATH
literally; it doesn't share the shell's resolution magic.

This module finds git by:
  1. Asking shutil.which() (honors PATH normally)
  2. Falling back to a list of common install locations
  3. Returning "git" as a last resort so the original error surfaces clearly

Result is cached for the process lifetime.
"""
import os
import shutil
import subprocess
import sys


# Common install locations for git on Windows + POSIX
_GIT_FALLBACK_PATHS = [
    # Windows -- system-wide installs
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files\Git\bin\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\bin\git.exe",
    # Windows -- per-user installs
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\git.exe"),
    # POSIX
    "/usr/bin/git",
    "/usr/local/bin/git",
    "/opt/homebrew/bin/git",
    "/opt/local/bin/git",
]

_cached_git = None


def git_exe():
    """Locate git. Tries PATH first, then known install locations.
    Caches the result for the lifetime of the Python process."""
    global _cached_git
    if _cached_git:
        return _cached_git

    found = shutil.which("git")
    if found:
        _cached_git = found
        return found

    for path in _GIT_FALLBACK_PATHS:
        if path and os.path.exists(path):
            _cached_git = path
            return path

    return "git"   # last resort; caller will see FileNotFoundError with clear context


def run_git(*args, **kwargs):
    """
    Convenience wrapper: subprocess.run([git_exe(), *args], **kwargs).
    Sets sensible defaults (capture_output=True, text=True). Raises a clear
    RuntimeError if git is genuinely missing.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    try:
        return subprocess.run([git_exe(), *args], **kwargs)
    except FileNotFoundError:
        raise RuntimeError(
            "git was not found on PATH or at any standard install location.\n"
            "  Install Git from https://git-scm.com/download and ensure it's on PATH,\n"
            "  or restart your terminal so the updated PATH is picked up."
        )
