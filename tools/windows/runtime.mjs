import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, readdir, rename, rm } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

export function runtimeEnvironment(root, inherited = process.env) {
  const local = (relative) => path.join(root, relative);
  const overrides = {
    CYTOFORGE_ROOT: root,
    CYTOFORGE_HOME: root,
    CYTOFORGE_DATA_DIR: local("data"),
    UV_CACHE_DIR: local(".cache/uv"),
    UV_PYTHON_INSTALL_DIR: local(".cache/uv/python"),
    UV_PYTHON_NO_REGISTRY: "true",
    UV_PYTHON_INSTALL_REGISTRY: "false",
    UV_PYTHON_INSTALL_BIN: "false",
    UV_PYTHON_BIN_DIR: local(".local/bin"),
    UV_PROJECT_ENVIRONMENT: local(".venv"),
    UV_TOOL_DIR: local(".cache/uv/tools"),
    UV_TOOL_BIN_DIR: local(".local/bin"),
    XDG_CACHE_HOME: local(".cache"),
    XDG_CONFIG_HOME: local(".config"),
    XDG_DATA_HOME: local(".local/share"),
    XDG_STATE_HOME: local(".local/state"),
    TMPDIR: local(".tmp"),
    TEMP: local(".tmp"),
    TMP: local(".tmp"),
    PYTHONPYCACHEPREFIX: local(".cache/python"),
    MPLCONFIGDIR: local(".cache/matplotlib"),
    NUMBA_CACHE_DIR: local(".cache/numba"),
    JOBLIB_TEMP_FOLDER: local(".tmp"),
    PIP_CACHE_DIR: local(".cache/pip"),
    npm_config_cache: local(".cache/npm"),
    npm_config_prefix: local(".local/npm"),
    npm_config_userconfig: local(".config/npmrc"),
    ELECTRON_CACHE: local(".cache/electron"),
    ELECTRON_BUILDER_CACHE: local(".cache/electron-builder"),
    PLAYWRIGHT_BROWSERS_PATH: local(".cache/playwright"),
    OMP_NUM_THREADS: "4",
    OPENBLAS_NUM_THREADS: "4",
    PYTHONPATH: local("backend"),
    PATH: [
      local(".local/tools/node"),
      local(".local/tools/uv"),
      inherited.PATH || inherited.Path || "",
    ].join(";"),
  };
  const replaced = new Set(
    Object.keys(overrides).map((key) => key.toLowerCase()),
  );
  replaced.add("electron_run_as_node");
  replaced.add("electron_skip_binary_download");
  const env = Object.fromEntries(
    Object.entries(inherited).filter(
      ([key]) => !replaced.has(key.toLowerCase()),
    ),
  );
  return { ...env, ...overrides };
}

export async function digest(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

export async function downloadVerified(
  url,
  sha256,
  destination,
  fetcher = fetch,
) {
  if (!/^https:\/\//.test(url) || !/^[a-f0-9]{64}$/.test(sha256))
    throw new Error("Invalid pinned dependency download");
  try {
    if ((await digest(destination)) === sha256) return;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await mkdir(path.dirname(destination), { recursive: true });
  const partial = `${destination}.${randomUUID()}.partial`;
  try {
    const response = await fetcher(url, {
      signal: AbortSignal.timeout(120000),
    });
    if (!response.ok || !response.body)
      throw new Error(`Dependency download failed (${response.status})`);
    await pipeline(
      Readable.fromWeb(response.body),
      createWriteStream(partial, { flags: "wx" }),
    );
    if ((await digest(partial)) !== sha256)
      throw new Error(
        "Dependency download checksum failed; nothing was installed",
      );
    await rename(partial, destination);
  } finally {
    await rm(partial, { force: true });
  }
}

export function runNative(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.root,
      env: options.env,
      shell: false,
      windowsHide: true,
      stdio: ["inherit", "pipe", "pipe"],
    });
    child.stdout.on("data", (chunk) => {
      process.stdout.write(chunk);
      options.log?.write(chunk);
    });
    child.stderr.on("data", (chunk) => {
      process.stderr.write(chunk);
      options.log?.write(chunk);
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (code === 0) resolve();
      else
        reject(
          new Error(`${path.basename(command)} stopped (${signal || code})`),
        );
    });
  });
}

export async function setupSteps(root, pins) {
  async function rendererFiles(directory) {
    const files = [];
    for (const entry of (
      await readdir(path.join(root, directory), { withFileTypes: true })
    ).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = `${directory}/${entry.name}`;
      if (entry.isDirectory()) files.push(...(await rendererFiles(relative)));
      else if (entry.isFile()) files.push(relative);
    }
    return files;
  }
  const renderer = await rendererFiles("frontend/src");
  const hashes = await Promise.all(
    [
      "package.json",
      "package-lock.json",
      "frontend/package.json",
      "pyproject.toml",
      "uv.lock",
    ].map((relative) => digest(path.join(root, relative))),
  );
  const rendererHashes = await Promise.all(
    renderer.map(async (relative) => [
      relative,
      await digest(path.join(root, relative)),
    ]),
  );
  return {
    node: path.join(root, ".local/tools/node/node.exe"),
    npm: path.join(root, ".local/tools/node/node_modules/npm/bin/npm-cli.js"),
    uv: path.join(root, ".local/tools/uv/uv.exe"),
    fingerprint: createHash("sha256")
      .update(JSON.stringify([pins, hashes, rendererHashes]))
      .digest("hex"),
  };
}

export async function readStamp(file) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT" || error instanceof SyntaxError) return null;
    throw error;
  }
}
