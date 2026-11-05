# account_creator.py
import base64
import html
import json
import os
import random
import threading
import time

from curl_cffi import requests

from headers import headers1, headers2, headers4, headers5, headers6
from utils import (
    load_proxies, generate_username, generate_birthday, get_password,
    tprint, fprint, sprint, wprint, caprint, crprint,
    update_counter, lock, load_config, validate_solver_config,
    wait_for_threads_and_exit,
)
import utils

from solvers.rosolve import rs_get_token
from secure_auth import get_sa
from tempmail import get_email, get_inbox, get_tempmail_public_apiKey

from flask import Flask, request, jsonify, Response, redirect
from queue import Queue, Empty as QueueEmpty


# =========================================================================
# Config
# =========================================================================
SOLVER, API_KEY, TIMEOUT = validate_solver_config()
SOLVER_FUNCTIONS = {"rosolve": rs_get_token}

MAX_CONSECUTIVE_CAPTCHA_FAILS = 30
MAX_CONSECUTIVE_CUSTOM_PASSWORD_FAILS = 3
MAX_CONSECUTIVE_INSUFFICIENT_BALANCE = 3

consecutive_captcha_fails = 0
consecutive_custom_password_fails = 0
consecutive_insufficient_balance = 0
captcha_fail_lock = threading.Lock()
custom_password_fail_lock = threading.Lock()
insufficient_balance_lock = threading.Lock()

EV_ENABLED_RUNTIME = False
DISPLAY_NAME_CFG_RUNTIME = {}


def set_runtime_options(ev_enabled, display_cfg):
    global EV_ENABLED_RUNTIME, DISPLAY_NAME_CFG_RUNTIME
    EV_ENABLED_RUNTIME = bool(ev_enabled)
    DISPLAY_NAME_CFG_RUNTIME = display_cfg if isinstance(display_cfg, dict) else {}


# =========================================================================
# SSE console
# =========================================================================
CONSOLE_LISTENERS = []
CONSOLE_LOCK = threading.Lock()


def log_to_console(msg_type, message):
    ts = time.strftime("%H:%M:%S")
    data = json.dumps({"type": msg_type, "time": ts, "message": message})
    with CONSOLE_LOCK:
        for q in list(CONSOLE_LISTENERS):
            try:
                q.put_nowait(data)
            except Exception:
                pass


