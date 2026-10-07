#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$CYTOFORGE_ROOT"
node node_modules/typescript/lib/tsc.js --ignoreConfig --target es2022 \
  --module esnext --moduleResolution bundler --skipLibCheck \
  --outDir .tmp/gate-style-tests \
  frontend/src/graphGateStyle.ts frontend/src/graphTypography.ts frontend/src/scene3d.ts
node node_modules/rolldown/bin/cli.mjs frontend/src/scene3d.ts \
  --format esm --platform node --external three --file .tmp/gate-style-tests/scene3d.js
printf '{"type":"module"}\n' > .tmp/gate-style-tests/package.json
node --test --experimental-test-isolation=none tools/test_graph_gate_style.mjs
