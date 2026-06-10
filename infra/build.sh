#!/usr/bin/env bash
# Assemble Lambda deployment packages under infra/build/<fn>/.
# Each package contains the handler plus the shared `common/` package so
# `from common import ...` resolves at runtime. No external dependencies are
# needed beyond boto3, which the Lambda runtime provides.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES="$HERE/../services"
BUILD="$HERE/build"

rm -rf "$BUILD"
mkdir -p "$BUILD"

# Wallet and Token import the shared common package.
for fn in wallet token; do
  mkdir -p "$BUILD/$fn"
  cp "$SERVICES/$fn/handler.py" "$BUILD/$fn/handler.py"
  cp -r "$SERVICES/common" "$BUILD/$fn/common"
done

# Issuer is self-contained.
mkdir -p "$BUILD/issuer"
cp "$SERVICES/issuer/handler.py" "$BUILD/issuer/handler.py"

# Drop bytecode caches so packages are deterministic.
find "$BUILD" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "built packages in $BUILD"
