#!/usr/bin/env bash
# Generates the showcase contract/client, starts the FastAPI server, calls it, then stops it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run --group showcase python -m scripts.fastapi_showcase.generate

uv run --group showcase python -m scripts.fastapi_showcase.serve &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    if curl --silent --fail http://127.0.0.1:8000/rpc         -H "content-type: application/json"         -d '{"jsonrpc":"2.0","method":"calculator.add","params":{"left":1,"right":1}}'         >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

uv run --group showcase python -m scripts.fastapi_showcase.call
