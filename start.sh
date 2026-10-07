#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source tools/env.sh
uv sync --frozen
if [[ ! -d node_modules || ! -d frontend/dist ]]; then
  npm ci
  npm run build
fi
npm run setup:desktop
exec npm run desktop
