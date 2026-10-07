// Run on Windows x64: verify native engine and plot windows before producing installers.
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { extractFile } from "@electron/asar";
import {
  digest,
  runNative,
  runtimeEnvironment,
  setupSteps,
} from "./runtime.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
if (process.platform !== "win32" || process.arch !== "x64") {
  console.error(
    "Build Windows installers on Windows x64; Linux engines cannot be used.",
  );
  process.exit(1);
}
const env = {
  ...runtimeEnvironment(root),
  CSC_IDENTITY_AUTO_DISCOVERY: "false",
};
const run = (command, args, extra = {}) =>
  runNative(command, args, { root, env: { ...env, ...extra } });
await mkdir(path.join(root, "artifacts"), { recursive: true });
await run(process.execPath, [
  path.join(root, "tools/windows/run.mjs"),
  "--dev-tools",
]);
const pins = JSON.parse(
  await readFile(path.join(root, "tools/windows/toolchain.json"), "utf8"),
);
const tools = await setupSteps(root, pins);
const npm = (...args) => run(tools.node, [tools.npm, ...args]);
const python = (...args) =>
  run(tools.uv, ["run", "--no-sync", "python", ...args]);
await npm("run", "typecheck");
await run(tools.node, [
  "--test",
  "--experimental-test-isolation=none",
  "tools/test_windows_runtime.mjs",
]);
await run(tools.uv, [
  "run",
  "--no-sync",
  "pytest",
  "--in-process-asgi",
  "--basetemp=.tmp/pytest-windows",
  "-q",
  "--junitxml=artifacts/pytest-windows-full.xml",
]);
await python("tools/package_engine.py");
const engine = path.join(
  root,
  "artifacts/engine/cytoforge-engine/cytoforge-engine.exe",
);
if ((await readFile(engine)).subarray(0, 2).toString() !== "MZ")
  throw new Error("A native Windows engine was not produced");
await python("tools/engine_smoke.py", "--algorithms", "pca,phenograph");
const builder = path.join(root, "node_modules/electron-builder/cli.js");
await run(tools.node, [builder, "--win", "dir", "--x64", "--publish", "never"]);
const unpacked = path.join(root, "artifacts/installers/win-unpacked");
const binary = path.join(unpacked, "CytoForge.exe");
if (!existsSync(binary)) throw new Error("Windows desktop executable missing");
async function inventory(directory, prefix = "") {
  const result = {};
  for (const entry of await readdir(path.join(directory, prefix), {
    withFileTypes: true,
  })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory())
      Object.assign(result, await inventory(directory, relative));
    else if (entry.isFile())
      result[relative] = await digest(path.join(directory, relative));
    else throw new Error(`Unexpected package entry: ${relative}`);
  }
  return result;
}
async function compare(source, target) {
  const a = await inventory(source),
    b = await inventory(target);
  if (JSON.stringify(a) !== JSON.stringify(b))
    throw new Error(`Packaged files differ: ${target}`);
  return Object.keys(a).length;
}
const engineFiles = await compare(
  path.dirname(engine),
  path.join(unpacked, "resources/engine"),
);
const frontendFiles = await compare(
  path.join(root, "frontend/dist"),
  path.join(unpacked, "resources/frontend"),
);
const desktop = await inventory(path.join(root, "desktop"));
const archive = path.join(unpacked, "resources/app.asar");
for (const relative of Object.keys(desktop)) {
  if (
    !extractFile(archive, `desktop/${relative}`).equals(
      await readFile(path.join(root, "desktop", relative)),
    )
  )
    throw new Error(`Packaged desktop source differs: ${relative}`);
}
const smoke = { CYTOFORGE_TEST_BINARY: binary };
delete env.CYTOFORGE_TEST_NO_SANDBOX;
await run(tools.node, ["tools/desktop_smoke.mjs"], smoke);
await run(tools.node, ["tools/desktop_plot_windows_smoke.mjs"], smoke);
await run(tools.node, [
  builder,
  "--prepackaged",
  unpacked,
  "--win",
  "nsis",
  "portable",
  "--x64",
  "--publish",
  "never",
]);
const installers = {};
for (const file of await readdir(path.join(root, "artifacts/installers"))) {
  if (file.endsWith(".exe"))
    installers[file] = await digest(
      path.join(root, "artifacts/installers", file),
    );
}
if (Object.keys(installers).length !== 2)
  throw new Error("Both Windows setup and portable EXEs are required");
await writeFile(
  path.join(root, "artifacts/windows-build-validation.json"),
  JSON.stringify(
    {
      status: "passed",
      platform: process.platform,
      architecture: process.arch,
      native_engine_sha256: await digest(engine),
      engine_files_matched: engineFiles,
      frontend_files_matched: frontendFiles,
      desktop_files_matched: Object.keys(desktop).length,
      source_regression: "artifacts/pytest-windows-full.xml",
      native_desktop: "artifacts/desktop-smoke.json",
      independent_plot_windows: "artifacts/desktop-plot-windows-smoke.json",
      installers,
      signed: false,
      biological_validation: false,
    },
    null,
    2,
  ) + "\n",
);
await writeFile(
  path.join(root, "artifacts/installers/SHA256SUMS.txt"),
  Object.entries(installers)
    .map(([file, hash]) => `${hash}  ${file}\n`)
    .join(""),
);
console.log(
  "Native Windows source, engine, desktop and independent plot-window checks passed.",
);
