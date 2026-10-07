// Native panel harmonization over independently specified, reordered FCS detector data.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", `desktop-aliases-${Date.now()}`);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_ALIASES_EVIDENCE ||
  "artifacts/desktop-aliases-source.json";
assert(path.resolve(output).startsWith(root + path.sep));
mkdirSync(profile, { recursive: true });
mkdirSync("artifacts/screenshots", { recursive: true });
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
const sha = (data) => createHash("sha256").update(data).digest("hex");
if (binary) evidence.binary_sha256 = sha(readFileSync(binary));
const save = () =>
  writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n");
const passed = (check) => {
  evidence.checks.push(check);
  save();
};
const errors = [];
const uid = () => randomUUID().replaceAll("-", "");
let application, main, workspaceId;

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
    packaged: app.isPackaged,
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    headless: app.commandLine.hasSwitch("headless"),
    disable_gpu: app.commandLine.hasSwitch("disable-gpu"),
  }));
  assert.equal(switches.packaged, !!binary);
  assert.equal(
    switches.no_sandbox,
    process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
  );
  assert(switches.headless && switches.disable_gpu);
  evidence.native_command_line_switches = switches;
  evidence.sandbox_exception = switches.no_sandbox;
  evidence.scope =
    "Linux x64 native desktop; explicit isolated headless test with hardware GPU disabled";
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
      const value = await response.json();
      if (method !== "GET" && value.id && Array.isArray(value.samples))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(value.id);
      return value;
    },
    { route, body, method },
  );
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
const dialog = () =>
  main.getByRole("dialog", { name: "Harmonize panel", exact: true });
