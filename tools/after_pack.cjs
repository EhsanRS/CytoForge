const { writeFile } = require("node:fs/promises");
const path = require("node:path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "linux") return;
  const executable = context.packager.executableName;
  if (!/^[A-Za-z0-9._-]+$/.test(executable))
    throw new Error("The Linux executable name cannot be used in AppRun");
  // The staged app directory overwrites electron-builder's generated AppRun.
  // Preserve caller flags: its upstream launcher silently adds --no-sandbox
  // when user namespaces are unavailable, including during a sandbox probe.
  const launcher = `#!/usr/bin/env bash
set -euo pipefail
cytoforge_appdir="\${APPDIR:-$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)}"
export LD_LIBRARY_PATH="$cytoforge_appdir/usr/lib\${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export XDG_DATA_DIRS="$cytoforge_appdir/usr/share\${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}:/usr/local/share:/usr/share"
if [[ -f "$cytoforge_appdir/usr/share/glib-2.0/schemas/gschemas.compiled" ]]; then
  export GSETTINGS_SCHEMA_DIR="$cytoforge_appdir/usr/share/glib-2.0/schemas"
fi
exec "$cytoforge_appdir/${executable}" "$@"
`;
  await writeFile(path.join(context.appOutDir, "AppRun"), launcher, {
    mode: 0o755,
  });
};
