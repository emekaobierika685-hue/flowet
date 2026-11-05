# Dockerfile
#
# Multi-stage build:
#   Stage 1 (node-build):  install Node 20 + jsdom (needed by helpers/arkose.py)
#   Stage 2 (final):       Python 3.11 + Node 20 binaries + all Python deps
#
# Build:
#   docker build -t roblox-gen:latest .
#
# Run:
#   docker run --rm -it -p 8080:8080 \
#     -v $PWD/input:/app/input:ro \
#     -v $PWD/output:/app/output \
#     -v $PWD/logs:/app/logs \
#     -v $PWD/config.yaml:/app/config.yaml:ro \
#     -e TEMPMail_PROVIDER=priyo \
#     roblox-gen:latest

# =========================================================================
# Stage 1: Node toolchain (provides node + jsdom for helpers/arkose.py)
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
    NODE_ENV=production

# ---- OS packages ----
#  - curl_cffi needs libcurl4 + OpenSSL
#  - tini reaps zombies from worker subprocesses (Node child procs)
#  - ca-certificates for HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcurl4 \
        libssl3 \
        libffi8 \
        libxml2 \
        libxslt1.1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# ---- Node runtime (copy from stage 1) ----
COPY --from=node-build /usr/local/bin/node /usr/local/bin/node
COPY --from=node-build /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm 2>/dev/null || true
# Expose jsdom to NODE_PATH so `require('jsdom')` resolves
RUN mkdir -p /usr/lib/node_modules && \
    cp -a /usr/local/lib/node_modules/* /usr/lib/node_modules/ 2>/dev/null || true

# ---- Python deps ----
# Copy requirements first for layer caching
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt

# ---- Application code ----
# Copy the whole project. Everything the runtime needs must already be on disk:
#   account_creator.py, headers.py, utils.py, secure_auth.py, tempmail.py,
#   config.yaml,
#   solvers/__init__.py, solvers/rosolve.py,
#   helpers/            (Fraudster helpers package)
#   tls_client/         (Fraudster vendored tls_client package,
#                        including tls_client/dependencies/tls-client-*.so)
#   app.py              (Fraudster solver class)
#   data/proxies.txt
#   data/webgl.json
COPY . /app

# Runtime dirs (mounted in prod, but created here so container works standalone)
RUN mkdir -p /app/input /app/output /app/logs /app/solver-db /app/data

# Sanity check: the Go TLS library must exist for the current arch.
#
# NOTE: platform.machine() on amd64 Linux returns "x86_64", NOT "x86".
# The 32-bit filename "-x86.so" is only for genuine i386/i686 machines.
RUN python - <<'PY'
import ctypes, sys, os
from sys import platform as p
from platform import machine

m = machine().lower()

if p == "darwin":
    # macOS
    if m == "arm64":
        ext = "-arm64.dylib"
    else:
        ext = "-x86.dylib"
elif p in ("win32", "cygwin"):
    # Windows
    ext = "-64.dll" if ctypes.sizeof(ctypes.c_voidp) == 8 else "-32.dll"
else:
    # Linux / other POSIX
    if m == "aarch64" or m == "arm64":
        ext = "-arm64.so"
    elif m in ("i386", "i686", "x86"):
        # genuine 32-bit only
        ext = "-x86.so"
    else:
        # x86_64, amd64, etc.
        ext = "-amd64.so"

path = f"/app/tls_client/dependencies/tls-client{ext}"

if not os.path.exists(path):
    print(f"[FATAL] Missing tls-client library: {path}", file=sys.stderr)
    print(f"        python platform : {p}", file=sys.stderr)
    print(f"        machine()       : {machine()!r}", file=sys.stderr)
    print(f"        expected file   : tls-client{ext}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Files present in /app/tls_client/dependencies/:", file=sys.stderr)
    deps_dir = "/app/tls_client/dependencies"
    if os.path.isdir(deps_dir):
        for entry in sorted(os.listdir(deps_dir)):
            print(f"  - {entry}", file=sys.stderr)
    else:
        print(f"  (directory does not exist: {deps_dir})", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix: re-copy tls_client/dependencies/ from the Fraudster repo.", file=sys.stderr)
    sys.exit(1)

print(f"[OK] tls-client library present: {path}")
PY

# ---- Healthcheck ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3).status==200 else 1)" || exit 1

EXPOSE 8080

# tini reaps zombies from worker subprocesses (Node child procs)
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["python", "-u", "account_creator.py"]
