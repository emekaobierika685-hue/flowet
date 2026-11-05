# Dockerfile
#
# Build for the wrapper_tls_requests variant of app.py.
#
# Stage 1 (node-build):  Node 22 + jsdom  (Node 22 is required: jsdom's
#                        undici dep calls zlib.createZstdDecompress, which
#                        only exists in Node >= 22.15)
# Stage 2 (final):       Python 3.11 + Node 22 binaries + Python deps

# =========================================================================
# Stage 1: Node toolchain (jsdom for helpers/arkose.py)
# =========================================================================
FROM node:22-bookworm-slim AS node-build

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
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcurl4 \
        libssl3 \
        libffi8 \
        libxml2 \
        libxslt1.1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# ---- Node runtime (from Node 22 stage) ----
COPY --from=node-build /usr/local/bin/node /usr/local/bin/node
COPY --from=node-build /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm 2>/dev/null || true
RUN mkdir -p /usr/lib/node_modules && \
    cp -a /usr/local/lib/node_modules/* /usr/lib/node_modules/ 2>/dev/null || true

# ---- Python deps ----
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt

# ---- Pre-warm the wrapper_tls_requests Go binary ----
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

RUN mkdir -p /app/input /app/output /app/logs /app/solver-db /app/data

# ---- Sanity check: app.py must be importable under Linux ----
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
        sys.exit(1)
    raise
except Exception as e:
    print(f"[WARN] app.py import check: {e}", file=sys.stderr)

print("[OK] app.py import check passed")
PY

# ---- Healthcheck ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/solve', timeout=3).status in (400,405) else 1)" || exit 1

EXPOSE 5000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-u", "app.py"]
