import { spawn, spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cache = path.join(root, ".cache"),
  temp = path.join(root, ".tmp");
for (const name of [
  cache,
  temp,
  path.join(root, ".config"),
  path.join(root, ".local/share"),
  path.join(root, ".local/state"),
])
  mkdirSync(name, { recursive: true });
const env = {
  ...process.env,
  CYTOFORGE_ROOT: root,
  UV_CACHE_DIR: path.join(cache, "uv"),
  UV_PYTHON_INSTALL_DIR: path.join(cache, "uv/python"),
  UV_TOOL_DIR: path.join(cache, "uv/tools"),
  UV_TOOL_BIN_DIR: path.join(root, ".local/bin"),
  UV_PROJECT_ENVIRONMENT: path.join(root, ".venv"),
  XDG_CACHE_HOME: cache,
  XDG_CONFIG_HOME: path.join(root, ".config"),
  XDG_DATA_HOME: path.join(root, ".local/share"),
  XDG_STATE_HOME: path.join(root, ".local/state"),
  TMPDIR: temp,
  TEMP: temp,
  TMP: temp,
  PYTHONPYCACHEPREFIX: path.join(cache, "python"),
  MPLCONFIGDIR: path.join(cache, "matplotlib"),
  NUMBA_CACHE_DIR: path.join(cache, "numba"),
  JOBLIB_TEMP_FOLDER: temp,
  npm_config_cache: path.join(cache, "npm"),
  ELECTRON_CACHE: path.join(cache, "electron"),
  ELECTRON_BUILDER_CACHE: path.join(cache, "electron-builder"),
  PLAYWRIGHT_BROWSERS_PATH: path.join(cache, "playwright"),
  CYTOFORGE_DATA_DIR: path.join(root, "data"),
  CYTOFORGE_FRONTEND_ORIGIN: "http://127.0.0.1:5173",
  PYTHONPATH: path.join(root, "backend"),
  OMP_NUM_THREADS: "4",
  OPENBLAS_NUM_THREADS: "4",
};
const children = [];
function start(command, args) {
  const child = spawn(command, args, {
    cwd: root,
    env,
    stdio: "inherit",
    shell: false,
  });
  children.push(child);
  child.on("error", (error) => {
    console.error(`${command}: ${error.message}`);
    shutdown(1);
  });
  child.on("exit", (code) => {
    if (!stopping) shutdown(code || 0);
  });
  return child;
}
let stopping = false;
function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) if (!child.killed) child.kill("SIGTERM");
  setTimeout(() => process.exit(code), 1500).unref();
}
process.on("SIGINT", () => shutdown());
process.on("SIGTERM", () => shutdown());
const setup = spawnSync("uv", ["sync", "--frozen"], {
  cwd: root,
  env,
  stdio: "inherit",
});
if (setup.status !== 0) process.exit(setup.status || 1);
start(
  path.join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  ),
  ["-m", "cytoforge", "--port", "8765", "--parent-pid", String(process.pid)],
);
start(process.execPath, [
  path.join(root, "node_modules/vite/bin/vite.js"),
  "--config",
  path.join(root, "frontend/vite.config.ts"),
  "--host",
  "127.0.0.1",
  "--port",
  "5173",
  path.join(root, "frontend"),
]);