# =========================================================================
# Worker — one account attempt per loop iteration
# =========================================================================
def thread_worker():
    global consecutive_captcha_fails, consecutive_custom_password_fails, consecutive_insufficient_balance

    while utils.thread_restart_enabled:
        proxy = None
        session = None
        try:
            # ---- DEBUG: Check proxy file ----
            proxy_path = "input/proxies.txt"
            if not os.path.exists(proxy_path):
                tprint(f"❌ DEBUG: File not found at {os.path.abspath(proxy_path)}")
                tprint(f"❌ DEBUG: Current directory is {os.getcwd()}")
                tprint(f"❌ DEBUG: Files in root: {os.listdir('.')}")
            else:
                with open(proxy_path, "r") as f:
                    lines = f.read().splitlines()
                tprint(f"✅ DEBUG: Found {len(lines)} lines in {proxy_path}")
            # --------------------------------

            proxy_lines = load_proxies(proxy_path)
            if not proxy_lines:
                tprint("No proxies available. Stopping all threads...")
                utils.thread_restart_enabled = False
                threading.Thread(
                    target=lambda: wait_for_threads_and_exit(
                        "No proxies available. All threads stopped."
                    )
                ).start()
                continue

            proxy = random.choice(proxy_lines)
            proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}

            session = requests.Session(impersonate="chrome", proxies=proxies)

            # ---- csrf bootstrap ----
            response = session.get(
                url="https://www.roblox.com/", headers=headers1(), timeout=15
            )
            if response.status_code == 429:
                fprint(f"API rate limit hit! {response.url}")
                update_counter("failed")
                time.sleep(10)
                continue
            if "data-token=" not in response.text:
                fprint("Could not scrape CSRF token from roblox.com")
                update_counter("failed")
                continue
            raw_token = response.text.split('data-token="')[1].split('"')[0]
            csrf_token = html.unescape(raw_token)

            username, basic_display_name = generate_username()
            birthday = generate_birthday()
            password = get_password()

            # ---- username validation ----
            payload = {"username": username, "context": "Signup", "birthday": birthday}
            response = session.post(
                url="https://auth.roblox.com/v1/usernames/validate",
                headers=headers2(csrf_token), json=payload, timeout=15,
            )
            if response.status_code != 200:
                if response.status_code == 403:
                    fprint("Token Validation Failed - likely caused by rotating proxy")
                    update_counter("failed")
                    continue
                elif response.status_code == 429:
                    fprint(f"API rate limit hit! {response.url}")
                    update_counter("failed")
                    time.sleep(10)
                    continue
                else:
                    fprint(f"Request failed with status code {response.status_code}: {response.text}")
                    update_counter("failed")
                    continue

            data = response.json()
            while data.get("message") != "Username is valid":
                if data.get("message") == "Token Validation Failed":
                    fprint("Token Validation Failed - likely caused by rotating proxy")
                else:
                    username, basic_display_name = generate_username()
                payload = {"username": username, "context": "Signup", "birthday": birthday}
                response = session.post(
                    url="https://auth.roblox.com/v1/usernames/validate",
                    headers=headers2(csrf_token), json=payload, timeout=15,
                )
                if response.status_code == 403:
                    csrf_token = response.headers.get("x-csrf-token")
                elif response.status_code == 429:
                    fprint(f"API rate limit hit! {response.url}")
                    time.sleep(10)
                    continue
                elif response.status_code != 200:
                    fprint(f"Request failed with status code {response.status_code}: {response.url}")
                    continue
                data = response.json()

            if not utils.thread_restart_enabled:
                continue
            sprint(f"Account generation started: {username}")

            # ---- password validation ----
            payload = {"username": username, "password": password}
            response = session.post(
                url="https://auth.roblox.com/v2/passwords/validate",
                headers=headers2(csrf_token), json=payload, timeout=15,
            )
            data = response.json()
            if data.get("message") != "Password is valid":
                config = load_config()
                if config.get("account_settings", {}).get("custom_password", {}).get("enabled", False):
                    with custom_password_fail_lock:
                        if consecutive_custom_password_fails >= MAX_CONSECUTIVE_CUSTOM_PASSWORD_FAILS:
                            continue
                        consecutive_custom_password_fails += 1
                        fprint(
                            f"Custom password validation failed! "
                            f"({consecutive_custom_password_fails}/{MAX_CONSECUTIVE_CUSTOM_PASSWORD_FAILS})"
                        )
                        update_counter("failed")
                        if consecutive_custom_password_fails == MAX_CONSECUTIVE_CUSTOM_PASSWORD_FAILS:
                            tprint("Too many consecutive custom password failures. Stopping all threads...")
                            utils.thread_restart_enabled = False
                            threading.Thread(
                                target=lambda: wait_for_threads_and_exit(
                                    "Too many consecutive custom password failures. All threads stopped."
                                )
                            ).start()
                    continue
                else:
                    fprint(f"Request failed with status code {response.status_code}: {response.url}")
                    update_counter("failed")
                    continue

            # ---- secure auth intent ----
            client_public_key, client_epoch_timestamp, server_nonce, sai_signature = get_sa(session)
            if not client_public_key:
                fprint("Unable to fetch server nonce.")
                update_counter("failed")
                continue

            signup_payload = {
                "username": username,
                "password": password,
                "birthday": birthday,
                "gender": 2,
                "isTosAgreementBoxChecked": True,
                "agreementIds": [
                    "306cc852-3717-4996-93e7-086daafd42f6",
                    "2ba6b930-4ba8-4085-9e8c-24b919701f15",
                ],
                "secureAuthenticationIntent": {
                    "clientPublicKey": client_public_key,
                    "clientEpochTimestamp": client_epoch_timestamp,
                    "serverNonce": server_nonce,
                    "saiSignature": sai_signature,
                },
            }

            # ---- first signup attempt → challenge required ----
            response = session.post(
                url="https://auth.roblox.com/v2/signup",
                headers=headers4(csrf_token, "auth.roblox.com"),
                json=signup_payload, timeout=15,
            )
            try:
                data = json.loads(response.text)
            except Exception:
                fprint(f"Pre-signup: non-JSON response {response.status_code}: {response.text[:200]}")
                update_counter("failed")
                continue

            errors = data.get("errors") or []
            if not errors or errors[0].get("message") != "Challenge is required to authorize the request":
                fprint(f"Pre-signup request failed: {data}")
                update_counter("failed")
                continue

            challenge_id = response.headers.get("rblx-challenge-id")
            challenge_metadata = response.headers.get("rblx-challenge-metadata")
            if not challenge_id or not challenge_metadata:
                fprint("Pre-signup missing rblx-challenge-id / rblx-challenge-metadata headers")
                update_counter("failed")
                continue

            try:
                metadata = json.loads(base64.b64decode(challenge_metadata.encode()).decode())
            except Exception as e:
                fprint(f"Failed to decode rblx-challenge-metadata: {e}")
                update_counter("failed")
                continue
            blob = metadata.get("dataExchangeBlob")
            if not blob:
                fprint("No dataExchangeBlob in challenge metadata")
                update_counter("failed")
                continue

            if not utils.thread_restart_enabled:
                continue

            # ---- Fraudster solve ----
            solver_func = SOLVER_FUNCTIONS.get(SOLVER)
            if solver_func is None:
                fprint(f"Unknown solver '{SOLVER}' (expected one of {list(SOLVER_FUNCTIONS)})")
                update_counter("failed")
                continue
            token, reason = solver_func(session, blob, proxy, API_KEY, TIMEOUT)

            if token is None:
                if "Insufficient solves" in (reason or ""):
                    with insufficient_balance_lock:
                        if consecutive_insufficient_balance >= MAX_CONSECUTIVE_INSUFFICIENT_BALANCE:
                            break
                        consecutive_insufficient_balance += 1
                        fprint(f"Failed to solve captcha: {reason}")
                        update_counter("failed")
                        if consecutive_insufficient_balance == MAX_CONSECUTIVE_INSUFFICIENT_BALANCE:
                            tprint("Captcha solver has no remaining balance. Stopping all threads...")
                            utils.thread_restart_enabled = False
                            threading.Thread(
                                target=lambda: wait_for_threads_and_exit(
                                    "Captcha solver has no remaining balance. All threads stopped."
                                )
                            ).start()
                    continue
                else:
                    with insufficient_balance_lock:
                        consecutive_insufficient_balance = 0

                with captcha_fail_lock:
                    if consecutive_captcha_fails >= MAX_CONSECUTIVE_CAPTCHA_FAILS:
                        break
                    consecutive_captcha_fails += 1
                    fprint(f"Failed to solve captcha: {reason}")
                    update_counter("failed")
                    if consecutive_captcha_fails == MAX_CONSECUTIVE_CAPTCHA_FAILS:
                        tprint(
                            f"Too many consecutive CAPTCHA failures "
                            f"({consecutive_captcha_fails}). Stopping all threads..."
                        )
                        utils.thread_restart_enabled = False
                        threading.Thread(
                            target=lambda: wait_for_threads_and_exit(
                                "Too many consecutive CAPTCHA failures. All threads stopped."
                            )
                        ).start()
                continue

            with captcha_fail_lock:
                consecutive_captcha_fails = 0
            with insufficient_balance_lock:
                consecutive_insufficient_balance = 0

            try:
                md = token.split("|")[0]
                if "pk=A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F" in token:
                    md += token.split("pk=A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F")[1].split("|cdn_url=")[0]
            except Exception:
                md = token[:48]
            caprint(f"Captcha solved: {md}")
            log_to_console("CAPTCHA", f"Solved for {username}")

            # ---- submit challenge ----
            metadata = {
                "unifiedCaptchaId": challenge_id,
                "captchaToken": token,
                "actionType": "Signup",
            }
            payload = {
                "challengeId": challenge_id,
                "challengeType": "captcha",
                "challengeMetadata": json.dumps(metadata),
            }
            response = session.post(
                url="https://apis.roblox.com/challenge/v1/continue",
                headers=headers4(csrf_token, "apis.roblox.com"),
                json=payload, timeout=15,
            )
            if response.status_code != 200:
                fprint(f"challenge/continue failed: {response.status_code} {response.url}")
                update_counter("failed")
                continue

            encrypted_metadata = base64.b64encode(
                json.dumps(metadata).encode("utf-8")
            ).decode("utf-8")

            # ---- second signup attempt with challenge headers ----
            response = session.post(
                url="https://auth.roblox.com/v2/signup",
                headers=headers5(encrypted_metadata, csrf_token, challenge_id),
                json=signup_payload, timeout=15,
            )
            if response.status_code != 200:
                fprint(f"Final signup failed: {response.status_code} {response.url} {response.text[:200]}")
                update_counter("failed")
                continue

            data = response.json()
            user_id = data.get("userId")
            cookie = response.cookies.get(".ROBLOSECURITY")
            if not user_id or not cookie:
                fprint(f"Signup succeeded but response missing userId/cookie: {data}")
                update_counter("failed")
                continue

            verified = False
            set_display_name = False
            email = None

            ev_enabled = EV_ENABLED_RUNTIME
            dn_cfg = DISPLAY_NAME_CFG_RUNTIME

            custom_display_name_enabled = False
            display_name = None
            if isinstance(dn_cfg, dict):
                custom_display_name_enabled = dn_cfg.get("enabled", False)
                mode = dn_cfg.get("mode", "custom")
                if custom_display_name_enabled:
                    if mode == "custom":
                        display_name = (dn_cfg.get("custom_name") or "").strip()
                        if not display_name:
                            wprint("Display name: custom mode selected but 'custom_name' is empty. Skipping.")
                            custom_display_name_enabled = False
                    elif mode == "from_username":
                        display_name = basic_display_name
                    else:
                        custom_display_name_enabled = False

            need_csrf = ev_enabled or custom_display_name_enabled
            csrf_ready = True
            if need_csrf:
                response = session.get(
                    url="https://www.roblox.com/home", headers=headers1(), timeout=15
                )
                if "data-token=" in response.text:
                    raw_token = response.text.split('data-token="')[1].split('"')[0]
                    csrf_token = html.unescape(raw_token)
                else:
                    wprint("Failed to fetch CSRF token for post-creation actions.")
                    csrf_ready = False

            # ---- display name ----
            if custom_display_name_enabled and csrf_ready:
                payload = {
                    "userId": user_id,
                    "newDisplayName": display_name,
                    "showAgedUpDisplayName": False,
                }
                response = session.patch(
                    url=f"https://users.roblox.com/v1/users/{user_id}/display-names",
                    headers=headers6(csrf_token, "users.roblox.com"),
                    json=payload, timeout=15,
                )
                if response.status_code == 200:
                    set_display_name = True
                else:
                    wprint(f"Display name change failed: {response.status_code} {response.text[:200]}")

            # ---- email verification ----
            if ev_enabled and csrf_ready:
                email, reason = get_email(session)
                if email:
                    payload = {"emailAddress": email, "password": ""}
                    response = session.post(
                        url="https://accountsettings.roblox.com/v1/email",
                        headers=headers6(csrf_token, "accountsettings.roblox.com"),
                        json=payload, timeout=15,
                    )
                    if response.status_code == 200:
                        ticket, reason = get_inbox(session, email)
                        if ticket:
                            payload = {"ticket": ticket}
                            response = session.post(
                                url="https://accountinformation.roblox.com/v1/email/verify",
                                headers=headers6(csrf_token, "accountinformation.roblox.com"),
                                json=payload, timeout=15,
                            )
                            if "verifiedUserHatAssetId" in response.text:
                                verified = True
                            else:
                                wprint(f"Email verify failed: {response.status_code} {response.url}")
                        else:
                            wprint(f"Failed to fetch inbox content: {reason}")
                    else:
                        wprint(f"Email set failed: {response.status_code} {response.url}")
                else:
                    wprint(f"Failed to fetch temp email: {reason}")

            # ---- persist ----
            with lock:
                os.makedirs("output", exist_ok=True)
                if verified and custom_display_name_enabled and set_display_name:
                    with open("output/verified_accounts.txt", "a", encoding="utf-8") as f:
                        f.write(f"{user_id}:{username}:{email}:{password}:{cookie}\n")
                    msg = f"Account created & email verified: @{username} ({display_name})"
                elif verified:
                    with open("output/verified_accounts.txt", "a", encoding="utf-8") as f:
                        f.write(f"{user_id}:{username}:{email}:{password}:{cookie}\n")
                    msg = f"Account created & email verified: @{username}"
                elif custom_display_name_enabled and set_display_name:
                    with open("output/accounts.txt", "a", encoding="utf-8") as f:
                        f.write(f"{user_id}:{username}:{password}:{cookie}\n")
                    msg = f"Account created: @{username} ({display_name})"
                else:
                    with open("output/accounts.txt", "a", encoding="utf-8") as f:
                        f.write(f"{user_id}:{username}:{password}:{cookie}\n")
                    msg = f"Account created: @{username}"

            crprint(msg)
            log_to_console("CREATED", msg)
            update_counter("generated")

        except Exception as e:
            error_msg = str(e).lower()
            if "could not resolve proxy" in error_msg:
                fprint(f"Failed to send request (5). Proxy invalid/unreachable: {proxy}")
            elif "timed out" in error_msg:
                fprint(f"Request timed out (28). Proxy: {proxy}")
            elif "unsupported proxy syntax" in error_msg:
                fprint(f"Wrong proxy format (5). Use 'username:password@host:port'. Proxy: {proxy}")
            elif "failed to connect to" in error_msg:
                fprint(f"Connection failed (7). Proxy: {proxy}")
            elif "proxy connect aborted" in error_msg:
                fprint(f"Proxy CONNECT aborted (56). Proxy: {proxy}")
            elif "connect tunnel failed" in error_msg:
                fprint(f"CONNECT tunnel failed (7). Proxy: {proxy}")
            elif "ssl certificate problem" in error_msg:
                fprint(f"SSL certificate problem (60). Proxy: {proxy}")
            elif "failed to perform, curl: (16)" in error_msg:
                fprint(f"HTTP/2 framing error (16). Proxy: {proxy}")
            elif "failed to perform, curl: (35)" in error_msg:
                fprint(f"TLS/SSL connection failed (35). Proxy: {proxy}")
            else:
                fprint(f"Unexpected error: {e}")
            update_counter("failed")
            time.sleep(5)

        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass


