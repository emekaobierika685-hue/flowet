## Roblox account generator /w gametype-1 funcaptcha solver
## twitter.com/h0nde
## 2020-09-07
## === FRAUDSTER INTEGRATION (2025) ===

import os
import sys
import json
import time
import itertools
import random
import secrets
import traceback
import ctypes
from threading import Thread, Lock, Event
from queue import Queue, Full as QueueFull, Empty as QueueEmpty

import requests
import yaml
import exrex
from flask import Flask, request, jsonify, Response, redirect

# === FRAUDSTER INTEGRATION: import the new solver + helpers ===
# Add the project root to sys.path so `helpers` and `tls_client` resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers
import helpers.utils
import helpers.models
import helpers.presets
import helpers.exceptions
from app import Funcaptcha as FraudsterSolver   # re-use the solver class from your app.py

# The old modules are GONE. If you still want the local imagehash path for
# gameType 1, re-enable `imageutil.py` — but it will not match current Arkose
# images, so we drop it.
# from funcaptcha import ...      # REMOVED
# from imageutil import ...       # REMOVED
# from fingerprint import ...     # REMOVED (Fraudster has its own models.UserBrowser)

if not os.path.exists("solver-db"): os.mkdir("solver-db")
if not os.path.exists("logs"): os.mkdir("logs")

# --- Config ---
with open("config.yaml") as f:
    config = yaml.safe_load(f)
    user_agent = config["misc"]["user_agent"]
    un_gen = lambda: exrex.getone(config["generator"]["username"], limit=20)
    pw_gen = lambda: exrex.getone(config["generator"]["password"], limit=50)

bheaders = {
    "User-Agent": user_agent,
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/account/signupredir"
}

# === FRAUDSTER INTEGRATION: counters (reuse the existing ones) ===
from counter import Counter, IntervalCounter
created_counter = IntervalCounter()
solver_success_counter = Counter()
solver_failure_counter = Counter()

# === FRAUDSTER INTEGRATION: solver singleton ===
# We create ONE Funcaptcha instance and share it across threads.
# Fraudster is already thread-safe (SessionPool + Lock).
_SOLVER: "FraudsterSolver | None" = None
_SOLVER_LOCK = Lock()

def get_solver() -> FraudsterSolver:
    global _SOLVER
    with _SOLVER_LOCK:
        if _SOLVER is None:
            _SOLVER = FraudsterSolver(max_workers=100, session_pool_size=50)
        return _SOLVER

# === FRAUDSTER INTEGRATION: preset for Roblox ===
ROBLOX_PRESET_KEY = "A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F"

# === FRAUDSTER INTEGRATION: proxy loading ===
# Fraudster's SessionPool pulls proxies from `data/proxies.txt` internally,
# but per-task proxies (og_proxy) take precedence, so we read the same file
# to pick one per Roblox signup.
if os.path.exists("proxies.txt"):
    with open("proxies.txt") as f:
        _RAW_PROXIES = [p.strip() for p in f.read().splitlines() if p.strip()]
elif os.environ.get("PROXIES"):
    _RAW_PROXIES = [p.strip() for p in os.environ["PROXIES"].split(",") if p.strip()]
else:
    _RAW_PROXIES = []

proxy_cycle = itertools.cycle(_RAW_PROXIES) if _RAW_PROXIES else None


# =========================================================================
# LIVE CONSOLE LOGGING SYSTEM (SSE)  — unchanged
# =========================================================================
CONSOLE_LISTENERS = []
CONSOLE_LOCK = Lock()

def log_to_console(msg_type, message):
    ts = time.strftime("%H:%M:%S")
    data = json.dumps({"type": msg_type, "time": ts, "message": message})
    with CONSOLE_LOCK:
        for q in list(CONSOLE_LISTENERS):
            try: q.put_nowait(data)
            except: pass

wlock = Lock()
def account_callback(account, source="CLI"):
    print("Created account:", account)
    created_counter.add()
    log_to_console("CREATED", f"Account created: {account.username}")
    with wlock:
        with open("logs/cookies.txt", "a", encoding="UTF-8", errors="ignore") as f:
            f.write("%s\n" % account.cookie)
        with open("logs/combos_cookies.txt", "a", encoding="UTF-8", errors="ignore") as f:
            f.write("%s:%s:%s\n" % (account.username, account.password, account.safe_cookie()))

