import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  downloadVerified,
  readStamp,
  runNative,
  runtimeEnvironment,
  setupSteps,
} from "./windows/runtime.mjs";

const temp = path.join(process.cwd(), ".tmp");
await mkdir(temp, { recursive: true });
async function scratch(fn) {
  const directory = await mkdtemp(path.join(temp, "windows-launch-test-"));
  try {
    await fn(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}
const checksum = (value) => createHash("sha256").update(value).digest("hex");

test("altered tool download cannot replace an existing dependency or leave partial files", async () =>
  scratch(async (directory) => {
    const file = path.join(directory, "uv.zip");
    await writeFile(file, "existing");
    await assert.rejects(
      downloadVerified(
        "https://example.com/uv.zip",
        checksum("expected"),
        file,
        async () => new Response("altered"),
      ),
      /checksum failed/,
    );
    assert.equal(await readFile(file, "utf8"), "existing");
    assert.deepEqual(await readdir(directory), ["uv.zip"]);
  }));

test("verified download and verified cache reuse retain exact bytes", async () =>
  scratch(async (directory) => {
    const file = path.join(directory, "uv.zip");
    await downloadVerified(
      "https://example.com/uv.zip",
      checksum("expected"),
      file,
      async () => new Response("expected"),
    );
    await downloadVerified(
      "https://example.com/uv.zip",
      checksum("expected"),
      file,
      async () => {
        throw new Error("cache was not reused");
      },
    );
    assert.equal(await readFile(file, "utf8"), "expected");
  }));

test("download HTTP failure removes partial files", async () =>
  scratch(async (directory) => {
    await assert.rejects(
      downloadVerified(
        "https://example.com/uv.zip",
        checksum("expected"),
        path.join(directory, "uv.zip"),
        async () => new Response("missing", { status: 404 }),
      ),
      /404/,
    );
    assert.deepEqual(await readdir(directory), []);
  }));

test("Windows environment casing cannot redirect caches or turn Electron into Node", () => {
  const root = path.join(temp, "path with spaces");
  const env = runtimeEnvironment(root, {
    Path: "system-path",
    NPM_CONFIG_CACHE: "outside",
    TEMP: "outside",
    ELECTRON_RUN_AS_NODE: "1",
    Electron_Skip_Binary_Download: "1",
  });
  assert.deepEqual(
    Object.keys(env).filter((key) => key.toLowerCase() === "path"),
    ["PATH"],
  );
  assert.equal(env.npm_config_cache, path.join(root, ".cache/npm"));
  assert.equal(env.TEMP, path.join(root, ".tmp"));
  assert.equal(env.UV_PYTHON_NO_REGISTRY, "true");
  assert.equal(env.UV_PYTHON_INSTALL_REGISTRY, "false");
  assert.equal(env.UV_PYTHON_INSTALL_BIN, "false");
  assert.equal(env.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(env.Electron_Skip_Binary_Download, undefined);
  assert.equal(env.NPM_CONFIG_CACHE, undefined);
  assert(env.PATH.endsWith(";system-path"));
});

test("native execution preserves arguments with spaces and shell characters; failures stop setup", async () =>
  scratch(async (directory) => {
    const file = path.join(directory, "argument.json");
    const argument = "a space & $(do-not-execute) `literal`";
    await runNative(
      process.execPath,
      [
        "-e",
        "require('fs').writeFileSync(process.argv[1], JSON.stringify(process.argv[2]))",
        file,
        argument,
      ],
      { root: directory },
    );
    assert.equal(JSON.parse(await readFile(file, "utf8")), argument);
    await assert.rejects(
      runNative(process.execPath, ["-e", "process.exit(7)"], {
        root: directory,
      }),
      /stopped \(7\)/,
    );
  }));

test("frontend edits invalidate installation stamps and incomplete stamps recover", async () =>
  scratch(async (directory) => {
    for (const file of [
      "package.json",
      "package-lock.json",
      "frontend/package.json",
      "pyproject.toml",
      "uv.lock",
      "frontend/src/App.tsx",
    ]) {
      await mkdir(path.dirname(path.join(directory, file)), {
        recursive: true,
      });
      await writeFile(path.join(directory, file), "first");
    }
    const first = await setupSteps(directory, {});
    await writeFile(path.join(directory, "frontend/src/App.tsx"), "second");
    assert.notEqual(
      (await setupSteps(directory, {})).fingerprint,
      first.fingerprint,
    );
    const stamp = path.join(directory, "stamp.json");
    assert.equal(await readStamp(stamp), null);
    await writeFile(stamp, "{");
    assert.equal(await readStamp(stamp), null);
  }));