# =========================================================================
# Follow helper
# =========================================================================
def resolve_username_to_id(username, timeout=10):
    r = requests.post(
        "https://users.roblox.com/v1/usernames/users",
        json={"usernames": [username], "excludeBannedUsers": True},
        timeout=timeout,
        impersonate="chrome",
    )
    data = r.json().get("data") or []
    if not data:
        raise LookupError(f"unknown username {username!r}")
    return int(data[0]["id"])


def follow_user(cookie: str, target_id: int, timeout=15) -> bool:
    s = requests.Session(impersonate="chrome")
    s.cookies[".ROBLOSECURITY"] = cookie
    headers = {
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/",
        "Content-Type": "application/json",
    }
    r = s.post("https://auth.roblox.com/v1/logout", headers=headers, timeout=timeout)
    xsrf = r.headers.get("x-csrf-token")
    if not xsrf:
        raise RuntimeError("Could not obtain CSRF token (proxy dead?)")
    headers["X-CSRF-TOKEN"] = xsrf
    r = s.post(
        f"https://friends.roblox.com/v1/users/{target_id}/follow",
        headers=headers, timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Roblox follow failed: {r.status_code} {r.text[:120]}")
    return True


# =========================================================================
# Flask app — generator routes + /solve (reusing the in-process solver)
# =========================================================================
API_KEY = os.environ.get("API_KEY")
API_PORT = int(os.environ.get("PORT", "8080"))

app = Flask(__name__)


def require_api_key(fn):
    def wrapper(*a, **kw):
        if API_KEY:
            supplied = request.headers.get("X-API-Key") or request.args.get("api_key")
            if supplied != API_KEY:
                return jsonify(error="unauthorized"), 401
        return fn(*a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


@app.route("/")
def index():
    return redirect("/api/console")


@app.route("/api/health")
def api_health():
    return jsonify(ok=True, counters=utils.get_counters())


@app.route("/api/stats")
def api_stats():
    return jsonify(
        counters=utils.get_counters(),
        consecutive_captcha_fails=consecutive_captcha_fails,
        ev_enabled=EV_ENABLED_RUNTIME,
    )


@app.route("/solve", methods=["POST"])
@require_api_key
def solve_endpoint():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        private_key = data.get("private_key")
        blob = data.get("blob")
        og_cookies = data.get("og_cookies")
        og_proxy = data.get("og_proxy")

        if not private_key:
            return jsonify({"error": "Missing required parameter: private_key"}), 400
        if not og_proxy and not data.get("niggamode"):
            return jsonify({"error": "Missing required parameter: og_proxy"}), 400

        from app import solver as _solver
        import helpers.presets as _presets

        preset = _presets.get_preset(private_key)
        if not preset:
            return jsonify({"error": f"No preset found for key: {private_key}"}), 404

        if preset.requires_blob and not blob:
            return jsonify({"error": "Blob is required for this preset but was not provided"}), 400

        result = _solver.solve(
            preset=preset,
            blob=blob,
            og_cookies=og_cookies,
            og_proxy=og_proxy,
        )
        if result.variant:
            _solver.logger.log(
                "white",
                f"Token: {result.token[:28]} | Waves: {result.waves} | Variant: {result.variant}",
            )

        return jsonify({
            "solved": result.solved,
            "token": result.token,
            "variant": result.variant,
            "suppressed": result.instapass,
        }), 200

    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON data provided"}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {e}"}), 500


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
        try:
            target_id = resolve_username_to_id(str(target))
        except Exception as e:
            return jsonify(error=f"Target lookup failed: {e}"), 404

    results = []
    for i in range(count):
        log_to_console("STARTED", f"Job {i+1}/{count} for target {target_id}")
        try:
            before = _read_output()
            t = threading.Thread(target=thread_worker_iteration, daemon=True)
            t.start()
            t.join(timeout=240)

            after = _read_output()
            new_lines = [ln for ln in after if ln not in before]
            if not new_lines:
                results.append({"error": "no account created (timeout or failure)"})
                continue

            line = new_lines[-1]
            parts = line.split(":")
            if len(parts) < 5:
                results.append({"error": f"malformed output line: {line[:60]}"})
                continue

            uid, username, *rest = parts
            if len(rest) == 3:
                email, password, cookie = rest
            else:
                email = None
                password, cookie = rest

            follow_status = "skipped"
            try:
                follow_user(cookie, target_id)
                follow_status = "success"
                log_to_console("FOLLOWED", f"{username} → {target_id}")
            except Exception as e:
                follow_status = f"failed: {e}"
                log_to_console("FAILED", f"Follow failed for {username}: {e}")

            results.append({
                "user_id": uid,
                "username": username,
                "email": email,
                "password": password,
                "cookie": cookie,
                "follow_status": follow_status,
            })
        except Exception as e:
            results.append({"error": str(e)})

    return jsonify(success=True, target_id=target_id, results=results)


