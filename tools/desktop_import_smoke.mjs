// Real file chooser, chained acquisition import, cancellation, export and restart.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  createWriteStream,
  readdirSync,
} from "node:fs";
import { once } from "node:events";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp/desktop-import-" + Date.now());
const fixture = path.join(root, "artifacts/import-fixture");
const exportsDirectory = path.join(profile, "exports");
const evidencePath =
  process.env.CYTOFORGE_IMPORT_EVIDENCE ||
  path.join(root, "artifacts/desktop-import-smoke.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(exportsDirectory, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
let application, window;
const errors = [];
const timeout = setTimeout(() => application?.process().kill(), 180000);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function launch() {
  return electron.launch({
    executablePath: binary,
    args: [
      ...(binary ? [] : ["."]),
      "--headless",
      "--ozone-platform=headless",
      "--disable-gpu",
      ...(process.env.CYTOFORGE_TEST_NO_SANDBOX === "1"
        ? ["--no-sandbox"]
        : []),
    ],
    chromiumSandbox: process.env.CYTOFORGE_TEST_NO_SANDBOX !== "1",
    env: {
      ...process.env,
      CYTOFORGE_HOME: profile,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
}
async function connect() {
  window = await application.firstWindow();
  window.on("pageerror", (error) => errors.push(error.message));
  await window.setViewportSize({ width: 1540, height: 1050 });
  await window.waitForLoadState("networkidle");
  await application.evaluate(({ session }, directory) => {
    session.defaultSession.on("will-download", (_event, item) =>
      item.setSavePath(directory + "/" + item.getFilename()),
    );
  }, exportsDirectory);
}
async function get(route) {
  return window.evaluate(async (route) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const response = await fetch("/api" + route, {
      headers: { "X-CytoForge-Token": token },
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }, route);
}
async function choose(paths) {
  const created = window.waitForResponse(
    (response) =>
      response.url().endsWith("/imports") &&
      response.request().method() === "POST",
  );
  const chooser = window.waitForEvent("filechooser");
  await window.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(paths);
  return (await created).json();
}
async function finish(title) {
  await expect(
    window.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible({
    timeout: 30000,
  });
  await window.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(window.getByRole("dialog")).not.toBeVisible();
}
async function settledFile(filename) {
  const output = path.join(exportsDirectory, filename);
  for (let tries = 0; tries < 100; tries++) {
    if (existsSync(output)) return output;
    await sleep(100);
  }
  throw new Error("The desktop did not complete its native export");
}
async function nativeDownload(filename, click) {
  const output = path.join(exportsDirectory, filename);
  const completion = application.evaluate(
    ({ session }, target) =>
      new Promise((resolve, reject) => {
        session.defaultSession.once("will-download", (_event, item) => {
          item.setSavePath(target);
          item.once("done", (_event, state) => {
            if (state === "completed") resolve(target);
            else reject(new Error("Native desktop download " + state));
          });
        });
      }),
    output,
  );
  await click();
  await completion;
  return settledFile(filename);
}

try {
  application = await launch();
  await connect();
  const packaged = await application.evaluate(({ app }) => app.isPackaged);
  assert.equal(packaged, Boolean(binary));
  await window
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await window
    .getByLabel("Experiment name", { exact: true })
    .fill("Native import scientific truth");
  await window
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(window.getByRole("dialog")).not.toBeVisible();
  const firstSession = await choose([
    path.join(fixture, "three-datasets.fcs"),
    path.join(fixture, "good.csv"),
    path.join(fixture, "broken-chain.fcs"),
    path.join(fixture, "bad.csv"),
  ]);
  await expect(
    window.getByRole("heading", { name: "4 samples imported", exact: true }),
  ).toBeVisible();
  await expect(window.locator(".sample-import-results")).toContainText(
    "Dataset 3",
  );
  await expect(window.locator(".import-message.error")).toHaveCount(2);
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-import-result.png"),
  });
  await finish("4 samples imported");
  const workspaceId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  const endpoint = "/workspaces/" + workspaceId;
  let doc = await get(endpoint);
  assert.equal(doc.samples.length, 4);
  assert.deepEqual(
    doc.samples.slice(0, 3).map((sample) => sample.event_count),
    [2, 3, 3],
  );
  assert.equal(doc.samples[0].metadata.com, "Budget $100 / sample/");
  assert.equal(doc.samples[0].channels[0].range, 500);
  assert.equal(doc.samples[0].channels[2].range, 20);
  assert.equal(doc.compensations.length, 1);
  const firstRecord = await get(endpoint + "/imports/" + firstSession.id);
  assert.equal(firstRecord.status, "succeeded");
  assert.equal(firstRecord.errors.length, 2);
  assert.equal(firstRecord.datasets.length, 4);

  // Rename the exact same source bytes, then explicitly request new acquisitions.
  const renamed = path.join(fixture, "renamed-chain.fcs");
  copyFileSync(path.join(fixture, "three-datasets.fcs"), renamed);
  const duplicateSession = await choose([renamed]);
  await expect(
    window.getByRole("heading", { name: "0 samples imported", exact: true }),
  ).toBeVisible();
  await expect(window.locator(".import-message")).toHaveCount(3);
  const duplicate = await get(endpoint + "/imports/" + duplicateSession.id);
  assert.equal(
    duplicate.datasets.filter((dataset) => dataset.skipped).length,
    3,
  );
  assert.equal((await get(endpoint)).revision, doc.revision);
  await window
    .getByRole("button", { name: "Import selection again", exact: true })
    .click();
  await finish("3 samples imported");
  doc = await get(endpoint);
  assert.equal(doc.samples.length, 7);
  assert.equal(doc.compensations.length, 2);
  await window.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(window.locator(".sidebar-bottom")).toContainText("4 samples");
  const afterUndo = await get(endpoint);
  assert.deepEqual(
    afterUndo.samples.map((sample) => sample.id),
    firstRecord.datasets.map((d) => d.sample_id),
  );
  await window.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(window.locator(".sidebar-bottom")).toContainText("7 samples");
  doc = await get(endpoint);

  // A genuine large CSV gives the user time to inspect progress and cancel its event pass.
  const large = path.join(fixture, "cancel-large.csv");
  const handle = createWriteStream(large);
  handle.write("X,Y,Z\n");
  const rows = "1,2,3\n".repeat(8192);
  const eventCount = 3_000_000;
  for (let index = 0; index < eventCount; index += 8192) {
    const data =
      index + 8192 <= eventCount ? rows : "1,2,3\n".repeat(eventCount - index);
    if (!handle.write(data)) await once(handle, "drain");
  }
  handle.end();
  await once(handle, "finish");
  const cancelSession = await choose([large]);
  let observed;
  for (let tries = 0; tries < 150; tries++) {
    observed = await get(endpoint + "/imports/" + cancelSession.id);
    if (observed.stage === "Reading events" && observed.events_read > 0) break;
    if (observed.status === "succeeded")
      throw new Error("Large import finished before cancellation");
    await sleep(75);
  }
  assert.equal(observed.status, "reading");
  assert.equal(observed.stage, "Reading events");
  assert.ok(observed.events_read > 0 && observed.events_read < eventCount);
  await expect(
    window.getByRole("progressbar", { name: "Sample import progress" }),
  ).toBeVisible();
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-import-progress.png"),
  });
  await window
    .getByRole("button", { name: "Cancel import", exact: true })
    .click();
  await finish("Import cancelled");
  const cancelled = await get(endpoint + "/imports/" + cancelSession.id);
  assert.equal(cancelled.status, "cancelled");
  assert.equal(cancelled.imported, 0);
  const afterCancel = await get(endpoint);
  assert.equal(afterCancel.revision, doc.revision);
  assert.deepEqual(afterCancel.samples, doc.samples);
  const importFolder = path.join(profile, "data/imports", cancelSession.id);
  assert.deepEqual(readdirSync(importFolder), ["state.json"]);

  await window
    .getByRole("button", { name: /^three-datasets.fcs · Dataset 1/ })
    .first()
    .click();
  await window.locator('canvas[data-ready="true"]').waitFor();
  const fcsExport = await nativeDownload("native-population.fcs", () =>
    window.getByRole("button", { name: "Export events", exact: true }).click(),
  );
  assert.ok(
    readFileSync(fcsExport).subarray(0, 6).equals(Buffer.from("FCS3.1")),
  );
  const archive = await nativeDownload("native-import.cytoforge", () =>
    window.getByRole("button", { name: "Save project", exact: true }).click(),
  );
  const oldPort = new URL(window.url()).port;
  await application.close();
  application = await launch();
  await connect();
  await expect(window.locator(".sidebar-bottom")).toContainText("7 samples");
  const newPort = new URL(window.url()).port;
  assert.notEqual(newPort, oldPort);
  const reopened = await get(endpoint);
  assert.deepEqual(reopened.samples, doc.samples);
  assert.equal(
    (await get(endpoint + "/imports/" + cancelSession.id)).status,
    "cancelled",
  );

  // Restore through the desktop's project chooser, then ensure dataset identities still deduplicate.
  await window
    .getByLabel("Open CytoForge project archive")
    .setInputFiles(archive);
  await expect
    .poll(() => window.evaluate(() => window.cytoforgeDesktop.getWorkspace()))
    .not.toBe(workspaceId);
  await expect(window.locator(".sidebar-bottom")).toContainText("7 samples");
  const restoredId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  assert.notEqual(restoredId, workspaceId);
  const restored = await get("/workspaces/" + restoredId);
  await choose([path.join(fixture, "three-datasets.fcs")]);
  await finish("0 samples imported");
  assert.equal((await get("/workspaces/" + restoredId)).samples.length, 7);
  assert.equal(restored.samples[0].metadata.cytoforge_dataset_count, "3");
  const zeroSession = await choose([
    path.join(fixture, "zero-event-ASCII.fcs"),
  ]);
  let zero;
  for (let tries = 0; tries < 100; tries++) {
    zero = await get(
      "/workspaces/" + restoredId + "/imports/" + zeroSession.id,
    );
    if (zero.status === "succeeded") break;
    await sleep(75);
  }
  assert.deepEqual(zero.errors, []);
  assert.equal(zero.imported, 1);
  await finish("1 sample imported");
  const emptySample = (await get("/workspaces/" + restoredId)).samples.at(-1);
  assert.equal(emptySample.event_count, 0);
  assert.equal(emptySample.metadata.datatype, "A");
  assert.deepEqual(errors, []);
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-import-restored.png"),
  });
  writeFileSync(
    evidencePath,
    JSON.stringify(
      {
        status: "passed",
        verified_at: new Date().toISOString(),
        packaged,
        binary: binary || null,
        workspace_id: workspaceId,
        restored_workspace_id: restoredId,
        profile,
        first_import: firstRecord,
        zero_event_ascii: { record: zero, sample: emptySample },
        duplicate_import: duplicate,
        cancellation: {
          observed,
          final_status: cancelled.status,
          revision: afterCancel.revision,
        },
        reopened_acquisitions: reopened.samples.length,
        ports: [oldPort, newPort],
        fcs_export: {
          path: fcsExport,
          sha256: createHash("sha256")
            .update(readFileSync(fcsExport))
            .digest("hex"),
        },
        project_export: archive,
        page_errors: errors,
        workflow: [
          "native file chooser",
          "three independent datasets",
          "gain/log/time preprocessing",
          "literal metadata",
          "mixed errors",
          "renamed duplicates",
          "intentional reimport",
          "undo/redo",
          "live event progress",
          "cooperative cancellation without mutation",
          "native FCS and project downloads",
          "restart with a new engine port",
          "portable project restore and duplicate identity",
        ],
      },
      null,
      2,
    ) + "\n",
  );
  process.stdout.write("Native desktop import workflow passed\n");
} finally {
  clearTimeout(timeout);
  await application?.close().catch(() => {});
}
