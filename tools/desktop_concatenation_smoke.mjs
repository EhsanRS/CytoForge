// Native sample merging, scientific values, origins and independent plot windows.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", `desktop-concatenation-${Date.now()}`);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_CONCATENATION_EVIDENCE ||
  "artifacts/desktop-concatenation-source.json";
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
let application, main, popup, workspaceId, sources, merged;
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
const dialogFor = () =>
  main.getByRole("dialog", { name: "Concatenate populations", exact: true });
async function selectSources() {
  await main.getByRole("button", { name: "Samples", exact: true }).click();
  for (const sample of sources)
    await main.getByLabel(`Select ${sample.name}`, { exact: true }).check();
}
async function open() {
  await main
    .getByRole("button", { name: "Concatenate populations", exact: true })
    .click();
  await expect(dialogFor()).toBeVisible();
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
const deadline = setTimeout(() => application?.process().kill(), 300000);
save();
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native concatenation truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const files = [
    path.join(profile, "Tube-1.csv"),
    path.join(profile, "Tube-2.csv"),
  ];
  writeFileSync(files[0], "X,Y\n0,2\n2,4\n4,2\n8,0\n");
  writeFileSync(files[1], "Y,X\n6,3\n8,5\n2,-1\n10,7\n");
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(files);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  for (const [index, sample] of doc.samples.entries()) {
    doc = await request(
      `/workspaces/${workspaceId}/samples/${sample.id}`,
      {
        revision: doc.revision,
        name: `Tube ${index + 1}`,
        tags: { donor: index ? "B" : "A" },
      },
      "PATCH",
    );
    doc = await request(`/workspaces/${workspaceId}/compensations`, {
      revision: doc.revision,
      sample_ids: [sample.id],
      compensation: {
        id: uid(),
        name: `Truth ${index + 1}`,
        detectors: ["X", "Y"],
        matrix: index
          ? [
              [1, 0],
              [0, 2],
            ]
          : [
              [2, 0],
              [0, 4],
            ],
      },
    });
    doc = await request(`/workspaces/${workspaceId}/gates`, {
      revision: doc.revision,
      gate: {
        sample_id: sample.id,
        name: "Chosen",
        kind: "hyperrectangle",
        dimensions: [
          {
            channel: "X",
            compensation_ref: "uncompensated",
            minimum: 1.5,
            maximum: 6,
            transform: { kind: "linear" },
          },
        ],
      },
    });
  }
  sources = doc.samples;
  await expect(
    main.getByRole("button", {
      name: `Revision ${doc.revision} · History`,
      exact: true,
    }),
  ).toBeVisible();
  await selectSources();
  const before = await documentFor();
  await open();
  await dialogFor()
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  assert.deepEqual(await documentFor(), before);
  passed(
    "Cancelling the native configuration leaves acquired samples, gates and scientific history unchanged",
  );

  await open();
  await dialogFor()
    .getByRole("button", { name: "Prepare merge", exact: true })
    .click();
  await expect(dialogFor().getByRole("alert")).toContainText("matrices differ");
  assert.deepEqual(await documentFor(), before);
  passed(
    "Raw sources with different matrices require an explicit choice and cannot silently receive the first matrix",
  );
  await dialogFor()
    .getByLabel("Concatenation values", { exact: true })
    .selectOption("compensated");
  await dialogFor()
    .getByLabel("Output sample name", { exact: true })
    .fill("Known merged population");
  await dialogFor().getByLabel("Output parameter 1", { exact: true }).fill("");
  const parameter = dialogFor().getByLabel("Output parameter 1", {
    exact: true,
  });
  await parameter.pressSequentially("Signal X");
  await expect(parameter).toHaveValue("Signal X");
  await expect(parameter).toBeFocused();
  for (const sample of sources)
    await dialogFor()
      .getByLabel(`Population for ${sample.name}`, { exact: true })
      .selectOption(doc.gates.find((g) => g.sample_id === sample.id).id);
  await dialogFor()
    .getByLabel("Additional keyword columns", { exact: true })
    .selectOption(["donor"]);
  await dialogFor()
    .getByRole("button", { name: "Prepare merge", exact: true })
    .click();
  await expect(
    dialogFor().getByRole("button", { name: "Create samples", exact: true }),
  ).toBeEnabled();
  await expect(dialogFor()).toContainText("Known merged population");
  assert.deepEqual(await documentFor(), before);
  await main.screenshot({
    path: "artifacts/screenshots/concatenation-review.png",
    fullPage: true,
  });
  passed(
    "Native population selectors, reordered parameter mapping and compensated values prepare all four retained events for review without adding samples",
  );
  await dialogFor()
    .getByRole("button", { name: "Create samples", exact: true })
    .click();
  await expect(dialogFor()).toHaveCount(0);
  doc = await documentFor();
  assert.equal(doc.revision, before.revision + 1);
  assert.deepEqual(doc.samples.slice(0, sources.length), sources);
  merged = doc.samples.at(-1);
  assert.equal(merged.event_count, 4);
  assert.equal(merged.compensation_id, null);
  assert.deepEqual(
    merged.channels.map((c) => c.name),
    ["Signal X", "Y", "CF_Source", "CF_EventID", "CF_donor"],
  );
  const pointData = await request(
    `/workspaces/${workspaceId}/samples/${merged.id}/plot?x=Signal%20X&y=Y&mode=scatter&bins=32&x_transform=${encodeURIComponent('{"kind":"linear"}')}&y_transform=${encodeURIComponent('{"kind":"linear"}')}`,
  );
  assert.deepEqual(
    [...pointData.points].sort((a, b) => a[0] - b[0]),
    [
      [1, 1],
      [2, 0.5],
      [3, 3],
      [5, 4],
    ],
  );
  passed(
    "One atomic native Create action materializes independently known compensated measurements while keeping both acquired sources byte-identical",
  );

  await main
    .getByRole("button", { name: `Origins for ${merged.name}`, exact: true })
    .click();
  const origins = main.getByRole("dialog", {
    name: "Merged event origins",
    exact: true,
  });
  await expect(origins.locator(".concat-origins tbody tr")).toHaveCount(4);
  const identities = await request(
    `/workspaces/${workspaceId}/samples/${merged.id}/origins`,
  );
  assert.deepEqual(
    identities.rows.map((r) => [r.source_index, r.source_event_id]),
    [
      [1, "1"],
      [1, "2"],
      [2, "0"],
      [2, "1"],
    ],
  );
  await expect(origins).toContainText("CF_donor: 1 = A; 2 = B");
  const csvPath = path.join(profile, "event-origins.csv");
  await application.evaluate(
    ({ session }, filename) =>
      session.defaultSession.once("will-download", (_event, item) =>
        item.setSavePath(filename),
      ),
    csvPath,
  );
  await origins
    .getByRole("button", { name: "Export event origins", exact: true })
    .click();
  await expect.poll(() => existsSync(csvPath)).toBe(true);
  await expect
    .poll(() => readFileSync(csvPath, "utf8").trim().split(/\r?\n/).length)
    .toBe(5);
  assert(readFileSync(csvPath, "utf8").includes(sources[1].id + ",Tube 2,1"));
  await main.screenshot({
    path: "artifacts/screenshots/concatenation-event-origins.png",
    fullPage: true,
  });
  await origins.getByRole("button", { name: "Close", exact: true }).click();
  passed(
    "The native origin inspector and streamed CSV preserve exact original event IDs and the keyword codebook",
  );

  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    main.getByRole("button", {
      name: `Origins for ${merged.name}`,
      exact: true,
    }),
  ).toHaveCount(0);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(
    main.getByRole("button", {
      name: `Origins for ${merged.name}`,
      exact: true,
    }),
  ).toBeVisible();
  assert.deepEqual((await documentFor()).samples.at(-1), merged);
  passed(
    "Native Undo and Redo remove and restore the complete batch, including immutable event lineage",
  );

  await selectSources();
  // Remove the automatically selected output from the next merge selection.
  await main.getByLabel(`Select ${merged.name}`, { exact: true }).uncheck();
  await open();
  await dialogFor()
    .getByLabel("Concatenation values", { exact: true })
    .selectOption("compensated");
  await dialogFor()
    .getByRole("button", { name: "Prepare merge", exact: true })
    .click();
  await expect(
    dialogFor().getByRole("button", { name: "Create samples", exact: true }),
  ).toBeEnabled();
  doc = await documentFor();
  doc = await request(
    `/workspaces/${workspaceId}/samples/${sources[0].id}`,
    {
      revision: doc.revision,
      name: sources[0].name,
      tags: { donor: "A", review_test: "changed" },
    },
    "PATCH",
  );
  await expect(
    dialogFor().getByRole("button", { name: "Create samples", exact: true }),
  ).toBeDisabled();
  await expect(dialogFor().getByRole("alert")).toContainText(
    "workspace changed",
  );
  await dialogFor()
    .getByRole("button", { name: "Discard / cancel", exact: true })
    .click();
  assert.equal((await documentFor()).samples.length, sources.length + 1);
  passed(
    "An edit from another native action invalidates the prepared merge and prevents a stale workspace overwrite",
  );

  await main.getByRole("button", { name: merged.name, exact: true }).click();
  await main
    .getByLabel("X axis channel", { exact: true })
    .selectOption("Signal X");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("Y");
  await main.getByRole("button", { name: "Scatter", exact: true }).click();
  await main
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .waitFor();
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup.waitForLoadState("networkidle");
  await popup
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .waitFor();
  const view = await popup.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
  assert.equal(view.sampleId, merged.id);
  assert.equal(view.x, "Signal X");
  const mainView = await main.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
  await popup
    .getByLabel("Y axis channel", { exact: true })
    .selectOption("CF_Source");
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
  passed(
    "Merged populations open in independent native plot windows; changing a popup axis leaves the main plot intact",
  );
  const saved = await documentFor();
  await closeDesktop();
  await launch();
  assert.deepEqual(await documentFor(), saved);
  assert.deepEqual(
    (await request(`/workspaces/${workspaceId}/samples/${merged.id}/origins`))
      .rows,
    identities.rows,
  );
  const windows = await application.windows();
  assert(windows.some((w) => w.url().includes("plotWindow=")));
  passed(
    "A real desktop restart restores the merged sample, exact event origins, scientific snapshot and native popup",
  );
  assert.deepEqual(errors, []);
  evidence.renderer_errors = errors;
  evidence.workspace_id = workspaceId;
  evidence.merged_sample_id = merged.id;
  evidence.exact_origin_rows = identities.rows;
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
        path: "artifacts/screenshots/concatenation-failure.png",
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
      output,
    }) + "\n",
  );
}
