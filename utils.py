# utils.py  (project root)
import os
import re
import json
import time
import random
import threading
import secrets
from datetime import datetime

import yaml

# ---- shared state ----
lock = threading.Lock()
thread_restart_enabled = True

# ---- counters ----
COUNTERS = {"generated": 0, "failed": 0, "retries": 0}
_COUNTER_LOCK = threading.Lock()

def update_counter(key: str, amount: int = 1):
    with _COUNTER_LOCK:
        COUNTERS[key] = COUNTERS.get(key, 0) + amount

def get_counters():
    with _COUNTER_LOCK:
        return dict(COUNTERS)

# ---- colored printers ----
class C:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"

def _ts():
    return time.strftime("%H:%M:%S")

def tprint(msg):  print(f"{C.CYAN}[{_ts()}] {msg}{C.RESET}")
def fprint(msg):  print(f"{C.RED}[{_ts()}] {msg}{C.RESET}")
def sprint(msg):  print(f"{C.GREEN}[{_ts()}] {msg}{C.RESET}")
def wprint(msg):  print(f"{C.YELLOW}[{_ts()}] {msg}{C.RESET}")
def caprint(msg): print(f"{C.MAGENTA}[{_ts()}] {msg}{C.RESET}")
def crprint(msg): print(f"{C.GREEN}[{_ts()}] {msg}{C.RESET}")

# ---- config ----
def load_config(path="config.yaml"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def validate_solver_config():
    """
    Returns (SOLVER, API_KEY, TIMEOUT).
    Matches the call `SOLVER, API_KEY, TIMEOUT = validate_solver_config()`.
    Fraudster runs in-process, so API_KEY is unused; we still return it.
    """
    cfg = load_config()
    solver = cfg.get("solver", {}).get("name", "rosolve")
    api_key = cfg.get("solver", {}).get("api_key", "")
    timeout = int(cfg.get("solver", {}).get("timeout", 180))
    return solver, api_key, timeout

# ---- proxies ----
def load_proxies(path="input/proxies.txt"):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

# ---- generators ----
_USERNAME_RE = re.compile(r"[a-zA-Z0-9_]+")

def generate_username():
    """
    Returns (username, basic_display_name).
    `basic_display_name` is derived from username by replacing '_'.
    """
    cfg = load_config().get("generator", {})
    pattern = cfg.get("username", "burger_[a-zA-Z0-9]{5}")
    try:
        import exrex
        username = exrex.getone(pattern, limit=20)
    except Exception:
        username = f"user_{secrets.token_hex(4)}"

    # Roblox rules: 3-20 chars, alphanumeric + _, no leading/trailing _,
    # no consecutive _
    username = username.strip("_")
    while "__" in username:
        username = username.replace("__", "_")
    if len(username) < 3:
        username = f"user_{secrets.token_hex(3)}"
    if len(username) > 20:
        username = username[:20].rstrip("_")

    display = username.replace("_", " ").strip() or username
    return username, display

def generate_birthday():
    # ISO 8601: YYYY-MM-DD, must be 13+ years old
    year = random.randint(1990, 2008)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"

def get_password():
    cfg = load_config().get("generator", {}).get("password")
    if cfg:
        try:
            import exrex
            return exrex.getone(cfg, limit=50)
        except Exception:
            pass
    # Default: strong random password
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%"
    return "".join(random.choice(alphabet) for _ in range(20))

# ---- shutdown helper ----
def wait_for_threads_and_exit(reason=""):
    if reason:
        tprint(reason)
    time.sleep(2)
    os._exit(0)