def gen_username():
    un = ""
    while not un or un.startswith("_") or un.endswith("_") or un.count("_") > 1 or len(un) < 4 or un.isdigit():
        un = un_gen()
    return un

def gen_password():
    return pw_gen()


# =========================================================================
# ROBlox PROXY WRAPPER (adapted)
# =========================================================================
# We keep a tiny wrapper because Roblox's signup needs to persist the
# .ROBLOSECURITY cookie between the CSRF retry and the final request.
class RobloxProxy:
    def __init__(self, proxy):
        self.xsrf_token = None
        self.raw = proxy          # "user:pass@host:port" or "host:port"
        # For `requests` we need the full URL
        if "@" in proxy:
            userpass, hostport = proxy.rsplit("@", 1)
            self.requests_url = f"http://{userpass}@{hostport}"
        else:
            self.requests_url = f"http://{proxy}"

def next_proxy() -> "RobloxProxy | None":
    if proxy_cycle is None:
        return None
    return RobloxProxy(next(proxy_cycle))


# =========================================================================
# === FRAUDSTER INTEGRATION: solve_one_captcha ===
# =========================================================================
def solve_one_captcha(timeout=180) -> str:
    """
    Uses the Fraudster solver to obtain a raw Arkose token for Roblox.
    Returns the raw token string (challenge_data.token.raw).
    """
    solver = get_solver()
    preset = helpers.presets.get_preset(ROBLOX_PRESET_KEY)
    if preset is None:
        raise RuntimeError("Roblox preset missing from helpers/presets.py")

    proxy = next_proxy()
    log_to_console("STARTED", f"Solving captcha via Fraudster (proxy={'yes' if proxy else 'no'})")

    # Roblox's sitekey requires `blob` — Fraudster needs it.
    # The blob is normally scraped from https://www.roblox.com/arkose/iframe.
    # If you don't have one, pass None and the solver will still try
    # (but Roblox *will* reject unblobbed tokens in most cases).
    blob = os.environ.get("ROBLOX_BLOB")  # optional override

    try:
        result = solver.solve(
            preset=preset,
            blob=blob,
            og_proxy=proxy.raw if proxy else None,
        )
    except helpers.exceptions.FuncaptchaError as e:
        solver_failure_counter.add()
        log_to_console("FAILED", f"Fraudster solve failed: {e}")
        raise CaptchaError(str(e))
    except Exception as e:
        solver_failure_counter.add()
        log_to_console("FAILED", f"Fraudster solve crashed: {e}\n{traceback.format_exc()}")
        raise CaptchaError(str(e))

    if not result.solved:
        solver_failure_counter.add()
        raise CaptchaError("Fraudster returned solved=False")

    solver_success_counter.add()
    log_to_console("CAPTCHA", f"Captcha solved | Waves: {result.waves} | Variant: {result.variant or 'suppressed'}")
    return result.token


# =========================================================================
# SignupError / CaptchaError
# =========================================================================
class SignupError(Exception): pass
class CaptchaError(Exception): pass

class CreatedAccount:
    def __init__(self, id, username, password, cookie):
        self.id = id; self.username = username; self.password = password; self.cookie = cookie
    def safe_cookie(self): return self.cookie.replace("WARNING:", "WARNING")
    def __repr__(self): return self.username

def raise_for_error(r):
    d = r.json()
    if "errors" in d:
        for err in d["errors"]:
            e = CaptchaError if err["code"] == 2 else SignupError
            raise e("%s (code %d)" % (err["message"], err["code"]))


