# Tiramisu — Terminal UI customization

Tiramisu's terminal output is opinionated by default but tunable via two env
vars: one for how agent responses render, one for which "thinking" spinner
animates during the wait.

This is the deep dive. The README mentions the env vars; this page lists the
options and shows what each looks like.

---

## `TIRAMISU_RENDER` — how agent output is shown

Cookie's reviews, Croissant's plans, Madeleine's reports, and any other
agent that streams a longer markdown response can render in three modes:

| `TIRAMISU_RENDER` | Behavior |
|---|---|
| `both` *(default)* | Stream raw text live, then print a rendered Markdown view below a divider. Two-phase: real-time feedback during the call plus a polished view to scroll back to. |
| `stream` | Stream raw text only. No rendered view. Cleanest for piping output to files or grepping. |
| `rendered` | Silent buffer with a thinking-spinner during the API call, then print the rendered Markdown only. No raw text shown. |

The default (`both`) is the safest for first-time users — you see something happening immediately, plus a polished view at the end.

### Why two-phase?

The original implementation tried to live-render markdown while streaming (using `rich.Live` + `rich.Markdown`). It worked great for short responses but broke visually on long ones: when the rendered content exceeds the terminal height, the cursor can't redraw the upper portion in place, so each chunk *appears* to duplicate as content grows.

Two-phase sidesteps this: stream cleanly during the call, render once at the end. No flicker, no duplication.

### Set per session

```powershell
$env:TIRAMISU_RENDER = "rendered"
t scan
```

### Set permanently

PowerShell — add to `$PROFILE`:
```powershell
$env:TIRAMISU_RENDER = "rendered"
```

POSIX — add to `~/.bashrc` or `~/.zshrc`:
```bash
export TIRAMISU_RENDER=rendered
```

`TIRAMISU_NO_RENDER=1` is kept as a deprecated alias for `stream`.

---

## `TIRAMISU_SPINNER` — what the "thinking…" indicator looks like

The wait indicator (visible during router decisions and in `rendered` mode) has five animal-themed variants:

| Value | Looks like | Vibe |
|---|---|---|
| `paws` *(default)* | 🐾 walking paw prints with a fading trail | calm, steady (120ms/frame) |
| `chase` | 🐶 puppy running across the line | energetic (100ms/frame) |
| `pastries` | 🍮 🥐 🍪 🧁 🍩 🍡 🍞 🍫 rotating | leisurely (180ms/frame) |
| `naptime` | 🐱 cat sleeping, zzz building | sleepy (280ms/frame) |
| `sniff` | 🐶 puppy sniffing left-to-right and back | quick (110ms/frame) |
| any rich built-in | `dots`, `dots2`, `line`, `arrow`, etc. | passes through |

```powershell
$env:TIRAMISU_SPINNER = "pastries"
tiramisu look at my code
```

Set permanently the same way as `TIRAMISU_RENDER`.

### Adding your own spinner

Edit `scripts/spinners.py` and add an entry to `SPINNERS`:

```python
SPINNERS["my_spinner"] = {
    "interval": 100,                  # ms per frame
    "frames": ["🦊", "🦊 .", "🦊 ..", "🦊 ..."],
}
```

Then set `TIRAMISU_SPINNER=my_spinner`. The intervals are tunable per spinner if the default feels too fast or slow on your terminal.

---

## REPL keys (inside `tiramisu`)

When you launch `tiramisu` with no args, you enter an interactive REPL backed by `prompt_toolkit`. A few built-in keys:

| Key | Effect |
|---|---|
| `↑` / `↓` | History (persisted across sessions at `~/.tiramisu/.repl_history`) |
| `Tab` | Complete a built-in or a phrase starter |
| `Esc` then `Enter` | Multi-line input (for long task descriptions) |
| `Ctrl+C` | Cancel current line / interrupt running subcommand — does NOT kill the REPL |
| `Ctrl+D` | Exit the REPL |
| `exit` / `quit` / `q` | Exit the REPL |
| `help` / `?` | Show the routing table |
| `clear` / `cls` | Clear the screen |
