# Dockerfile
#
# Build for the wrapper_tls_requests variant of app.py.
#
# Stage 1 (node-build):  Node 20 + jsdom  (needed by helpers/arkose.py's t_guess)
# Stage 2 (final):       Python 3.11 + Node binaries + Python deps
#
# Notes:
#   - NO vendored tls_client/ — wrapper_tls_requests ships its own Go binary.
#   - TLSClient.initialize() in app.py downloads tls-client-amd64.so on first
#     run; the pre-warm RUN below does that at build time so the container
#     doesn't need outbound HTTPS to GitHub at runtime.
#   - app.py uses ctypes.windll.kernel32.SetConsoleTitleW on import → crashes
#     on Linux. The sanity-check RUN below fails the build with a clear
#     message if you haven't patched it yet.

# =========================================================================
# Stage 1: Node toolchain (jsdom for helpers/arkose.py)
# =========================================================================
FROM node:20-bookworm-slim AS node-build

WORKDIR /nodeapp
RUN npm init -y >/dev/null 2>&1 && \
    npm install --omit=dev jsdom@24 >/dev/null 2>&1

# =========================================================================
# Stage 2: Python runtime
# =========================================================================
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_PATH=/usr/lib/node_modules \
    NODE_ENV=production \
    HOME=/root

# ---- OS packages ----
#  - libcurl4 + libssl3:      needed by curl_cffi (used by wrapper_tls_requests)
#  - libffi8:                 needed by cffi (cryptography)
#  - libxml2 / libxslt1.1:    needed by jsdom's Python bindings
#  - tini:                    PID-1 zombie reaper for Node child procs
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcurl4 \
        libssl3 \
        libffi8 \
        libxml2 \
        libxslt1.1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# ---- Node runtime ----
COPY --from=node-build /usr/local/bin/node /usr/local/bin/node
COPY --from=node-build /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm 2>/dev/null || true
# jsdom resolution: require('jsdom') must resolve from NODE_PATH
RUN mkdir -p /usr/lib/node_modules && \
    cp -a /usr/local/lib/node_modules/* /usr/lib/node_modules/ 2>/dev/null || true

# ---- Python deps ----
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt

# ---- Pre-warm the wrapper_tls_requests Go binary ----
# TLSClient.initialize() downloads the correct tls-client-*.so for this arch
# and caches it under $HOME/.tls_requests/ (or sys.prefix).
# Doing it at build time means the running container doesn't need outbound
# HTTPS to GitHub at runtime.
RUN python - <<'PY'
import sys
try:
    from tls_requests import TLSClient
    TLSClient.initialize()
    print("[OK] wrapper_tls_requests binary initialized")
except Exception as e:
    print(f"[FATAL] TLSClient.initialize() failed: {e}", file=sys.stderr)
    import traceback; traceback.print_exc()
    sys.exit(1)
PY

# ---- Application code ----
COPY . /app

# Runtime dirs (mounted in prod but created here so the image works standalone)
RUN mkdir -p /app/input /app/output /app/logs /app/solver-db /app/data

# ---- Sanity check: app.py must be importable under Linux ----
# This catches the `ctypes.windll` crash BEFORE we build the whole image.
RUN python - <<'PY'
import sys, os
os.environ.setdefault("PYTHONUNBUFFERED", "1")

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_module", "/app/app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app_module"] = mod
    spec.loader.exec_module(mod)
except AttributeError as e:
    if "windll" in str(e):
        print("[FATAL] app.py still uses ctypes.windll.kernel32 on Linux.", file=sys.stderr)
        print("        Patch the top of app.py with a try/except guard:", file=sys.stderr)
        print("", file=sys.stderr)
        print("          try:", file=sys.stderr)
        print("              SetConsoleTitle = ctypes.windll.kernel32.SetConsoleTitleW", file=sys.stderr)
        print("          except AttributeError:", file=sys.stderr)
        print("              def SetConsoleTitle(_t): pass", file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)
    raise
except Exception as e:
    # Other import errors (missing module, bad syntax) are surfaced, not hidden
    print(f"[WARN] app.py import check: {e}", file=sys.stderr)

print("[OK] app.py import check passed")
PY

# ---- Healthcheck ----
# app.py's Flask runs on port 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/solve', timeout=3).status in (400,405) else 1)" || exit 1

EXPOSE 5000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-u", "app.py"]