# =========================================================================
# === FRAUDSTER INTEGRATION: create_account rewritten ===
# =========================================================================
def create_account(token: str, proxy: "RobloxProxy | None") -> CreatedAccount:
    """
    Creates a Roblox account using a pre-solved captcha token.
    If Roblox's CSRF cookie is missing, we re-fetch a *new* token and retry.
    """
    username = gen_username()
    password = gen_password()
    log_to_console("STARTED", f"Creating account: {username}")

    def send(tok):
        proxies = {"https": proxy.requests_url} if proxy else None
        return requests.post(
            url="https://auth.roblox.com/v2/signup",
            headers={**bheaders, "X-CSRF-TOKEN": (proxy.xsrf_token if proxy else None) or "-"},
            proxies=proxies,
            timeout=15,
            json=dict(
                username=username, password=password, gender="Male",
                birthday="2000-01-01", isTosAgreementBoxChecked=True,
                captchaToken=tok, captchaProvider="PROVIDER_ARKOSE_LABS"
            )
        )

    try:
        r = send(token)
        # CSRF bootstrap — Roblox returns 403 with x-csrf-token header
        if r.status_code == 403 and "x-csrf-token" in r.headers:
            if proxy:
                proxy.xsrf_token = r.headers["x-csrf-token"]
            r = send(token)

        # If captcha was invalidated by the CSRF round-trip, re-solve once.
        if r.status_code in (400, 403) and r.json().get("errors", [{}])[0].get("code") == 2:
            log_to_console("CAPTCHA", "Captcha invalidated by CSRF retry; re-solving...")
            token = solve_one_captcha()     # fresh token
            r = send(token)

        raise_for_error(r)
        cookie = r.cookies[".ROBLOSECURITY"]
        return CreatedAccount(r.json()["userId"], username, password, cookie)

    except Exception as e:
        log_to_console("FAILED", f"Roblox rejected account: {e}")
        raise


# =========================================================================
# API & FOLLOW LOGIC
# =========================================================================
API_KEY = os.environ.get("API_KEY")
CREATE_WORKERS = int(os.environ.get("CREATE_WORKERS", "4"))
API_PORT = int(os.environ.get("PORT", "8080"))

app = Flask(__name__)

