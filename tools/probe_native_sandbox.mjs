import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const binary = path.resolve(
  root,
  process.env.CYTOFORGE_TEST_BINARY ??
    "artifacts/installers/CytoForge-0.1.0.AppImage",
);
const args = ["--headless", "--ozone-platform=headless", "--disable-gpu"];
const profile = path.join(root, ".tmp", `native-sandbox-probe-${Date.now()}`);
const temporary = path.join(profile, ".tmp");
mkdirSync(temporary, { recursive: true });
// Run sequentially with other extract-and-run checks: the AppImage runtime
// shares its extracted directory for identical image bytes.
const probe = spawnSync(binary, args, {
  env: {
    ...process.env,
    APPIMAGE_EXTRACT_AND_RUN: "1",
    CYTOFORGE_HEADLESS_TEST: "1",
    CYTOFORGE_HOME: profile,
    TMPDIR: temporary,
    TEMP: temporary,
    TMP: temporary,
  },
  encoding: "utf8",
  timeout: 15000,
});
const report = {
  binary,
  temporary_directory: temporary,
  checked_at: new Date().toISOString(),
  status: probe.error?.code === "ETIMEDOUT" ? "unverified" : "failed",
  headless: true,
  sandbox_exception: false,
  production_launcher: true,
  caller_arguments: args,
  exit_code: probe.status,
  signal: probe.signal,
  stderr: probe.stderr.slice(-16384),
  stdout: probe.stdout.slice(-2048),
  stdout_truncated: probe.stdout.length > 2048,
  error: probe.error?.message ?? null,
  missing_suid_helper_setup:
    /SUID sandbox helper|owned by root.*4755|mode 4755/s.test(probe.stderr),
};
writeFileSync(
  path.join(root, "artifacts/native-sandbox-probe.json"),
  JSON.stringify(report, null, 2),
);
console.log(JSON.stringify(report, null, 2));
