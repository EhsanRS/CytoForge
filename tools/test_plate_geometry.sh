#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/plate-geometry-tests frontend/src/plateGeometry.ts frontend/src/platePlotWindows.ts
printf '{"type":"module"}\n' > .tmp/plate-geometry-tests/package.json
node --test --experimental-test-isolation=none tools/test_plate_geometry.mjs
