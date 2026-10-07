// Promote a verified local build while preserving the supervised desktop session.
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import {
  existsSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  statSync,
  copyFileSync,
  writeFileSync,
  renameSync,
  chmodSync,
} from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const { validState } = createRequire(import.meta.url)(
  "../desktop/plot-windows.cjs",
);
const { viewMemory, MAX_SESSION_BYTES } = createRequire(import.meta.url)(
  "../desktop/plot-view-memory.cjs",
);
const root = process.cwd();
const label = process.argv[2] || "population-history";
assert(/^[a-z][a-z0-9-]{0,60}$/.test(label));
const proofManifest = process.argv[3];
if (proofManifest)
  assert(path.resolve(proofManifest).startsWith(root + path.sep));
const proofFiles = proofManifest
  ? JSON.parse(readFileSync(proofManifest, "utf8")).proofs
  : [
      "desktop-population-history-appimage.json",
      "desktop-population-history-navigation-appimage.json",
      "desktop-population-history-plot-windows-appimage.json",
      "desktop-population-history-3d-appimage.json",
      "desktop-population-history-core-appimage.json",
      "desktop-population-history-wsp-table-appimage.json",
      "desktop-population-history-table-report-appimage.json",
      "desktop-population-history-graph-report-appimage.json",
      "desktop-population-history-table-pdf-appimage-validation.json",
      "desktop-population-history-graph-pdf-appimage-validation.json",
    ];
assert(
  Array.isArray(proofFiles) &&
    proofFiles.length > 0 &&
    proofFiles.every((file) => /^[a-zA-Z0-9_.-]+\.json$/.test(file)),
);
const before = JSON.parse(
  readFileSync(
    `artifacts/desktop-preview-before-${label}-promotion.json`,
    "utf8",
  ),
);
assert.equal(before.status, "passed");
assert(
  Date.now() - Date.parse(before.checked_at) < 30000,
  "Audit the running desktop immediately before promotion",
);
assert(!before.active_jobs.length && !before.file_chooser);
assert(
  before.guards.every(
    (g) =>
      !g.visible_dialogs &&
      !g.partial_polygon &&
      !g.partial_drag &&
      !g.box_editor &&
      !g.dirty,
  ),
);
const candidate = "artifacts/release-candidate",
  backup = `artifacts/installers-before-${label}`;
assert(
  existsSync(candidate) && !existsSync(backup),
  "Keep previous installers in a new backup directory",
);
assert(
  !existsSync(`artifacts/release-validation-before-${label}.json`) &&
    !existsSync(
      `.tmp/desktop-progress/.config/plot-windows.json.before-${label}`,
    ) &&
    !existsSync(`artifacts/desktop-${label}-promotion.json`),
  "Do not overwrite a previous promotion or its session backups",
);
const binary = path.join(candidate, "CytoForge-0.1.0.AppImage");
const hash = createHash("sha256").update(readFileSync(binary)).digest("hex");
const integrity = JSON.parse(
  readFileSync("artifacts/desktop-package-integrity.json", "utf8"),
);
assert.equal(integrity.status, "passed");
assert.equal(hash, integrity.appimage_sha256);
for (const file of proofFiles) {
  const proofPath = path.join("artifacts", file);
  const proof = JSON.parse(readFileSync(proofPath, "utf8"));
  assert.equal(proof.status, "passed", file);
  assert(
    statSync(proofPath).mtimeMs >= statSync(binary).mtimeMs,
    `Stale proof: ${file}`,
  );
  if (proof.binary_sha256) assert.equal(proof.binary_sha256, hash, file);
}
const argv = readFileSync(`/proc/${before.preview_pid}/cmdline`, "utf8").split(
  "\0",
);
assert(argv[0].endsWith("node") && argv[1] === "tools/progress-preview.mjs");
assert.equal(readlinkSync(`/proc/${before.preview_pid}/cwd`), root);
const stat = (pid) => {
  try {
    const value = readFileSync(`/proc/${pid}/stat`, "utf8");
    const fields = value.slice(value.lastIndexOf(")") + 2).split(" ");
    return { state: fields[0], parent: Number(fields[1]) };
  } catch {
    return null;
  }
};
const descendants = new Set([before.preview_pid]);
const processes = readdirSync("/proc")
  .filter((v) => /^\d+$/.test(v))
  .map(Number);
let changed = true;
while (changed) {
  changed = false;
  for (const pid of processes) {
    if (!descendants.has(pid) && descendants.has(stat(pid)?.parent)) {
      descendants.add(pid);
      changed = true;
    }
  }
}
assert(
  descendants.has(before.desktop_pid) &&
    descendants.has(before.private_engine_pid),
);
process.kill(before.preview_pid, "SIGTERM");
const deadline = Date.now() + 45000;
while (
  [...descendants].some((pid) => {
    const value = stat(pid);
    return value && value.state !== "Z";
  })
) {
  assert(
    Date.now() < deadline,
    "The owned desktop has not shut down; do not force termination or replace its files",
  );
  await new Promise((resolve) => setTimeout(resolve, 200));
}
const settings = ".tmp/desktop-progress/.config/plot-windows.json";
let saved = existsSync(settings)
  ? JSON.parse(readFileSync(settings, "utf8"))
  : { version: 1, windows: [] };
assert(
  [1, 2].includes(saved.version) &&
    Array.isArray(saved.windows) &&
    saved.windows.length <= 32,
);
if (before.main_view) assert(validState(before.main_view));
for (const record of saved.windows) {
  assert(validState(record.state));
  viewMemory(validState, record.views || []);
}
const mainMemory = viewMemory(validState, saved.main?.views || [], 192);
if (before.main_view) mainMemory.remember(before.main_view);
const migrated = {
  ...saved,
  version: 2,
  main: { state: before.main_view, views: mainMemory.serialize() },
};
const serialized = JSON.stringify(migrated);
assert(Buffer.byteLength(serialized) <= MAX_SESSION_BYTES);
if (existsSync(settings)) {
  copyFileSync(settings, settings + `.before-${label}`);
  chmodSync(settings + `.before-${label}`, 0o600);
}
const temporary = settings + "." + randomBytes(6).toString("hex") + ".tmp";
writeFileSync(temporary, serialized, { mode: 0o600, flag: "wx", flush: true });
renameSync(temporary, settings);
copyFileSync(
  "artifacts/release-validation.json",
  `artifacts/release-validation-before-${label}.json`,
);
renameSync("artifacts/installers", backup);
renameSync(candidate, "artifacts/installers");
const evidence = {
  status: "passed",
  promoted_at: new Date().toISOString(),
  stopped_preview_pid: before.preview_pid,
  owned_pids_stopped: [...descendants],
  all_owned_processes_stopped: true,
  previous_installers: backup,
  appimage_sha256: hash,
  main_view_migrated: !!before.main_view,
  previous_plot_session_version: saved.version,
  release_label: label,
  validated_proofs: proofFiles,
  plot_session_version: 2,
  restored_window_descriptors: saved.windows.length,
  original_workspace_id: before.original_workspace_id,
  original_workspace_sha256: before.original_workspace_sha256,
  scientific_workspace_writes: 0,
};
writeFileSync(
  `artifacts/desktop-${label}-promotion.json`,
  JSON.stringify(evidence, null, 2),
);
console.log(JSON.stringify(evidence));
