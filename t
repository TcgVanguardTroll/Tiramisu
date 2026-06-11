#!/usr/bin/env bash
# Tiramisu CLI dispatcher (POSIX). Windows uses t.bat instead.
set -e

TIRAMISU_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "[tiramisu] Python not found. Set PYTHON env var or install python3." >&2
    exit 1
fi

cmd="${1:-help}"
shift || true

case "$cmd" in
    hook)
        # t hook [path]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/install_hooks.py" "${1:-.}"
        ;;
    task)
        # t task [description]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/start_task.py" "$@"
        ;;
    review)
        # t review -- Cookie on staged diff
        "$PYTHON" "$TIRAMISU_ROOT/hooks/cookie_review.py"
        ;;
    scan)
        # t scan [path]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/scan.py" "$@"
        ;;
    pr)
        # t pr [base] [--post] [--dry-run]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/pr_review.py" "$@"
        ;;
    implement)
        # t implement "description" [--auto] [--yes]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/implement.py" "$@"
        ;;
    chat)
        # t chat [initial question]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/chat.py" "$@"
        ;;
    learn)
        # t learn "text" | list | forget <id>
        "$PYTHON" "$TIRAMISU_ROOT/scripts/learn.py" "$@"
        ;;
    reflect)
        # t reflect [days]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/reflect.py" "$@"
        ;;
    research)
        # t research [show|run|mute|list|sources ...]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/research.py" "$@"
        ;;
    brainstorm)
        # t brainstorm [topic]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/brainstorm.py" "$@"
        ;;
    onboard)
        # t onboard [description of the unmet need]
        "$PYTHON" "$TIRAMISU_ROOT/scripts/onboard.py" "$@"
        ;;
    help|--help|-h|"")
        cat <<EOF

  Tiramisu  --  t <command> [args]

  t hook [path]       Install Cookie + Eclair hooks (default: current dir)
  t task [desc]       Croissant scope session before you start coding
  t review            Cookie reviews staged diff (outside a commit)
  t scan [path]       Cookie reads a file or directory in full
  t pr [base]         Cookie reviews your whole branch vs main
  t pr --post         ...and posts inline comments to the GitHub PR
  t implement "..."   Eclair writes code with full codebase access
  t chat [question]   Conversational mode -- read-only, remembers context
  t learn "text"      Teach the agents a preference
  t reflect [days]    Weekly self-improvement report
  t research [action] Cannoli's external research; auto-runs weekly
  t brainstorm [...]  Mochi stress-tests an idea before you scope it
  t onboard [...]     Brioche drafts a new agent persona for an unmet need
  t help              This message

EOF
        ;;
    *)
        echo "[tiramisu] Unknown command: $cmd"
        echo "Run: t help"
        exit 1
        ;;
esac
