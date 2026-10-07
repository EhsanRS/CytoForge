import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";

const root = process.cwd();
const binary = path.resolve(
  root,
  process.env.CYTOFORGE_TEST_BINARY ??
    "artifacts/installers/CytoForge-0.1.0.AppImage",
);
const fixture = path.join(root, ".tmp", `appimage launcher-${Date.now()}`);
mkdirSync(fixture, { recursive: true });
const extracted = spawnSync(binary, ["--appimage-extract", "AppRun"], {
  cwd: fixture,
  env: process.env,
  encoding: "utf8",
  timeout: 30000,
});
assert.equal(extracted.status, 0, extracted.stderr);
const launcher = path.join(fixture, "squashfs-root/AppRun");
const script = readFileSync(launcher, "utf8");
const appdir = path.dirname(launcher);
mkdirSync(path.join(appdir, "bin"));
writeFileSync(path.join(appdir, "bin/unshare"), "#!/bin/sh\nexit 1\n", {
  mode: 0o755,
});
writeFileSync(
  path.join(appdir, "cytoforge-desktop"),
  '#!/usr/bin/env bash\nfor cytoforge_arg; do printf "%s\\0" "$cytoforge_arg"; done\n',
  { mode: 0o755 },
);
const cases = [
  [],
  [
    "argument with spaces",
    "$literal`argument`",
    "--value=semi;colon",
    "O'Reilly",
  ],
  ["--no-sandbox", "--headless"],
];
for (const args of cases) {
  const result = spawnSync(launcher, args, {
    cwd: fixture,
    env: {
      ...process.env,
      APPDIR: appdir,
      PATH: `${appdir}/bin:${process.env.PATH}`,
    },
    encoding: "utf8",
    timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  const received = result.stdout ? result.stdout.slice(0, -1).split("\0") : [];
  assert.deepEqual(
    received,
    args,
    "Packaged AppRun changed the caller's flags",
  );
}
const evidence = {
  binary,
  checked_at: new Date().toISOString(),
  status: "passed",
  packaged_launcher_sha256: createHash("sha256").update(script).digest("hex"),
  blocked_namespace_fixture: true,
  no_implicit_sandbox_override: true,
  argument_quoting_preserved: true,
  explicit_test_override_preserved: true,
};
writeFileSync(
  path.join(root, "artifacts/appimage-launcher-validation.json"),
  JSON.stringify(evidence, null, 2),
);
console.log(
  "Actual AppImage launcher preserves caller arguments without an automatic sandbox override.",
);