def _read_output():
    lines = []
    for path in ("output/accounts.txt", "output/verified_accounts.txt"):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines.extend(f.read().splitlines())
    return lines


def thread_worker_iteration():
    saved = utils.thread_restart_enabled
    try:
        utils.thread_restart_enabled = True
        _one_shot_worker()
    finally:
        utils.thread_restart_enabled = saved


def _one_shot_worker():
    global _SINGLE_RUN
    _SINGLE_RUN = True
    try:
        thread_worker()
    finally:
        _SINGLE_RUN = False


_SINGLE_RUN = False


@app.route("/api/console")
def console_page():
    html_page = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Roblox Account Generator</title>
        <style>
            body { background:#1e1e1e; color:#d4d4d4; font-family:'Consolas','Courier New',monospace; margin:0; padding:20px; }
            .header { display:flex; justify-content:space-between; border-bottom:1px solid #333; padding-bottom:10px; margin-bottom:10px; color:#fff; }
            .title { color:#4ec9b0; font-weight:bold; }
            .stats { font-size:0.9em; color:#9cdcfe; }
            #console { height:calc(100vh - 80px); overflow-y:auto; font-size:14px; line-height:1.5; }
            .log-line { white-space:pre-wrap; word-wrap:break-word; }
            .time { color:#569cd6; }
            .CREATED { color:#4ec9b0; font-weight:bold; }
            .STARTED { color:#dcdcaa; }
            .CAPTCHA { color:#ce9178; }
            .FAILED  { color:#f48771; font-weight:bold; }
            .FOLLOWED { color:#c586c0; }
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
                if (data.type === "PING") return;
                const div = document.createElement('div');
                div.className = 'log-line';
                div.innerHTML = `<span class="time">[${data.time}]</span> <span class="${data.type}">[${data.type}]</span> ${data.message}`;
                consoleDiv.appendChild(div);
                consoleDiv.scrollTop = consoleDiv.scrollHeight;
            };
            setInterval(() => {
                fetch('/api/stats').then(r => r.json()).then(d => {
                    const c = d.counters || {};
                    statsDiv.innerHTML = `Generated: ${c.generated||0} | Failed: ${c.failed||0} | EV: ${d.ev_enabled}`;
                }).catch(() => {});
            }, 2000);
        </script>
    </body>
    </html>
    """
    return Response(html_page, mimetype="text/html")


@app.route("/api/console/stream")
def console_stream():
    def event_stream(q):
        yield "data: " + json.dumps({"type": "PING"}) + "\n\n"
        while True:
            try:
                yield f"data: {q.get(timeout=20)}\n\n"
            except QueueEmpty:
                yield "data: " + json.dumps({"type": "PING"}) + "\n\n"

    q = Queue(maxsize=100)
    with CONSOLE_LOCK:
        CONSOLE_LISTENERS.append(q)

    def on_close():
        with CONSOLE_LOCK:
            try:
                CONSOLE_LISTENERS.remove(q)
            except ValueError:
                pass

    resp = Response(event_stream(q), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.call_on_close(on_close)
    return resp


# =========================================================================
# Startup
# =========================================================================
def bootstrap():
    os.makedirs("input", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs("solver-db", exist_ok=True)

    key, err = get_tempmail_public_apiKey()
    if key:
        tprint(f"temp-mail API key loaded ({key[:6]}…)")
    else:
        wprint(f"temp-mail key unavailable: {err} (EV will fail if enabled)")

    cfg = load_config()
    acc = cfg.get("account_settings", {}) or {}
    ev_cfg = (acc.get("email_verification") or {})
    dn_cfg = (acc.get("display_name") or {})
    set_runtime_options(
        ev_enabled=bool(ev_cfg.get("enabled", False)),
        display_cfg=dn_cfg,
    )
    if EV_ENABLED_RUNTIME:
        tprint("Email verification: ENABLED")
    if DISPLAY_NAME_CFG_RUNTIME.get("enabled"):
        tprint(f"Display name: ENABLED (mode={DISPLAY_NAME_CFG_RUNTIME.get('mode')})")

    try:
        from app import solver as _solver
        tprint(f"Fraudster solver initialized ({type(_solver).__name__})")
    except Exception as e:
        wprint(f"Fraudster solver warm-up failed: {e}")

    n_threads = int(cfg.get("threads", {}).get("main", 10))
    for i in range(n_threads):
        t = threading.Thread(target=thread_worker, name=f"worker-{i}", daemon=True)
        t.start()
    tprint(f"Spawned {n_threads} worker thread(s)")


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=API_PORT, threaded=True)
