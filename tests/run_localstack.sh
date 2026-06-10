#!/usr/bin/env bash
# Boot LocalStack, run the local + LocalStack handler tests, and report.
# Usage: tests/run_localstack.sh [--keep]   (--keep leaves the container running)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=safepay-localstack
KEEP="${1:-}"

# 1. fast, dependency-free fake-AWS suite
python3 "$ROOT/tests/local_e2e.py"

# 2. ensure a venv with boto3
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install --quiet --upgrade pip boto3
fi

# 3. start LocalStack (community 3.x — no auth token required)
if ! docker ps --filter "name=$NAME" --format '{{.Names}}' | grep -q "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p 4566:4566 \
    -e SERVICES=kms,dynamodb,s3,events,sns localstack/localstack:3.8.1 >/dev/null
fi

echo "waiting for LocalStack..."
for _ in $(seq 1 60); do
  if curl -s http://localhost:4566/_localstack/health | grep -q '"s3"'; then break; fi
  sleep 2
done

# 4. real-services suite
"$ROOT/.venv/bin/python" "$ROOT/tests/localstack_e2e.py"

if [ "$KEEP" != "--keep" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  echo "stopped $NAME"
fi
