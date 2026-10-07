#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/spreading-tests frontend/src/spreading.ts
node node_modules/rolldown/bin/cli.mjs frontend/src/spreading.ts \
  --format esm --platform node --file .tmp/spreading-tests/spreading.js
printf '{"type":"module"}\n' > .tmp/spreading-tests/package.json
node --test --experimental-test-isolation=none tools/test_spreading.mjs