async function openMapping() {
  await main.getByRole("button", { name: "Samples", exact: true }).click();
  await main.getByLabel("Select all visible samples").check();
  await main
    .getByRole("button", { name: "Harmonize panel", exact: true })
    .click();
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

function fcs(names, labels, rows, matrix) {
  const fields = {
    $BEGINANALYSIS: "0",
    $ENDANALYSIS: "0",
    $BEGINSTEXT: "0",
    $ENDSTEXT: "0",
    $BYTEORD: "1,2,3,4",
    $DATATYPE: "D",
    $MODE: "L",
    $NEXTDATA: "0",
    $PAR: String(names.length),
    $TOT: String(rows.length),
    $BEGINDATA: "0",
    $ENDDATA: "0",
    $SPILLOVER: [2, ...names.slice(0, 2), ...matrix.flat()].join(","),
  };
  for (let i = 0; i < names.length; i++) {
    Object.assign(fields, {
      [`$P${i + 1}B`]: "64",
      [`$P${i + 1}E`]: "0,0",
      [`$P${i + 1}G`]: "1",
      [`$P${i + 1}N`]: names[i],
      [`$P${i + 1}S`]: labels[i],
      [`$P${i + 1}R`]: "100",
    });
  }
  let text, start;
  for (let i = 0; i < 12; i++) {
    text = Buffer.from("|" + Object.entries(fields).flat().join("|") + "|");
    start = Math.ceil((256 + text.length) / 8) * 8;
    const end = start + rows.length * names.length * 8 - 1;
    if (fields.$BEGINDATA === String(start) && fields.$ENDDATA === String(end))
      break;
    fields.$BEGINDATA = String(start);
    fields.$ENDDATA = String(end);
  }
  const bytes = Buffer.alloc(start + rows.length * names.length * 8);
  const offset = (n) => String(n).padStart(8, " ");
  bytes.write(
    "FCS3.1    " +
      [256, 255 + text.length, start, bytes.length - 1, 0, 0]
        .map(offset)
        .join(""),
  );
  text.copy(bytes, 256);
  rows.flat().forEach((value, i) => bytes.writeDoubleLE(value, start + i * 8));
  return bytes;
}
const truth = [
  [1, 2, 9],
  [3, 4, 8],
  [5, 6, 7],
  [7, 8, 6],
];
async function csv(sampleId, corrected = true) {
  const value = await main.evaluate(
    async ({ workspaceId, sampleId, corrected }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch(
        `/api/workspaces/${workspaceId}/samples/${sampleId}/export?format=csv&compensated=${corrected}`,
        { headers: { "X-CytoForge-Token": token } },
      );
      if (!response.ok) throw new Error(await response.text());
      return response.text();
    },
    { workspaceId, sampleId, corrected },
  );
  const lines = value.trim().split(/\r?\n/);
  return {
    names: lines.shift().split(","),
    rows: lines.map((line) => line.split(",").map(Number)),
  };
}
const view = (page) =>
  page.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
const readyPlot = (page) =>
  page.locator('.primary-plot canvas[data-ready="true"]').first().waitFor();

const deadline = setTimeout(() => application?.process()?.kill(), 450000);
save();
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native alias detector truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const paths = [
    path.join(profile, "Panel A.fcs"),
    path.join(profile, "Panel B.fcs"),
  ];
  writeFileSync(
    paths[0],
    fcs(["FL1", "FL2", "FL3"], ["CD3", "CD4", "Background"], truth, [
      [2, 0],
      [0, 4],
    ]),
  );
  writeFileSync(
    paths[1],
    fcs(
      ["B2", "B1", "B3"],
      ["CD4", "CD3", "Background"],
      truth.map(([x, y, z]) => [y, x, z]),
      [
        [4, 0],
        [0, 2],
      ],
    ),
  );
  await importFiles(paths);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  assert.equal(doc.samples.length, 2);
  const sourceIds = doc.samples.map((s) => s.id);
  const rawPaths = sourceIds.map((id) =>
    path.join(profile, "data", "events", workspaceId, `${id}.npy`),
  );
  const rawHashes = rawPaths.map((p) => sha(readFileSync(p)));
  assert.deepEqual(
    rawHashes,
    doc.samples.map((s) => s.sha256),
  );
  const original = doc;
  passed(
    "Independent float64 FCS detector fixtures reopen with reordered panels and their distinct original spillover bindings",
  );

  await openMapping();
  await dialog()
    .getByRole("button", { name: "Suggest from marker labels", exact: true })
    .click();
  await expect(
    dialog().locator('input[aria-label^="Shared name"]'),
  ).toHaveCount(3);
  await dialog()
    .getByRole("button", {
      name: "Remove shared parameter Background",
      exact: true,
    })
    .click();
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual(await documentFor(), original);
  passed(
    "Exact marker-label suggestions show explicit per-sample sources; cancelling writes no workspace, event data or matrix state",
  );

  await openMapping();
  await dialog()
    .getByRole("button", { name: "Suggest from marker labels", exact: true })
    .click();
  await dialog()
    .getByRole("button", {
      name: "Remove shared parameter Background",
      exact: true,
    })
    .click();
  await dialog().locator('input[aria-label^="Shared name"]').first().fill("B2");
  await dialog()
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog().getByRole("alert")).toContainText("B2 already exists");
  assert.deepEqual(await documentFor(), original);
  await dialog()
    .locator('input[aria-label^="Shared name"]')
    .first()
    .fill("CD3");
  await dialog()
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog().getByLabel("Channel mapping review")).toContainText(
    "B1",
  );
  assert.deepEqual(await documentFor(), original);
  passed(
    "A collision is rejected for the entire cohort; editing and reviewing a valid mapping still changes no scientific data",
  );
  await main.screenshot({
    path: "artifacts/screenshots/desktop-aliases-mapping-review.png",
  });
  await dialog()
    .getByRole("button", { name: "Apply channel aliases", exact: true })
    .click();
  await expect(dialog()).toHaveCount(0);
  doc = await documentFor();
  assert.equal(doc.revision, original.revision + 1);
  assert.deepEqual(
    doc.samples.map((s) => s.aliases),
    [
      { CD3: "FL1", CD4: "FL2" },
      { CD3: "B1", CD4: "B2" },
    ],
  );
  assert.deepEqual(doc.compensations, original.compensations);
  assert.deepEqual(
    rawPaths.map((p) => sha(readFileSync(p))),
    rawHashes,
  );
  passed(
    "Applying creates two harmonized sample panels in one atomic history revision without changing any acquired NPY bytes or detector bindings",
  );

  for (const sample of doc.samples) {
    for (const corrected of [false, true]) {
      const exported = await csv(sample.id, corrected);
      const positions = [
        exported.names.indexOf("CD3"),
        exported.names.indexOf("CD4"),
      ];
      assert(positions.every((i) => i >= 0));
      assert.deepEqual(
        exported.rows.map((r) => positions.map((i) => r[i])),
        truth.map(([x, y]) => (corrected ? [x / 2, y / 4] : [x, y])),
      );
    }
  }
  passed(
    "Raw and compensated CSV independently recover identical CD3/CD4 measurement truth from both reordered panels",
  );

  await main.locator(".sample-name").first().click();
  await main.getByRole("button", { name: "Scatter", exact: true }).click();
  await main.getByLabel("X axis channel", { exact: true }).selectOption("CD3");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("CD4");
  await readyPlot(main);
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  let popup = await opened;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup.waitForLoadState("networkidle");
  await popup
    .getByLabel("Plot sample", { exact: true })
    .selectOption(sourceIds[1]);
  await expect
    .poll(async () => (await view(popup)).sampleId)
    .toBe(sourceIds[1]);
  await expect(popup.getByLabel("Plot sample", { exact: true })).toHaveValue(
    sourceIds[1],
  );
  await popup.getByLabel("X axis channel", { exact: true }).selectOption("CD4");
  await popup.getByLabel("Y axis channel", { exact: true }).selectOption("CD3");
  await readyPlot(popup);
  await expect.poll(async () => (await view(main)).x).toBe("CD3");
  await expect.poll(async () => (await view(popup)).x).toBe("CD4");
  assert.equal((await view(main)).sampleId, sourceIds[0]);
  assert.equal((await view(popup)).sampleId, sourceIds[1]);
  await popup.screenshot({
    path: "artifacts/screenshots/desktop-aliases-independent-plot.png",
  });
  passed(
    "Independent native plot windows use the same aliases across different physical panels while preserving separate samples and axes",
  );

  doc = await request(`/workspaces/${workspaceId}/gates/batch`, {
    revision: doc.revision,
    gates: [
      {
        id: uid(),
        sample_id: sourceIds[0],
        name: "Shared CD3 range",
        kind: "range",
        x: "CD3",
        bounds: [1, 3],
        x_transform: { kind: "linear" },
      },
    ],
  });
  doc = await request(`/workspaces/${workspaceId}/gates/apply`, {
    revision: doc.revision,
    source_sample_id: sourceIds[0],
    target_sample_ids: [sourceIds[1]],
    replace: false,
  });
  for (const id of sourceIds) {
    const counts = await request(
      `/workspaces/${workspaceId}/samples/${id}/counts`,
    );
    assert.equal(
      counts.find(
        (g) =>
          doc.gates.find((s) => s.id === g.id)?.name === "Shared CD3 range",
      ).count,
      2,
    );
  }
  passed(
    "One shared alias gate transfers between detector panels and independently counts the same two corrected events",
  );

  await main
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const exporting = main.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await exporting
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  await expect(
    exporting.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeEnabled();
  const savedFile = path.join(profile, "Aliases preserved.fcs");
  await application.evaluate(
    ({ session }, filename) =>
      session.defaultSession.once("will-download", (_event, item) =>
        item.setSavePath(filename),
      ),
    savedFile,
  );
  await exporting
    .getByRole("button", { name: "Save event file", exact: true })
    .click();
  await expect(exporting).toHaveCount(0);
  const bytes = readFileSync(savedFile);
  const dataStart = Number(bytes.subarray(26, 34).toString());
  assert.equal(bytes.length - dataStart, truth.length * 3 * 8);
  truth
    .flat()
    .forEach((value, i) =>
      assert.equal(bytes.readDoubleLE(dataStart + i * 8), value),
    );
  await importFiles([savedFile]);
  doc = await documentFor();
  const reopened = doc.samples.at(-1);
  assert.deepEqual(reopened.aliases, { CD3: "FL1", CD4: "FL2" });
  assert.deepEqual(
    doc.compensations.find((m) => m.id === reopened.compensation_id).detectors,
    ["FL1", "FL2"],
  );
  assert.deepEqual(
    (await csv(reopened.id)).rows.map((r) => r.slice(3, 5)),
    truth.map(([x, y]) => [x / 2, y / 4]),
  );
  assert.deepEqual(
    rawPaths.map((p) => sha(readFileSync(p))),
    rawHashes,
  );
  passed(
    "Native streamed FCS saving retains exactly three physical double columns; reopening restores aliases and applies the original matrix exactly once",
  );

  const job = await request(`/workspaces/${workspaceId}/jobs`, {
    revision: doc.revision,
    name: "Shared-panel PCA",
    algorithm: "pca",
    inputs: sourceIds.map((sample_id) => ({ sample_id })),
    channels: ["CD3", "CD4"],
    max_events: 100,
    use_transforms: false,
  });
  await expect
    .poll(
      async () =>
        (await request(`/workspaces/${workspaceId}/jobs/${job.id}`)).status,
      { timeout: 60000 },
    )
    .toBe("succeeded");
  doc = await request(`/workspaces/${workspaceId}/jobs/${job.id}/apply`, {
    revision: doc.revision,
  });
  assert.equal(doc.analyses[0].data.length, 2);
  assert(doc.analyses[0].data.every((d) => d.event_count === 4));
  passed(
    "The desktop worker fits shared-panel PCA across alias channels with both source samples' original event identities",
  );

  await openMapping();
  await dialog()
    .getByRole("button", { name: "Remove shared parameter CD3", exact: true })
    .click();
  await dialog()
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog().getByRole("alert")).toContainText("cannot remove CD3");
  await expect(dialog().getByRole("alert")).toContainText("Shared CD3 range");
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  passed(
    "The native mapping editor refuses to remove aliases used by saved populations and fitted models",
  );

  await openMapping();
  await dialog()
    .getByLabel(`Source for CD4 in ${original.samples[0].name}`, {
      exact: true,
    })
    .selectOption("FL3");
  await dialog()
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  doc = await request(
    `/workspaces/${workspaceId}`,
    { revision: doc.revision, name: "Revision during alias review" },
    "PATCH",
  );
  await expect(dialog().getByRole("alert")).toContainText("Workspace changed");
  await expect(
    dialog().getByRole("button", {
      name: "Apply channel aliases",
      exact: true,
    }),
  ).toBeDisabled();
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  passed(
    "A concurrent workspace revision disables a reviewed native mapping before it can be applied",
  );

  await openMapping();
  await dialog()
    .getByLabel(`Source for CD3 in ${original.samples[0].name}`, {
      exact: true,
    })
    .selectOption("FL3");
  await dialog()
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog()).toContainText("Shared-panel PCA");
  await expect(dialog()).toContainText("previously FL1");
  await dialog()
    .getByRole("button", { name: "Apply channel aliases", exact: true })
    .click();
  await expect(dialog()).toHaveCount(0);
  doc = await documentFor();
  assert.equal(doc.samples[0].aliases.CD3, "FL3");
  assert.equal(
    (
      await request(`/workspaces/${workspaceId}/samples/${sourceIds[0]}/counts`)
    )[0].count,
    0,
  );
  assert.deepEqual(
    rawPaths.map((p) => sha(readFileSync(p))),
    rawHashes,
  );
  passed(
    "Rebinding is explicitly reviewed with its affected PCA and saved population; values change through the new source while acquired arrays remain byte-identical",
  );

  await main.locator(".sample-name").first().click();
  await main.getByLabel("X axis channel", { exact: true }).selectOption("CD3");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("CD4");
  await readyPlot(main);
  const mainState = await view(main),
    popupState = await view(popup),
    saved = await documentFor();
  await closeDesktop();
  await launch();
  await expect.poll(async () => (await view(main)).x).toBe("CD3");
  assert.deepEqual(await documentFor(), saved);
  assert.deepEqual(await view(main), mainState);
  popup = (await application.windows()).find((page) =>
    page.url().includes("plotWindow="),
  );
  assert(popup);
  await popup.waitForLoadState("networkidle");
  assert.deepEqual(await view(popup), popupState);
  assert.deepEqual(
    rawPaths.map((p) => sha(readFileSync(p))),
    rawHashes,
  );
  const measurement = await request(
    `/workspaces/${workspaceId}/samples/${sourceIds[0]}/statistics?channel=CD3`,
  );
  assert(
    Math.abs(
      measurement.mean -
        truth.reduce((sum, row) => sum + row[2], 0) / truth.length,
    ) < 1e-12,
  );
  assert.equal(measurement.count, 4);
  passed(
    "A real desktop restart preserves alias bindings, fitted history and independent window state, and reopens the same original event bytes",
  );
  assert.deepEqual(errors, []);
  evidence.renderer_errors = errors;
  evidence.workspace_id = workspaceId;
  evidence.original_event_sha256 = rawHashes;
  evidence.status = "passed";
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = String(error?.stack || error);
  save();
  await application?.close().catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  save();
}
