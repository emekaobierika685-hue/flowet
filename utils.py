# utils.py  (project root — NOT helpers/utils.py)
import os
import re
import json
import time
import random
import threading
import secrets
from datetime import datetime

import yaml


# =========================================================================
# Shared state
# =========================================================================
lock = threading.Lock()
thread_restart_enabled = True


# =========================================================================
# Counters
# =========================================================================
COUNTERS = {"generated": 0, "failed": 0, "retries": 0}
_COUNTER_LOCK = threading.Lock()


def update_counter(key: str, amount: int = 1):
    with _COUNTER_LOCK:
        COUNTERS[key] = COUNTERS.get(key, 0) + amount


def get_counters():
    with _COUNTER_LOCK:
        return dict(COUNTERS)


# =========================================================================
# Colored printers
# =========================================================================
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


# =========================================================================
# Config
# =========================================================================
def load_config(path="config.yaml"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_solver_config():
    """
    Returns (SOLVER, API_KEY, TIMEOUT).
    Matches: `SOLVER, API_KEY, TIMEOUT = validate_solver_config()`
    """
    cfg = load_config()
    solver = cfg.get("solver", {}).get("name", "rosolve")
    api_key = cfg.get("solver", {}).get("api_key", "")
    timeout = int(cfg.get("solver", {}).get("timeout", 180))
    return solver, api_key, timeout


# =========================================================================
# Proxies
# =========================================================================
def load_proxies(path="input/proxies.txt"):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# =========================================================================
# Username generation — nerzaer_ + 8 alphanumeric chars
# =========================================================================
USERNAME_PREFIX = "nerzaer_"
USERNAME_SUFFIX_LEN = 8
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

# e.g. "nerzaer_9803i90i4"
_USERNAME_RE = re.compile(r"^nerzaer_[a-z0-9]{8}$")


def _make_nerzaer_username() -> str:
    """Build one `nerzaer_XXXXXXXX` username (8 lowercase alnum chars)."""
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(USERNAME_SUFFIX_LEN))
    return f"{USERNAME_PREFIX}{suffix}"


def _sanitize_username(un: str) -> str:
    """
    Enforce Roblox username rules:
      - 3..20 chars
      - alphanumeric + underscore only
      - no leading/trailing underscore
      - no consecutive underscores
    Falls back to a fresh nerzaer_ username if cleaning destroys the prefix.
    """
    if not un:
        return _make_nerzaer_username()

    # keep only a-z0-9_
    un = re.sub(r"[^a-z0-9_]", "", un.lower())
    if not un:
        return _make_nerzaer_username()

    # collapse consecutive underscores
    while "__" in un:
        un = un.replace("__", "_")

    # strip leading/trailing underscore — but this can nuke the prefix,
    # so only strip trailing here; the prefix never has a trailing _ issue.
    un = un.rstrip("_")

    # length guard
    if len(un) < 3:
        return _make_nerzaer_username()
    if len(un) > 20:
        un = un[:20].rstrip("_")

    # final guarantee: must match nerzaer_XXXXXXXX
    if not _USERNAME_RE.match(un):
        return _make_nerzaer_username()
    return un


def generate_username():
    """
    Returns (username, basic_display_name).

    Username format is ALWAYS `nerzaer_` + 8 lowercase alphanumeric chars,
    e.g. `nerzaer_9803i90i4`. The `generator.username` config key is
    honoured if it still produces a matching string; otherwise we force
    the canonical format.
    """
    cfg = load_config().get("generator", {})
    pattern = cfg.get("username", "nerzaer_[a-z0-9]{8}")

    username = None
    try:
        import exrex
        # try the configured pattern up to 20 times
        for _ in range(20):
            candidate = exrex.getone(pattern, limit=20)
            candidate = _sanitize_username(candidate)
            if _USERNAME_RE.match(candidate):
                username = candidate
                break
    except Exception:
        pass

    if username is None:
        # exrex missing, bad pattern, or all 20 candidates were invalid
        username = _make_nerzaer_username()

    # display name: underscores → spaces (Roblox allows spaces in display names)
    display = username.replace("_", " ").strip() or username
    return username, display


# =========================================================================
# Birthday (Roblox requires age >= 13)
# =========================================================================
def generate_birthday():
    year = random.randint(1990, 2008)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


# =========================================================================
# Password
# =========================================================================
def get_password():
    cfg = load_config().get("generator", {}).get("password")
    if cfg:
        try:
            import exrex
            for _ in range(5):
                pw = exrex.getone(cfg, limit=50)
                if pw and len(pw) >= 8:
                    return pw
        except Exception:
            pass

    # strong default: 20 chars, mixed alphabet + symbols
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%"
    while True:
        pw = "".join(random.choice(alphabet) for _ in range(20))
        # Roblox: must contain at least one letter and one digit/symbol
        if (any(c.isalpha() for c in pw)
                and any(c.isdigit() or c in "!@#$%" for c in pw)):
            return pw


# =========================================================================
# Shutdown helper
# =========================================================================
def wait_for_threads_and_exit(reason=""):
    if reason:
        tprint(reason)
    time.sleep(2)
    os._exit(0)
