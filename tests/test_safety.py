"""
Safety invariants for the dangerous tool surfaces (chat.py / implement.py).

These are the failure modes with asymmetric blast radius:
  - Path sandbox escape -> LLM-generated edit_file lands outside the repo root.
    A model that hallucinates `~/.ssh/config` or `C:\\Windows\\...` as a target
    must be refused before the write happens.
  - Confirmation gating bypassed -> a future refactor that "simplifies" the
    write path could remove the Y/n prompt and let writes go silently.
  - Shell command run without confirmation -> the worst case, since shell
    can do anything write_file can plus more.

If these tests fail, something safety-load-bearing regressed.
"""
import os
import sys
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# _validate_path: refuse paths outside the current working directory
# --------------------------------------------------------------------------

def test_validate_path_accepts_path_inside_cwd(tmp_workspace):
    from chat import _validate_path
    inside = tmp_workspace / "sub" / "file.txt"
    assert _validate_path(inside) is None, (
        "an in-cwd path should not produce an error"
    )


def test_validate_path_rejects_absolute_path_outside_cwd(tmp_workspace):
    from chat import _validate_path
    if sys.platform == "win32":
        outside = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    else:
        outside = Path("/etc/passwd")
    err = _validate_path(outside)
    assert err is not None, "path outside cwd should be refused"
    assert "outside" in err.lower()


def test_validate_path_blocks_dotdot_escape(tmp_workspace):
    """`../../something` should resolve to outside cwd and be blocked."""
    from chat import _validate_path
    escape = tmp_workspace / ".." / ".." / "evil.txt"
    err = _validate_path(escape)
    assert err is not None, "../ traversal must be rejected"


def test_validate_path_accepts_nested_paths(tmp_workspace):
    """Deeply nested in-cwd paths must remain valid."""
    from chat import _validate_path
    nested = tmp_workspace / "a" / "b" / "c" / "d.txt"
    assert _validate_path(nested) is None


# --------------------------------------------------------------------------
# _confirm: gating semantics
# --------------------------------------------------------------------------

def test_confirm_y_means_yes(monkeypatch):
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _confirm("apply?") is True


def test_confirm_n_means_no(monkeypatch):
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert _confirm("apply?") is False


def test_confirm_empty_uses_default_yes(monkeypatch):
    """Pressing Enter without typing should accept the default. For most
    write operations the default is 'yes' so the flow is fast."""
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _confirm("apply?", default_yes=True) is True


def test_confirm_empty_uses_default_no(monkeypatch):
    """For dangerous actions (shell) the default is 'no'. Pressing Enter
    without typing must NOT auto-run a shell command."""
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _confirm("run?", default_yes=False) is False


def test_confirm_keyboard_interrupt_is_no(monkeypatch):
    """Ctrl+C during the prompt means 'no, don't do this.' Crashing instead
    would be worse but auto-yes would be the worst."""
    from chat import _confirm

    def raise_kb(_):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", raise_kb)
    assert _confirm("apply?", default_yes=True) is False
    assert _confirm("run?", default_yes=False) is False


def test_confirm_eof_is_no(monkeypatch):
    """EOF (Ctrl+D, piped input ended) means 'no'. Same reasoning as Ctrl+C."""
    from chat import _confirm

    def raise_eof(_):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    assert _confirm("apply?", default_yes=True) is False


def test_confirm_case_insensitive_y(monkeypatch):
    """Some users will type 'Y' with shift held."""
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "Y")
    assert _confirm("apply?") is True


def test_confirm_garbage_input_returns_default(monkeypatch):
    """Random text (not y/n) should NOT count as yes; default kicks in.

    NOTE: current impl returns False on any non-'y' input. We treat "garbage
    behaves like an explicit no" as the conservative contract -- protects
    against typos triggering writes."""
    from chat import _confirm
    monkeypatch.setattr("builtins.input", lambda _: "blah")
    # Whatever the policy is, it must NOT silently say yes when default is no.
    result = _confirm("run?", default_yes=False)
    assert result is False, "garbage input must NOT trigger a default-no action"


# --------------------------------------------------------------------------
# Full execute_tool() integration: writes get refused on bad paths even
# when the user answers 'y' to the prompt
# --------------------------------------------------------------------------

def test_edit_file_refuses_path_outside_cwd_even_with_yes(tmp_workspace, monkeypatch):
    """A confirmation 'y' should not bypass path sandboxing. The validation
    must run BEFORE we read the file or apply the edit."""
    from chat import execute_tool

    # Always say yes -- the test is that the SANDBOX still rejects, not the user
    monkeypatch.setattr("builtins.input", lambda _: "y")

    if sys.platform == "win32":
        target = "C:\\Windows\\Temp\\tiramisu_safety_test.txt"
    else:
        target = "/tmp/tiramisu_safety_test.txt"

    result = execute_tool("edit_file", {
        "path": target,
        "old_string": "anything",
        "new_string": "anything else",
    })
    assert "outside" in result.lower() or "error" in result.lower(), (
        f"edit_file should refuse out-of-cwd paths regardless of confirmation; "
        f"got: {result!r}"
    )
    # And critically: the file must NOT exist or have been modified
    assert not Path(target).exists() or Path(target).read_text() != "anything else"


def test_write_file_refuses_path_outside_cwd_even_with_yes(tmp_workspace, monkeypatch):
    """Same invariant for write_file."""
    from chat import execute_tool

    monkeypatch.setattr("builtins.input", lambda _: "y")

    if sys.platform == "win32":
        target = "C:\\Windows\\Temp\\tiramisu_write_safety_test.txt"
    else:
        target = "/tmp/tiramisu_write_safety_test.txt"

    result = execute_tool("write_file", {
        "path": target,
        "content": "should not be written",
    })
    assert "outside" in result.lower() or "error" in result.lower()
    assert not Path(target).exists() or "should not be written" not in Path(target).read_text(errors="replace")


def test_edit_file_in_cwd_with_no_does_not_modify(tmp_workspace, monkeypatch):
    """If the user says 'n' to the prompt, the file must stay untouched
    even when the path is valid."""
    from chat import execute_tool

    target = tmp_workspace / "hello.txt"
    target.write_text("original content", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = execute_tool("edit_file", {
        "path": str(target),
        "old_string": "original",
        "new_string": "modified",
    })
    assert "decline" in result.lower() or "user" in result.lower()
    assert target.read_text() == "original content", (
        "edit_file must not modify the file when user says 'n'"
    )


def test_write_file_in_cwd_with_no_does_not_create(tmp_workspace, monkeypatch):
    """User-declined write must not create the file."""
    from chat import execute_tool

    target = tmp_workspace / "new_file.txt"
    assert not target.exists()

    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = execute_tool("write_file", {
        "path": str(target),
        "content": "should not be created",
    })
    assert "decline" in result.lower() or "user" in result.lower()
    assert not target.exists(), "declined write must not create the file"


def test_shell_command_defaults_to_no_on_empty_input(tmp_workspace, monkeypatch):
    """Pressing Enter at the shell-confirmation prompt must NOT run the command.
    This is the highest-leverage safety guarantee in the codebase."""
    from chat import execute_tool

    monkeypatch.setattr("builtins.input", lambda _: "")

    result = execute_tool("run_shell", {
        "command": "echo TIRAMISU_SHELL_DID_NOT_GUARD",
    })
    assert "decline" in result.lower() or "user" in result.lower()
    # The echo should not have run; result should not contain its output
    assert "TIRAMISU_SHELL_DID_NOT_GUARD" not in result, (
        "shell command ran without explicit user 'y' -- safety gate broken"
    )
