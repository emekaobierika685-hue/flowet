# solvers/rosolve.py
"""
Bridge between the new Roblox signup flow and the Fraudster solver.

Signature required by worker:
    rs_get_token(session, blob, proxy, api_key, timeout) -> (token | None, reason | "")
"""
import os
import sys

# Make sure Fraudster helpers are importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import helpers
import helpers.presets
import helpers.exceptions
from app import Funcaptcha   # the Fraudster solver class from your app.py

import utils

ROBLOX_PRESET_KEY = "A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F"

# Singleton — Fraudster is thread-safe internally
_SOLVER = None

def _get_solver():
    global _SOLVER
    if _SOLVER is None:
        _SOLVER = Funcaptcha(max_workers=100, session_pool_size=50)
    return _SOLVER

def rs_get_token(session, blob, proxy, api_key, timeout):
    """
    session : curl_cffi.requests.Session  (already proxied — Fraudster uses its own)
    blob    : base64 blob from rblx-challenge-metadata['dataExchangeBlob']
    proxy   : 'user:pass@host:port' or 'host:port'
    api_key : unused (in-process solver)
    timeout : seconds
    """
    preset = helpers.presets.get_preset(ROBLOX_PRESET_KEY)
    if preset is None:
        return None, "Roblox preset missing from helpers/presets.py"

    if not blob:
        return None, "Missing dataExchangeBlob (Roblox did not send one)"

    solver = _get_solver()

    try:
        result = solver.solve(
            preset=preset,
            blob=blob,
            og_proxy=proxy,
        )
    except helpers.exceptions.FuncaptchaError as e:
        return None, f"solver error: {e}"
    except Exception as e:
        return None, f"solver crash: {e}"

    if not result.solved:
        return None, "solver returned solved=False"

    # Roblox expects the RAW token (the same string that has |pk=...|cdn_url=...)
    return result.token, ""
