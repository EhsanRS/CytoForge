#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/spectral-autospill-tests frontend/src/spectralAutoSpill.ts
node node_modules/rolldown/bin/cli.mjs frontend/src/spectralAutoSpill.ts \
  --format esm --platform node --file .tmp/spectral-autospill-tests/spectralAutoSpill.js
printf '{"type":"module"}\n' > .tmp/spectral-autospill-tests/package.json
node --test --experimental-test-isolation=none tools/test_spectral_autospill.mjs
