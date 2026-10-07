#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/backgate-tests \
  frontend/src/backgates.ts frontend/src/reportBackgates.ts
node node_modules/rolldown/bin/cli.mjs frontend/src/reportBackgates.ts \
  --format esm --platform node --file .tmp/backgate-tests/reportBackgates.js
printf '{"type":"module"}\n' > .tmp/backgate-tests/package.json
node --test --experimental-test-isolation=none tools/test_report_backgates.mjs
