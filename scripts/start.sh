#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
    python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "The .venv requires Python 3.11+")'
.venv/bin/python -m pip install -r requirements-lock.txt
(cd frontend && npm ci && npm run build)
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8000}"
