# tempmail.py
#
# Temp-mail client for priyo.email (free public API).
# Returns:
#   get_email(session)          -> (email: str | None, reason: str | None)
#   get_inbox(session, email)   -> (ticket: str | None, reason: str | None)
#   get_tempmail_public_apiKey()-> (key: str | None, reason: str | None)
#
# Notes:
# - The API key is scraped from https://v3.priyo.email/api-docs (undocumented).
# - If priyo changes their HTML, get_tempmail_public_apiKey() returns an error.
# - Swap the PROVIDER block below to use mail.tm / 1secmail / etc.
#
# ⚠️  DO NOT add `from tempmail import ...` here — that's a self-import and
#     will crash with "cannot import name from partially initialized module".
#     `from tempmail import ...` belongs only in account_creator.py.

import re
import time
import urllib.parse
from threading import Lock

import requests


# =========================================================================
# Provider config — change this env var to switch providers
# =========================================================================
import os
PROVIDER = os.environ.get("TEMPMail_PROVIDER", "priyo").lower()


# =========================================================================
# Shared state
# =========================================================================
tempmail_public_apiKey = None
tempmail_lock = Lock()
_key_attempted = False
_key_attempted_ts = 0.0
_KEY_RETRY_AFTER = 300


# =========================================================================
# API key fetch (priyo.email only)
# =========================================================================
def get_tempmail_public_apiKey():
    """
    Scrape the public API key from priyo.email's /api-docs page.
    Cached for process lifetime. Retries at most every 5 minutes on failure.

    Returns: (key, None) on success, (None, reason) on failure.
    """
    global tempmail_public_apiKey, _key_attempted, _key_attempted_ts

    with tempmail_lock:
        if tempmail_public_apiKey:
            return tempmail_public_apiKey, None

        now = time.time()
        if _key_attempted and (now - _key_attempted_ts) < _KEY_RETRY_AFTER:
            return None, "API key fetch previously failed (retry in progress)"
        _key_attempted = True
        _key_attempted_ts = now

    try:
        response = requests.get(
            "https://v3.priyo.email/api-docs",
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code != 200:
            return None, f"api-docs returned HTTP {response.status_code}"

        match = re.search(
            r'<input.*?type=["\']password["\'].*?value=["\']([^"\']+)["\']',
            response.text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None, "API key not found in /api-docs HTML (priyo changed layout)"

        with tempmail_lock:
            tempmail_public_apiKey = match.group(1)
        return tempmail_public_apiKey, None

    except requests.exceptions.Timeout:
        return None, "api-docs request timed out"
    except requests.exceptions.ConnectionError as e:
        return None, f"api-docs connection error: {e}"
    except Exception as e:
        return None, f"api-docs unexpected error: {e}"


# =========================================================================
# Get a fresh email address
# =========================================================================
def get_email(session):
    """
    Fetch a fresh random email address.
    `session` is the caller's curl_cffi session (already proxied).
    Returns: (email, None) or (None, reason).
    """
    key, key_err = get_tempmail_public_apiKey()
    if not key:
        return None, f"temp-mail unavailable: {key_err}"

    max_retries = 2
    retry_count = 0

    while retry_count <= max_retries:
        try:
            response = session.get(
                f"https://free.priyo.email/api/random-email/{key}",
                timeout=15,
            )

            if response.status_code == 429:
                retry_count += 1
                if retry_count > max_retries:
                    return None, "rate-limited (429) on random-email"
                time.sleep(3)
                continue

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as ve:
                    return None, f"invalid JSON from random-email ({ve})"

                if isinstance(data, dict):
                    email = data.get("email")
                    if email and "@" in email:
                        return email, None
                    return None, "'email' key missing or malformed in response"
                return None, f"random-email JSON is not a dict: {type(data).__name__}"

            return None, f"random-email HTTP {response.status_code}"

        except requests.exceptions.Timeout:
            retry_count += 1
            if retry_count > max_retries:
                return None, "random-email timed out"
            time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            retry_count += 1
            if retry_count > max_retries:
                return None, f"random-email connection error: {e}"
            time.sleep(1)
        except Exception as e:
            retry_count += 1
            if retry_count > max_retries:
                msg = str(e).lower()
                if "could not resolve proxy" in msg:
                    return None, "proxy invalid or unreachable"
                return None, f"random-email error: {e}"
            time.sleep(1)

    return None, "random-email exhausted retries"


# =========================================================================
# Poll the inbox for the Roblox verification ticket
# =========================================================================
def get_inbox(session, email):
    """
    Poll the inbox for a Roblox verification email and extract the ticket.

    Returns: (ticket, None) or (None, reason).

    Hard deadline of 60s so a throttled inbox never blocks a worker forever.
    """
    key, key_err = get_tempmail_public_apiKey()
    if not key:
        return None, f"temp-mail unavailable: {key_err}"

    deadline = time.time() + 60
    error_retries = 0
    MAX_ERROR_RETRIES = 3
    empty_count = 0
    MAX_EMPTY_RETRIES = 15

    TICKET_RE = re.compile(
        r'<a[^>]*class=["\'][^"\']*email-button[^"\']*["\'][^>]*'
        r'href=["\'][^"\']*ticket=([^"&\']+)',
        re.IGNORECASE | re.DOTALL,
    )

    while time.time() < deadline:
        try:
            response = session.get(
                f"https://free.priyo.email/api/messages/{email}/{key}",
                timeout=10,
            )

            if response.status_code == 429:
                time.sleep(3)
                continue

            if response.status_code != 200:
                error_retries += 1
                if error_retries > MAX_ERROR_RETRIES:
                    return None, f"messages HTTP {response.status_code}"
                time.sleep(1)
                continue

            try:
                data = response.json()
            except ValueError as ve:
                error_retries += 1
                if error_retries > MAX_ERROR_RETRIES:
                    return None, f"invalid JSON from messages ({ve})"
                time.sleep(1)
                continue

            if not data:
                empty_count += 1
                if empty_count > MAX_EMPTY_RETRIES:
                    return None, "no messages received after maximum retries"
                time.sleep(1)
                continue

            for msg in data:
                if not isinstance(msg, dict):
                    continue
                html_content = msg.get("content", "") or ""
                match = TICKET_RE.search(html_content)
                if match:
                    return urllib.parse.unquote(match.group(1)), None

            empty_count += 1
            if empty_count > MAX_EMPTY_RETRIES:
                return None, "ticket not found in email content after maximum retries"
            time.sleep(1)

        except requests.exceptions.Timeout:
            error_retries += 1
            if error_retries > MAX_ERROR_RETRIES:
                return None, "messages request timed out"
            time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            error_retries += 1
            if error_retries > MAX_ERROR_RETRIES:
                return None, f"messages connection error: {e}"
            time.sleep(1)
        except Exception as e:
            error_retries += 1
            if error_retries > MAX_ERROR_RETRIES:
                msg = str(e).lower()
                if "could not resolve proxy" in msg:
                    return None, "proxy invalid or unreachable"
                return None, f"messages error: {e}"
            time.sleep(1)

    return None, "inbox poll timed out (60s deadline)"
