// Native streamed saving and scientific reopening, including independent plot windows.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", `desktop-event-export-${Date.now()}`);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_EVENT_EXPORT_EVIDENCE ||
  "artifacts/desktop-event-export-source.json";
assert(path.resolve(output).startsWith(root + path.sep));
mkdirSync(profile, { recursive: true });
mkdirSync("artifacts/screenshots", { recursive: true });
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
if (binary)
  evidence.binary_sha256 = createHash("sha256")
    .update(readFileSync(binary))
    .digest("hex");
const errors = [];
let application, main, workspaceId;
const uid = () => randomUUID().replaceAll("-", "");
const save = () =>
  writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n");
const passed = (value) => {
  evidence.checks.push(value);
  save();
};
async function launch() {
  application = await electron.launch({
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
  const switches = await application.evaluate(({ app }) => ({
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    headless: app.commandLine.hasSwitch("headless"),
    disable_gpu: app.commandLine.hasSwitch("disable-gpu"),
    packaged: app.isPackaged,
  }));
  assert.equal(switches.packaged, !!binary);
  assert.equal(
    switches.no_sandbox,
    process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
  );
  assert(switches.headless && switches.disable_gpu);
  evidence.sandbox_exception = switches.no_sandbox;
  evidence.native_command_line_switches = switches;
  evidence.scope =
    "Linux x64 headless native desktop with hardware GPU disabled";
  main = await application.firstWindow();
  main.on("pageerror", (error) => errors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  return main.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        method,
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      const result = await response.json();
      if (method !== "GET" && result.id && Array.isArray(result.samples))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(result.id);
      return result;
    },
    { route, body, method },
  );
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
const exportDialog = () =>
  main.getByRole("dialog", { name: "Export events", exact: true });
async function openExport(values = "raw", format = "fcs") {
  await main
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  await exportDialog()
    .getByLabel("Export measurement values")
    .selectOption(values);
  await exportDialog().getByLabel("Event export format").selectOption(format);
}
async function prepare() {
  await exportDialog()
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  await expect(
    exportDialog().getByRole("button", {
      name: "Save event file",
      exact: true,
    }),
  ).toBeEnabled();
}
async function saveFile(filename, cancel = false, page = main) {
  await application.evaluate(
    ({ session }, { filename, cancel }) =>
      session.defaultSession.once("will-download", (_event, item) => {
        if (cancel) setImmediate(() => item.cancel());
        else item.setSavePath(filename);
      }),
    { filename, cancel },
  );
  const dialog = page.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Save event file", exact: true })
    .click();
  if (cancel)
    await expect(
      dialog.getByRole("button", {
        name: "Save event file",
        exact: true,
      }),
    ).toBeEnabled();
  else await expect(dialog).toHaveCount(0);
}
async function importFiles(files) {
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(files);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
}
async function closeDesktop() {
  const closed = application.waitForEvent("close");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((w) => !w.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await closed;
}
const deadline = setTimeout(() => application?.process()?.kill(), 300000);
save();
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native event export truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const files = [
    path.join(profile, "First.csv"),
    path.join(profile, "Second.csv"),
  ];
  const first = [
    [0.12500000000001, 200.00000000000003],
    [2.00000000000001, 4.00000000000001],
    [4.00000000000001, 2.00000000000001],
    [8.00000000000001, 0],
  ];
  const second = [
    [6.00000000000001, 3.00000000000001],
    [8.00000000000001, 5.00000000000001],
    [2.00000000000001, -1.00000000000001],
    [10.00000000000001, 7.00000000000001],
  ];
  writeFileSync(
    files[0],
    "X,Y\n" + first.map((r) => r.join(",")).join("\n") + "\n",
  );
  writeFileSync(
    files[1],
    "Y,X\n" + second.map((r) => r.join(",")).join("\n") + "\n",
  );
  await importFiles(files);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  for (const [index, sample] of doc.samples.entries()) {
    doc = await request(
      `/workspaces/${workspaceId}/samples/${sample.id}`,
      {
        revision: doc.revision,
        name: sample.name,
        tags: { donor: index ? "B" : "A" },
      },
      "PATCH",
    );
    doc = await request(`/workspaces/${workspaceId}/compensations`, {
      revision: doc.revision,
      sample_ids: [sample.id],
      compensation: {
        id: uid(),
        name: "Known basis",
        detectors: ["X", "Y"],
        matrix: [
          [2, 0],
          [0, 4],
        ],
      },
    });
  }
  const sources = doc.samples;
  const base = `/workspaces/${workspaceId}/concatenations`;
  let merge = await request(base, {
    revision: doc.revision,
    name: "Precise merged",
    inputs: sources.map((s) => ({ sample_id: s.id })),
    parameters: ["X", "Y"].map((name) => ({
      name,
      sources: Object.fromEntries(sources.map((s) => [s.id, name])),
    })),
    keywords: ["donor"],
  });
  await expect
    .poll(async () => (merge = await request(`${base}/${merge.id}`)).status)
    .toBe("ready");
  doc = await request(`${base}/${merge.id}/apply`, {
    revision: doc.revision,
    review_hash: merge.review_hash,
  });
  const merged = doc.samples.at(-1);
  doc = await request(`/workspaces/${workspaceId}/gates`, {
    revision: doc.revision,
    gate: {
      sample_id: merged.id,
      name: "Two events per source",
      kind: "range",
      x: "CF_EventID",
      bounds: [0.5, 2.5],
      x_transform: { kind: "linear" },
    },
  });
  await main.getByLabel("Plot sample", { exact: true }).selectOption(merged.id);
  await main
    .getByLabel("Open child population", { exact: true })
    .selectOption(doc.gates.at(-1).id);
  const before = await documentFor();
  await openExport();
  await expect(exportDialog().getByLabel("Export population")).toHaveValue(
    doc.gates.at(-1).id,
  );
  await exportDialog()
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  assert.deepEqual(await documentFor(), before);
  passed(
    "Native export starts from the plotted population; cancelling configuration leaves workspace history unchanged",
  );

  await openExport();
  await prepare();
  await expect(exportDialog()).toContainText("4 events");
  await expect(exportDialog()).toContainText("Correction matrix preserved");
  await expect(exportDialog()).toContainText("Exact event origins");
  assert.deepEqual(await documentFor(), before);
  await main.screenshot({
    path: "artifacts/screenshots/event-export-review.png",
    fullPage: true,
  });
  const rawFile = path.join(profile, "Reopened.fcs");
  await saveFile(rawFile, true);
  assert.deepEqual(await documentFor(), before);
  passed(
    "Preparing an FCS file and cancelling the native save leaves the reviewed file available without scientific writes",
  );
  await saveFile(rawFile);
  const fcs = readFileSync(rawFile);
  const dataStart = Number(fcs.subarray(26, 34).toString());
  const expected = [
    [...first[1], 1, 1, 1],
    [...first[2], 1, 2, 1],
    [second[1][1], second[1][0], 2, 1, 2],
    [second[2][1], second[2][0], 2, 2, 2],
  ];
  assert.equal(fcs.subarray(0, 6).toString(), "FCS3.1");
  for (const [row, values] of expected.entries())
    for (const [column, value] of values.entries())
      assert.equal(fcs.readDoubleLE(dataStart + (row * 5 + column) * 8), value);
  assert.deepEqual(await documentFor(), before);
  passed(
    "Main-process authenticated streaming saves all selected 64-bit measurements and integer origins exactly; binary DATA is checked independently",
  );

  await importFiles([rawFile]);
  doc = await documentFor();
  const reopened = doc.samples.at(-1);
  assert.equal(reopened.event_count, 4);
  assert(reopened.compensation_id && reopened.event_export.values === "raw");
  assert.deepEqual(
    reopened.concatenation.sources.map((s) => s.count),
    [2, 2],
  );
  assert.deepEqual(
    reopened.concatenation.keywords,
    merged.concatenation.keywords,
  );
  const origins = await request(
    `/workspaces/${workspaceId}/samples/${reopened.id}/origins`,
  );
  assert.deepEqual(
    origins.rows.map((r) => [r.source_index, r.source_event_id]),
    [
      [1, "1"],
      [1, "2"],
      [2, "1"],
      [2, "2"],
    ],
  );
  passed(
    "Reopening the saved native FCS restores correction, source snapshots, category codebooks and four exact acquisition identities",
  );

  await main.getByLabel("Plot sample", { exact: true }).selectOption(merged.id);
  await openExport("compensated", "csv");
  await exportDialog()
    .getByLabel("Export population")
    .selectOption(doc.gates.at(-1).id);
  await prepare();
  const csvFile = path.join(profile, "Corrected.csv");
  await saveFile(csvFile);
  const rows = readFileSync(csvFile, "utf8")
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((r) => r.split(",").map(Number));
  assert.deepEqual(
    rows,
    expected.map((r) => [r[0] / 2, r[1] / 4, ...r.slice(2)]),
  );
  passed(
    "Native CSV saving preserves full numeric precision and materializes compensation once while keeping origin and category columns integral",
  );

  await openExport();
  await prepare();
  doc = await documentFor();
  doc = await request(
    `/workspaces/${workspaceId}/samples/${sources[0].id}`,
    {
      revision: doc.revision,
      name: sources[0].name,
      tags: { donor: "A", changed: "yes" },
    },
    "PATCH",
  );
  await expect(
    exportDialog().getByRole("button", {
      name: "Save event file",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(exportDialog().getByRole("alert")).toContainText(
    "workspace changed",
  );
  await exportDialog()
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  passed(
    "A concurrent native workspace edit invalidates the prepared export and blocks saving a stale population snapshot",
  );
  await assert.rejects(
    () =>
      main.evaluate(
        async ({ workspaceId }) =>
          globalThis.cytoforgeDesktop.saveEventExport({
            workspaceId,
            exportId: "../outside",
            url: "http://example.com",
          }),
        { workspaceId },
      ),
    /Invalid desktop event export descriptor/,
  );
  passed(
    "The desktop save bridge rejects arbitrary URLs, extra descriptor fields and path-like identifiers",
  );

  await main
    .getByLabel("Plot sample", { exact: true })
    .selectOption(reopened.id);
  await main.getByRole("button", { name: "Scatter", exact: true }).click();
  await main.getByLabel("X axis channel", { exact: true }).selectOption("X");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("Y");
  await main
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .waitFor();
  const mainView = await main.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  const popup = await opened;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup.waitForLoadState("networkidle");
  await popup
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .waitFor();
  await popup.getByLabel("Y axis channel").selectOption("CF_Source");
  await expect
    .poll(() =>
      popup.evaluate(
        async () =>
          (await globalThis.cytoforgeDesktop.getPlotSession()).state.y,
      ),
    )
    .toBe("CF_Source");
  assert.deepEqual(
    await main.evaluate(
      async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
    ),
    mainView,
  );
  await popup
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const popupExport = popup.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await popupExport.getByLabel("Event export format").selectOption("csv");
  await popupExport
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  await expect(
    popupExport.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeEnabled();
  const popupFile = path.join(profile, "Popup.csv");
  await saveFile(popupFile, false, popup);
  const popupValues = readFileSync(popupFile, "utf8")
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .map((r) => r.split(",").map(Number));
  assert.deepEqual(popupValues, expected);
  passed(
    "A reopened FCS population supports independent native plotting and streamed exports without changing the main plot",
  );
  await openExport();
  await prepare();
  const saved = await documentFor();
  await closeDesktop();
  await launch();
  assert.deepEqual(await documentFor(), saved);
  assert.deepEqual(
    (await request(`/workspaces/${workspaceId}/samples/${reopened.id}/origins`))
      .rows,
    origins.rows,
  );
  assert(
    (await application.windows()).some((w) => w.url().includes("plotWindow=")),
  );
  passed(
    "A real desktop restart preserves reopened FCS history, exact origins and the independent plot window",
  );
  await main
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  await exportDialog()
    .getByRole("button", { name: "Resume FCS export", exact: true })
    .click();
  await expect(
    exportDialog().getByRole("button", {
      name: "Save event file",
      exact: true,
    }),
  ).toBeEnabled();
  const recoveredFile = path.join(profile, "Recovered.fcs");
  await saveFile(recoveredFile);
  const recovered = readFileSync(recoveredFile);
  const recoveredStart = Number(recovered.subarray(26, 34).toString());
  for (const [row, values] of expected.entries())
    for (const [column, value] of values.entries())
      assert.equal(
        recovered.readDoubleLE(recoveredStart + (row * 5 + column) * 8),
        value,
      );
  assert.deepEqual(await documentFor(), saved);
  passed(
    "The native export dialog resumes a prepared file after restart, saves its exact bytes, and clears the preparation without scientific writes",
  );
  assert.deepEqual(errors, []);
  evidence.renderer_errors = errors;
  evidence.workspace_id = workspaceId;
  evidence.reopened_sample_id = reopened.id;
  evidence.exact_origin_rows = origins.rows;
  evidence.status = "passed";
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack || String(error);
  evidence.renderer_errors = errors;
  process.exitCode = 1;
  if (main && !main.isClosed())
    await main
      .screenshot({
        path: "artifacts/screenshots/event-export-failure.png",
        fullPage: true,
      })
      .catch(() => {});
} finally {
  clearTimeout(deadline);
  save();
  if (application) await application.close().catch(() => {});
  process.stdout.write(
    JSON.stringify({
      status: evidence.status,
      checks: evidence.checks.length,
      evidence: output,
    }) + "\n",
  );
}
