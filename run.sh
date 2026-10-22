#!/usr/bin/env bash
# Start the Personal Assistant at http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
fi
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}" "$@"
