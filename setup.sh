#!/usr/bin/env bash
# Tiramisu install script for macOS / Linux
# Usage:  ./setup.sh
# Idempotent: safe to re-run after a `git pull` to update dependencies.
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "Installing Tiramisu from: $INSTALL_DIR"
echo ""

# 1. Find Python
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "  [ERROR] Python not found."
    echo "  Install Python 3.10+ and retry."
    exit 1
fi
echo "  [1/4] Python: $($PYTHON --version) -- $(command -v "$PYTHON")"

# 2. Install dependencies
echo "  [2/4] Installing dependencies..."
"$PYTHON" -m pip install --quiet --disable-pip-version-check \
    -r "$INSTALL_DIR/requirements.txt"
echo "        Done (anthropic, rich, prompt_toolkit)."

# 3. Add to PATH (append to .bashrc / .zshrc if not already there)
EXPORT_LINE="export PATH=\"$INSTALL_DIR:\$PATH\""
needs_restart=0
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
    if [ -f "$rc" ] && ! grep -qF "$INSTALL_DIR" "$rc"; then
        case "$rc" in
            *config.fish)
                echo "set -gx PATH \"$INSTALL_DIR\" \$PATH" >> "$rc"
                ;;
            *)
                echo "$EXPORT_LINE" >> "$rc"
                ;;
        esac
        echo "  [3/4] Added to: $rc"
        needs_restart=1
    fi
done
if [ "$needs_restart" -eq 0 ]; then
    echo "  [3/4] PATH already configured."
fi

# 4. Ensure .env exists with a key placeholder
ENV_DIR="$HOME/.tiramisu"
ENV_FILE="$ENV_DIR/.env"
needs_key=0
if [ ! -f "$ENV_FILE" ]; then
    mkdir -p "$ENV_DIR"
    cat > "$ENV_FILE" <<EOF
# Tiramisu environment
# Get your key at: https://console.anthropic.com/
ANTHROPIC_API_KEY=
EOF
    echo "  [4/4] Created: $ENV_FILE"
    needs_key=1
elif ! grep -qE '^ANTHROPIC_API_KEY=\S' "$ENV_FILE"; then
    echo "  [4/4] .env exists but no API key set: $ENV_FILE"
    needs_key=1
else
    echo "  [4/4] .env exists with API key."
fi

# Summary
echo ""
echo "Tiramisu installed."
echo ""
if [ "$needs_restart" -eq 1 ]; then
    echo "  [!] Restart your shell or run: source ~/.bashrc  (or ~/.zshrc)"
fi
if [ "$needs_key" -eq 1 ]; then
    echo "  [!] Edit $ENV_FILE and add your ANTHROPIC_API_KEY"
fi
if [ "$needs_restart" -eq 0 ] && [ "$needs_key" -eq 0 ]; then
    echo "  Try:  tiramisu"
fi
echo ""
