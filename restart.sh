#!/usr/bin/env bash
set -e

pkill -f "uvicorn app:app" || true
sleep 1

cd "$(dirname "$0")"
source .venv/bin/activate
nohup uvicorn app:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo "Restarted. Tail logs with: tail -f server.log"
