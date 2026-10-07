// Local Windows setup and desktop launch; no system policy changes.
import { createWriteStream, existsSync } from "node:fs";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import {
  downloadVerified,
  readStamp,
  runNative,
  runtimeEnvironment,
  setupSteps,
} from "./runtime.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
if (process.platform !== "win32" || process.arch !== "x64") {
  console.error("This launcher requires Windows x64 on an Intel or AMD PC.");
  process.exit(1);
}
const mode = process.argv.slice(2);
if (
  mode.length > 1 ||
  (mode.length === 1 && !["--setup-only", "--dev-tools"].includes(mode[0]))
) {
  console.error("Usage: Start-CytoForge.cmd [--setup-only|--dev-tools]");
  process.exit(1);
}
const env = runtimeEnvironment(root);
for (const folder of [".tmp", ".cache/logs", ".config"])
  await mkdir(path.join(root, folder), { recursive: true });
const log = createWriteStream(
  path.join(root, ".cache/logs/windows-start.log"),
  { flags: "a" },
);
const run = (command, args, extraEnv = {}) =>
  runNative(command, args, { root, env: { ...env, ...extraEnv }, log });
try {
  const pins = JSON.parse(
    await readFile(path.join(root, "tools/windows/toolchain.json"), "utf8"),
  );
  const tools = await setupSteps(root, pins);
  if (!existsSync(tools.uv)) {
    console.log("Downloading the pinned Windows Python package manager...");
    const archive = path.join(root, ".cache/downloads/uv-windows-x64.zip");
    await downloadVerified(pins.uv.url, pins.uv.sha256, archive);
    const stage = path.join(root, ".tmp", `uv-bootstrap-${randomUUID()}`);
    try {
      await run(
        "powershell.exe",
        [
          "-NoLogo",
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          "Expand-Archive -LiteralPath $env:CYTOFORGE_ARCHIVE -DestinationPath $env:CYTOFORGE_UNPACK",
        ],
        { CYTOFORGE_ARCHIVE: archive, CYTOFORGE_UNPACK: stage },
      );
      if (!existsSync(path.join(stage, "uv.exe")))
        throw new Error("The Windows uv archive is incomplete");
      await mkdir(path.dirname(path.dirname(tools.uv)), { recursive: true });
      await rename(stage, path.dirname(tools.uv));
    } finally {
      await rm(stage, { recursive: true, force: true });
    }
  }
  console.log("Checking Python 3.13 and the locked scientific dependencies...");
  await run(tools.uv, [
    "sync",
    "--frozen",
    "--managed-python",
    "--python",
    "3.13",
    ...(mode[0] === "--dev-tools" ? [] : ["--no-dev"]),
  ]);
  const stampFile = path.join(root, ".cache/windows-runtime.json");
  const stamp = await readStamp(stampFile);
  if (
    stamp?.fingerprint !== tools.fingerprint ||
    !existsSync(path.join(root, "node_modules/electron/dist/electron.exe")) ||
    !existsSync(path.join(root, "frontend/dist/index.html"))
  ) {
    console.log(
      "Installing locked desktop dependencies and building the interface...",
    );
    await run(tools.node, [tools.npm, "ci"]);
    await run(tools.node, [tools.npm, "run", "setup:desktop"]);
    await run(tools.node, [tools.npm, "run", "build"]);
    const temporary = `${stampFile}.${randomUUID()}.partial`;
    await writeFile(
      temporary,
      JSON.stringify({
        fingerprint: tools.fingerprint,
        platform: "win32",
        arch: "x64",
      }) + "\n",
      { flag: "wx" },
    );
    await rename(temporary, stampFile);
  }
  if (!mode.length) {
    console.log("Opening CytoForge. Analysis and plot windows run on this PC.");
    await run(tools.node, [
      path.join(root, "node_modules/electron/cli.js"),
      ".",
    ]);
  }
} catch (error) {
  console.error(error.message);
  log.write(`\n${error.message}\n`);
  process.exitCode = 1;
} finally {
  await new Promise((resolve) => log.end(resolve));
}
