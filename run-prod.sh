#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"
source .venv/bin/activate
nohup uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo "Started. Tail logs with: tail -f server.log"
