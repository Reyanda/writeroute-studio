#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python -m uvicorn app:app --host "${WRITEROUTE_HOST:-127.0.0.1}" --port "${WRITEROUTE_PORT:-8744}"
