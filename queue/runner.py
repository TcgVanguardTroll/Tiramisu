#!/usr/bin/env python3
"""
KiroAgentQueue Runner â€” replaces crontab for Tiramisu scheduled tasks.

Features over raw crontab:
- Retry with backoff on failure
- Overlap prevention (skip if previous run still active)
- Timeout enforcement (macOS-compatible, no `timeout` binary needed)
- Failure notifications via Slack DM
- Structured JSON state for observability
- Single process, no external dependencies beyond Python 3.9+

Usage:
  python3 queue/runner.py          # foreground (for testing)
  python3 queue/runner.py &        # background daemon
  launchctl load queue/com.tiramisu.queue.plist  # macOS service (preferred)
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread, Lock

QUEUE_DIR = Path(__file__).resolve().parent
QUEUE_FILE = QUEUE_DIR / "queue.json"
STATE_FILE = QUEUE_DIR / "state.json"
LOCK_DIR = QUEUE_DIR / "locks"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file

_state_lock = Lock()


def load_queue():
    with open(QUEUE_FILE) as f:
        return json.load(f)


def rotate_log(log_file: Path):
    """Rotate log file if it exceeds LOG_MAX_BYTES. Keeps one .1 backup."""
    if not log_file.exists():
        return
    try:
        if log_file.stat().st_size > LOG_MAX_BYTES:
            backup = log_file.with_suffix(log_file.suffix + ".1")
            if backup.exists():
                backup.unlink()
            log_file.rename(backup)
    except OSError:
        pass


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"runs": {}, "failures": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def now_utc():
    return datetime.now(timezone.utc)


def cron_matches(cron_expr, dt):
    """Minimal cron matcher for minute-level scheduling."""
    parts = cron_expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts

    def match_field(field, value, max_val):
        if field == "*":
            return True
        for part in field.split(","):
            if "/" in part:
                base, step = part.split("/")
                start = 0 if base == "*" else int(base)
                if (value - start) % int(step) == 0 and value >= start:
                    return True
            elif "-" in part:
                lo, hi = part.split("-")
                if int(lo) <= value <= int(hi):
                    return True
            else:
                if int(part) == value:
                    return True
        return False

    return (
        match_field(minute, dt.minute, 59)
        and match_field(hour, dt.hour, 23)
        and match_field(dom, dt.day, 31)
        and match_field(month, dt.month, 12)
        and match_field(dow, dt.isoweekday() % 7, 6)
    )


def is_locked(task_id):
    lock_file = LOCK_DIR / f"{task_id}.lock"
    if not lock_file.exists():
        return False
    try:
        pid = int(lock_file.read_text().strip())
        os.kill(pid, 0)  # Check if process is alive
        return True
    except (ValueError, ProcessLookupError, OSError):
        lock_file.unlink(missing_ok=True)  # Stale lock â€” remove it
        return False


def acquire_lock(task_id):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_DIR / f"{task_id}.lock"
    lock_file.write_text(str(os.getpid()))


def release_lock(task_id):
    lock_file = LOCK_DIR / f"{task_id}.lock"
    lock_file.unlink(missing_ok=True)


_last_notified = {}  # {task_id: timestamp}
_NOTIFY_COOLDOWN = 1800  # 30 minutes between notifications for same task


def notify_failure(task, error_msg, settings):
    """Send Slack DM on failure via MeshClaw bot. Deduplicates within 30 min."""
    task_id = task["id"]
    now = time.time()
    if task_id in _last_notified and (now - _last_notified[task_id]) < _NOTIFY_COOLDOWN:
        return  # suppress â€” already notified recently
    _last_notified[task_id] = now

    import urllib.request
    env_file = Path.home() / ".meshclaw" / ".env"
    if not env_file.exists():
        return
    env = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = env.get("SLACK_BOT_TOKEN")
    owner_id = env.get("MESHCLAW_OWNER_ID")
    if not token or not owner_id:
        return
    try:
        # Open DM
        req = urllib.request.Request(
            "https://slack.com/api/conversations.open",
            data=json.dumps({"users": owner_id}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        channel = resp.get("channel", {}).get("id")
        if not channel:
            return
        # Send
        msg = f"\u26a0\ufe0f *KiroAgentQueue* task `{task['id']}` failed:\n```\n{error_msg[:500]}\n```"
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=json.dumps({"channel": channel, "text": msg}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def run_task(task, settings, state):
    """Execute a single task with timeout and retry."""
    task_id = task["id"]
    work_dir = settings.get("work_dir", "${TIRAMISU_HOME:-$HOME/.tiramisu}")
    log_dir = Path(settings.get("log_dir", "${TIRAMISU_HOME:-$HOME/.tiramisu}/logs"))
    log_file = log_dir / f"{task_id}.log"
    timeout = task.get("timeout_sec", 300)
    retry_cfg = task.get("retry", {"max_attempts": 1, "backoff_sec": 0})
    max_attempts = retry_cfg.get("max_attempts", 1)
    backoff = retry_cfg.get("backoff_sec", 0)

    if is_locked(task_id):
        return  # skip â€” previous run still active

    acquire_lock(task_id)
    attempt = 0
    success = False
    last_error = ""

    try:
        rotate_log(log_file)
        while attempt < max_attempts and not success:
            attempt += 1
            ts = now_utc().isoformat()
            with open(log_file, "a") as lf:
                lf.write(f"\n[{ts}] Attempt {attempt}/{max_attempts}\n")
                try:
                    result = subprocess.run(
                        ["bash", "-c", task["command"]],
                        cwd=work_dir,
                        timeout=timeout,
                        capture_output=True,
                        text=True,
                    )
                    lf.write(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
                    if result.stderr:
                        lf.write(f"\nSTDERR: {result.stderr[-1000:]}\n")
                    if result.returncode == 0:
                        success = True
                    else:
                        last_error = f"exit code {result.returncode}: {result.stderr[-200:]}"
                        lf.write(f"\n[{ts}] FAILED: {last_error}\n")
                except subprocess.TimeoutExpired:
                    last_error = f"timeout after {timeout}s"
                    lf.write(f"\n[{ts}] TIMEOUT after {timeout}s\n")
                except Exception as e:
                    last_error = str(e)
                    lf.write(f"\n[{ts}] ERROR: {e}\n")

            if not success and attempt < max_attempts and backoff > 0:
                time.sleep(backoff * attempt)

        # Update state (lock covers full read-modify-write)
        with _state_lock:
            current_state = load_state()
            current_state["runs"][task_id] = {
                "last_run": now_utc().isoformat(),
                "success": success,
                "attempts": attempt,
            }
            if not success:
                current_state["failures"][task_id] = current_state["failures"].get(task_id, 0) + 1
            else:
                current_state["failures"].pop(task_id, None)
            save_state(current_state)

        if not success and task.get("notify_on_failure", False):
            notify_failure(task, last_error, settings)

    except Exception as e:
        # Log thread-level exceptions so they don't vanish silently
        try:
            with open(log_file, "a") as lf:
                lf.write(f"\n[{now_utc().isoformat()}] THREAD ERROR: {e}\n")
        except Exception:
            pass
    finally:
        release_lock(task_id)


def main_loop():
    """Main scheduler loop â€” checks every 30 seconds, fires tasks on the minute."""
    print(f"[{now_utc().isoformat()}] KiroAgentQueue started. PID={os.getpid()}")
    queue = load_queue()
    settings = queue["settings"]
    state = load_state()
    last_fired_minute = -1

    while True:
        dt = now_utc()
        current_minute = dt.hour * 60 + dt.minute

        if current_minute != last_fired_minute:
            last_fired_minute = current_minute
            active_threads = []
            max_conc = settings.get("concurrency", 2)

            for task in queue["tasks"]:
                if cron_matches(task["schedule"], dt):
                    # Wait for a slot before starting
                    while len([t for t in active_threads if t.is_alive()]) >= max_conc:
                        time.sleep(1)
                    t = Thread(target=run_task, args=(task, settings, state), daemon=True)
                    t.start()
                    active_threads.append(t)

        time.sleep(15)


if __name__ == "__main__":
    # Handle graceful shutdown
    def _shutdown(sig, frame):
        print(f"\n[{now_utc().isoformat()}] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Rotate the runner's own stderr log (written by launchd)
    err_log = Path(__file__).resolve().parent.parent / "logs" / "queue_runner_err.log"
    rotate_log(err_log)

    main_loop()
