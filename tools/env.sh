#!/usr/bin/env bash
# Source from any working directory. Keep all generated files on this volume.
export CYTOFORGE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="$CYTOFORGE_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$CYTOFORGE_ROOT/.cache/uv/python"
export UV_TOOL_DIR="$CYTOFORGE_ROOT/.cache/uv/tools"
export UV_TOOL_BIN_DIR="$CYTOFORGE_ROOT/.local/bin"
export UV_PROJECT_ENVIRONMENT="$CYTOFORGE_ROOT/.venv"
export XDG_CACHE_HOME="$CYTOFORGE_ROOT/.cache"
export XDG_CONFIG_HOME="$CYTOFORGE_ROOT/.config"
export XDG_DATA_HOME="$CYTOFORGE_ROOT/.local/share"
export XDG_STATE_HOME="$CYTOFORGE_ROOT/.local/state"
export TMPDIR="$CYTOFORGE_ROOT/.tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export PYTHONPYCACHEPREFIX="$CYTOFORGE_ROOT/.cache/python"
export MPLCONFIGDIR="$CYTOFORGE_ROOT/.cache/matplotlib"
export NUMBA_CACHE_DIR="$CYTOFORGE_ROOT/.cache/numba"
export JOBLIB_TEMP_FOLDER="$TMPDIR"
export npm_config_cache="$CYTOFORGE_ROOT/.cache/npm"
export ELECTRON_CACHE="$CYTOFORGE_ROOT/.cache/electron"
export ELECTRON_BUILDER_CACHE="$CYTOFORGE_ROOT/.cache/electron-builder"
export PLAYWRIGHT_BROWSERS_PATH="$CYTOFORGE_ROOT/.cache/playwright"
export PIP_CACHE_DIR="$CYTOFORGE_ROOT/.cache/pip"
export CYTOFORGE_DATA_DIR="$CYTOFORGE_ROOT/data"
export PYTHONPATH="$CYTOFORGE_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
if [[ -d "$CYTOFORGE_ROOT/.cache/sysroot/root/usr/lib/x86_64-linux-gnu" ]]; then
  export LD_LIBRARY_PATH="$CYTOFORGE_ROOT/.cache/sysroot/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export GSETTINGS_SCHEMA_DIR="$CYTOFORGE_ROOT/.cache/sysroot/root/usr/share/glib-2.0/schemas"
fi
mkdir -p "$TMPDIR" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