def require_api_key(fn):
    def wrapper(*a, **kw):
        if API_KEY:
            supplied = request.headers.get("X-API-Key") or request.args.get("api_key")
            if supplied != API_KEY: return jsonify(error="unauthorized"), 401
        return fn(*a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper

def resolve_username_to_id(username, timeout=10):
    r = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": True}, timeout=timeout)
    data = r.json().get("data") or []
    if not data: raise LookupError("unknown username %r" % username)
    return int(data[0]["id"])

def follow_user(account, target_id, timeout=15):
    s = requests.Session()
    s.cookies[".ROBLOSECURITY"] = account.cookie
    headers = {"User-Agent": user_agent, "Origin": "https://www.roblox.com", "Referer": "https://www.roblox.com/", "Content-Type": "application/json"}
    r = s.post("https://auth.roblox.com/v1/logout", headers=headers, timeout=timeout)
    xsrf = r.headers.get("x-csrf-token")
    if not xsrf: raise RuntimeError("Could not obtain CSRF token (Are the proxies dead?)")
    headers["X-CSRF-TOKEN"] = xsrf
    r = s.post(f"https://friends.roblox.com/v1/users/{target_id}/follow", headers=headers, timeout=timeout)
    if r.status_code >= 400: raise RuntimeError(f"Roblox follow failed: {r.status_code} {r.text[:100]}")
    return True


# =========================================================================
# API ROUTES
# =========================================================================

@app.route("/")
def index():
    return redirect("/api/console")

@app.route("/api/health")
def api_health():
    # railway.json healthcheck path
    return jsonify(ok=True)

@app.route("/api/stats")
def api_stats():
    return jsonify(
        created=created_counter.total,
        solve_ok=solver_success_counter.total,
        solve_fail=solver_failure_counter.total
    )

@app.route("/api/create", methods=["POST"])
@require_api_key
def api_create():
    body = request.get_json(silent=True) or {}
    target = body.get("target") or body.get("follow")
    count = int(body.get("followers", 1))

    if not target:
        return jsonify(error="Missing 'target' or 'follow' ID/Username"), 400

    try:
        target_id = int(target)
    except (TypeError, ValueError):
        try: target_id = resolve_username_to_id(str(target))
        except Exception as e: return jsonify(error=f"Target lookup failed: {e}"), 404

    results = []

    for i in range(count):
        try:
            log_to_console("STARTED", f"Starting job {i+1}/{count} for target {target_id}")

            # === FRAUDSTER INTEGRATION: solve captcha first ===
            proxy = next_proxy()
            token = solve_one_captcha()

            # === FRAUDSTER INTEGRATION: signup ===
            acct = create_account(token, proxy)

            follow_status = "success"
            try:
                follow_user(acct, target_id)
                log_to_console("FOLLOWED", f"Account {acct.username} followed user {target_id}")
            except Exception as e:
                follow_status = f"failed: {e}"
                log_to_console("FAILED", f"Follow failed for {acct.username}: {e}")

            results.append({
                "username": acct.username,
                "password": acct.password,
                "cookie": acct.safe_cookie(),
                "follow_status": follow_status
            })

        except Exception as e:
            results.append({"error": str(e)})

    return jsonify(success=True, target_id=target_id, results=results)


@app.route("/api/console")
def console_page():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Account Generator Console</title>
        <style>
            body { background-color: #1e1e1e; color: #d4d4d4; font-family: 'Consolas', 'Courier New', monospace; margin: 0; padding: 20px; }
            .header { display: flex; justify-content: space-between; border-bottom: 1px solid #333; padding-bottom: 10px; margin-bottom: 10px; color: #fff; }
            .title { color: #4ec9b0; font-weight: bold; }
            .stats { font-size: 0.9em; color: #9cdcfe; }
            #console { height: calc(100vh - 80px); overflow-y: auto; font-size: 14px; line-height: 1.5; }
            .log-line { white-space: pre-wrap; word-wrap: break-word; }
            .time { color: #569cd6; }
            .CREATED { color: #4ec9b0; font-weight: bold; }
            .STARTED { color: #dcdcaa; }
            .CAPTCHA { color: #ce9178; }
            .FAILED  { color: #f48771; font-weight: bold; }
            .FOLLOWED { color: #c586c0; }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">Roblox Account Generator // Live Console</div>
            <div class="stats" id="stats">Connecting...</div>
        </div>
        <div id="console"></div>
        <script>
            const consoleDiv = document.getElementById('console');
            const statsDiv = document.getElementById('stats');
            const evtSource = new EventSource("/api/console/stream");

            evtSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                if(data.type === "PING") return;
                const div = document.createElement('div');
                div.className = 'log-line';
                div.innerHTML = `<span class="time">[${data.time}]</span> <span class="${data.type}">[${data.type}]</span> ${data.message}`;
                consoleDiv.appendChild(div);
                consoleDiv.scrollTop = consoleDiv.scrollHeight;
            };

            setInterval(() => {
                fetch('/api/stats').then(r => r.json()).then(d => {
                    statsDiv.innerHTML = `Created: ${d.created} | Captcha Solved: ${d.solve_ok} | Failed: ${d.solve_fail}`;
                }).catch(e => {});
            }, 2000);
        </script>
    </body>
    </html>
    """
    return Response(html, mimetype="text/html")

@app.route("/api/console/stream")
def console_stream():
    def event_stream(q):
        yield "data: " + json.dumps({"type": "PING"}) + "\n\n"
        while True:
            try: yield f"data: {q.get(timeout=20)}\n\n"
            except QueueEmpty: yield "data: " + json.dumps({"type": "PING"}) + "\n\n"

    q = Queue(maxsize=100)
    with CONSOLE_LOCK: CONSOLE_LISTENERS.append(q)
    def on_close():
        with CONSOLE_LOCK: CONSOLE_LISTENERS.remove(q)

    resp = Response(event_stream(q), mimetype="text/event-stream")
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.call_on_close(on_close)
    return resp

if __name__ == "__main__":
    # === FRAUDSTER INTEGRATION: bootstrap solver once at startup ===
    try:
        get_solver()
        log_to_console("STARTED", "Fraudster solver initialized")
    except Exception as e:
        print(f"[FATAL] Could not init Fraudster solver: {e}")

    app.run(host="0.0.0.0", port=API_PORT, threaded=True)
