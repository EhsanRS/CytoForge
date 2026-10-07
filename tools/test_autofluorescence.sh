#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/autofluorescence-tests frontend/src/autofluorescence.ts
node node_modules/rolldown/bin/cli.mjs frontend/src/autofluorescence.ts \
  --format esm --platform node --file .tmp/autofluorescence-tests/autofluorescence.js
printf '{"type":"module"}\n' > .tmp/autofluorescence-tests/package.json
node --test --experimental-test-isolation=none tools/test_autofluorescence.mjs
