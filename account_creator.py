## Roblox account generator /w gametype-1 funcaptcha solver
## twitter.com/h0nde
## 2020-09-07

from funcaptcha import FunCaptchaSession, Proxy, BadGameTypeOrVariant, BannedProxy
from fingerprint import Fingerprint, Window
from threading import Thread, Lock, Event
from queue import Queue, Full as QueueFull, Empty as QueueEmpty
from counter import Counter, IntervalCounter
import itertools
import random
import imageutil
import pickle
import time
import os
import secrets
import exrex
import requests
import ctypes
import yaml
import uuid
import sys
import traceback
import signal
import json

from flask import Flask, request, jsonify, Response, redirect

if not os.path.exists("solver-db"): os.mkdir("solver-db")
if not os.path.exists("logs"): os.mkdir("logs")

## Load config
with open("config.yaml") as f:
    config = yaml.safe_load(f)
    hash_length = config["solver"]["hash_length"]
    hash_method = imageutil.methods[config["solver"]["hash_method"]]
    masking = config["solver"]["mask"]
    solver_count = config["threads"]["solver"]
    resubmitter_count = config["threads"]["resubmitter"]
    thread_count = config["threads"]["main"]
    user_agent = config["misc"]["user_agent"]

    un_gen = lambda: exrex.getone(config["generator"]["username"], limit=20)
    pw_gen = lambda: exrex.getone(config["generator"]["password"], limit=50)
    
bheaders = {
    "User-Agent": user_agent,
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/account/signupredir"
}

## Counters
created_counter = IntervalCounter()
solver_success_counter = Counter()
solver_failure_counter = Counter()

## Solver variables
solved_queue = Queue()
resubmit_queue = Queue()
appear = {}
cache = {}

# =========================================================================
# LIVE CONSOLE LOGGING SYSTEM (SSE)
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

class Proxy(Proxy):
    def __init__(self, proxy):
        self.xsrf_token = None
        super().__init__(proxy, "http")

if os.path.exists("proxies.txt"):
    with open("proxies.txt") as f:
        proxies = list(map(Proxy, f.read().splitlines()))
elif os.environ.get("PROXIES"):
    proxies = list(map(Proxy, os.environ.get("PROXIES").split(",")))
else:
    proxies = []
proxies = itertools.cycle(proxies) if proxies else None

def get_identity():
    window = Window("Roblox", "https://www.roblox.com/account/signupredir")
    fp = Fingerprint(
        user_agent=user_agent, protochain_hash="5d76839801bc5904a4f12f1731a7b6d1",
        sec_fetch=True, content_type_value="application/x-www-form-urlencoded; charset=UTF-8",
        accept_language_value="en-US,en;q=0.9",
        jsbd_gen=lambda w: dict(HL=random.randint(1,5), NCE=True, DT=w.title, NWD="undefined", DA=None, DR=None, DMT=random.randint(1,40), DO=None, DOT=random.randint(30,50)),
        DNT="unknown", L="en-US", D=24, PR=1, S="1920,1080", AS="1920,1040", SS=True, LS=True, IDB=True, B=False, ODB=True, CPUC="unknown", PK="Win32",
        JSF="Arial,Arial Black,Arial Narrow,Book Antiqua,Bookman Old Style,Calibri,Cambria,Cambria Math,Century,Century Gothic,Century Schoolbook,Comic Sans MS,Consolas,Courier,Courier New,Garamond,Georgia,Helvetica,Impact,Lucida Bright,Lucida Calligraphy,Lucida Console,Lucida Fax,Lucida Handwriting,Lucida Sans,Lucida Sans Typewriter,Lucida Sans Unicode,Microsoft Sans Serif,Monotype Corsiva,MS Gothic,MS PGothic,MS Reference Sans Serif,MS Sans Serif,MS Serif,Palatino Linotype,Segoe Print,Segoe Script,Segoe UI,Segoe UI Light,Segoe UI Semibold,Segoe UI Symbol,Tahoma,Times,Times New Roman,Trebuchet MS,Verdana,Wingdings,Wingdings 2,Wingdings 3",
        P="Chrome PDF Plugin,Chrome PDF Viewer,Native Client", T="0,false,false", H="8", SWF=False
    )
    return fp, window

def get_session():
    fp, window = get_identity()
    fcs = FunCaptchaSession(
        public_key="A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F", service_url="https://roblox-api.arkoselabs.com",
        window=window, fingerprint=fp, analytics=True, whitelisted_types=[1], whitelisted_variants=None,
        proxy=next(proxies) if proxies else None
    )
    return fcs

def prepare_image(im, label=None):
    im = imageutil.remove_background(im)
    im = im.crop(im.getbbox())
    if masking: im = imageutil.mask(im)
    return im

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

def create_account(ch):
    username = gen_username()
    password = gen_password()
    log_to_console("STARTED", f"Account generation started: {username}")
    
    def send():
        return requests.post(
            url="https://auth.roblox.com/v2/signup",
            headers={**bheaders, "X-CSRF-TOKEN": ch.proxy.xsrf_token or "-"},
            proxies={"https": "%s:%d" % (ch.proxy.host, ch.proxy.port)} if ch.proxy else None,
            timeout=15,
            json=dict(username=username, password=password, gender="Male", birthday="2000-01-01", isTosAgreementBoxChecked=True, captchaToken=ch.full_token, captchaProvider="PROVIDER_ARKOSE_LABS")
        )
    try:
        r = send()
        if "x-csrf-token" in r.headers:
            ch.proxy.xsrf_token = r.headers["x-csrf-token"]
            r = send()
        raise_for_error(r)
        cookie = r.cookies[".ROBLOSECURITY"]
        return CreatedAccount(r.json()["userId"], username, password, cookie)
    except Exception as e:
        log_to_console("FAILED", f"Roblox rejected account: {e}")
        raise e

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

def solve_one_captcha(timeout=15):
    deadline = time.time() + timeout
    session = get_session()
    while time.time() < deadline:
        try:
            print("[*] Fetching Captcha challenge...")
            fc = session.get_challenge()
            print("[+] Challenge fetched! Images:", len(fc.image_urls))
            for imdata in fc.images:
                im = imageutil.to_pil(imdata)
                mim = prepare_image(im, label="main")
                mh = imageutil.hash_image(mim, hash_method, hash_length)
                appear[mh] = 1
                rd = 0.0
                max_steps = int(360 / fc.rotate_degree) if fc.rotate_degree else 0
                for rn in range(1, max_steps):
                    rd = fc.rotate_degree * rn
                    rk = "%s|%.2f" % (mh, rd)
                    rh = cache.get(rk)
                    if not rh:
                        rim = prepare_image(im.rotate(rd * -1), label="rotate")
                        rh = imageutil.hash_image(rim, hash_method, hash_length)
                        cache[rk] = rh
                    if rh not in appear: break
                solved = bool(fc.check_answer(rd))
                if solved:
                    log_to_console("CAPTCHA", f"Captcha solved")
                    return fc
                break
        except Exception as e:
            print("[-] Solver Error:", e)
            session.close_conns()
            session = get_session()
    raise TimeoutError("Solver timed out (proxies might be dead)")

# -------------------------------------------------------------------------
# API ROUTES
# -------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect("/api/console")

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
            ch = solve_one_captcha()
            acct = create_account(ch)
            
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
    app.run(host="0.0.0.0", port=API_PORT, threaded=True)