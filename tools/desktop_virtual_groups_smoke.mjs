// Native pooled groups over independently specified detector values and source populations.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", `desktop-virtual-groups-${Date.now()}`);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_VIRTUAL_GROUPS_EVIDENCE ||
  "artifacts/desktop-virtual-groups-source.json";
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
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native virtual cohort truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const files = [
    path.join(profile, "Panel A.fcs"),
    path.join(profile, "Panel B.fcs"),
  ];
  writeFileSync(
    files[0],
    fcs(["FL1", "FL2", "FL3"], ["CD3", "CD4", "Background"], truth, [
      [2, 0],
      [0, 4],
    ]),
  );
  writeFileSync(
    files[1],
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
  await importFiles(files);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  const sourceIds = doc.samples.map((s) => s.id);
  const rawPaths = sourceIds.map((id) =>
    path.join(profile, "data", "events", workspaceId, `${id}.npy`),
  );
  const rawHashes = rawPaths.map((p) => sha(readFileSync(p)));
  const aliasRequest = {
    revision: doc.revision,
    mappings: doc.samples.map((s, i) => ({
      sample_id: s.id,
      bindings: [
        { name: "CD3", source: i ? "B1" : "FL1" },
        { name: "CD4", source: i ? "B2" : "FL2" },
      ],
    })),
  };
  const aliasReview = await request(
    `/workspaces/${workspaceId}/channel-aliases/preview`,
    aliasRequest,
  );
  doc = await request(`/workspaces/${workspaceId}/channel-aliases/apply`, {
    ...aliasRequest,
    review_hash: aliasReview.review_hash,
  });
  const group = {
    id: uid(),
    name: "Native pooled panels",
    sample_ids: sourceIds,
  };
  doc = await request(`/workspaces/${workspaceId}/groups`, {
    revision: doc.revision,
    group,
  });
  const gates = doc.samples.map((s, i) => ({
    id: uid(),
    sample_id: s.id,
    name: "Positive",
    kind: "range",
    x: "CD3",
    bounds: i ? [2, 4] : [0, 2],
  }));
  doc = await request(`/workspaces/${workspaceId}/gates/batch`, {
    revision: doc.revision,
    gates,
  });
  const original = doc;
  await main.getByRole("button", { name: "Samples", exact: true }).click();
  await main.locator(".sample-name").first().click();
  await expect.poll(async () => (await view(main)).sampleId).toBe(sourceIds[0]);
  await main.getByLabel("X axis channel", { exact: true }).selectOption("CD3");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("CD4");
  await main
    .getByLabel("Plot sample group", { exact: true })
    .selectOption(group.id);
  await main.getByLabel("Pool plot group", { exact: true }).click();
  await readyPlot(main);
  await expect(
    main.getByLabel("Pool sample group", { exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(main.locator(".metric-row strong").first()).toHaveText("8");
  await expect(main.locator(".inspector-count strong")).toHaveText("8");
  await expect(main.locator(".metric-row strong").nth(2)).toHaveText(
    /2\s*parameters/,
  );
  await expect.poll(async () => (await view(main)).pooled).toBe(true);
  assert.deepEqual(await documentFor(), original);
  passed(
    "Linked native plot and sample-list toggles pool both reordered detector panels with eight events and no new sample or source writes",
  );

  const popupEvent = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  let popup = await popupEvent;
  popup.on("pageerror", (e) => errors.push(e.message));
  await popup.waitForLoadState("networkidle");
  await readyPlot(popup);
  assert.equal((await view(popup)).pooled, true);
  await popup.getByLabel("Pool plot group", { exact: true }).click();
  await readyPlot(popup);
  await expect(popup.locator(".metric-row strong").first()).toHaveText("4");
  assert.equal((await view(main)).pooled, true);
  passed(
    "An independent native plot window inherits the pooled cohort and can switch to its representative sample without changing the main window",
  );

  await main
    .getByRole("button", { name: /^Positive/ })
    .first()
    .click();
  await readyPlot(main);
  await expect.poll(async () => (await view(main)).gateId).toBe(gates[0].id);
  await expect(main.locator(".metric-row strong").first()).toHaveText("4");
  const summary = main.getByLabel("Pooled source samples", { exact: true });
  await summary.locator("summary").click();
  await expect(summary).toContainText("Panel A.fcs: 2 of 4 events");
  await expect(summary).toContainText("Panel B.fcs: 2 of 4 events");
  passed(
    "Pooled population navigation matches exact hierarchy paths while retaining each source's different gate geometry and independent population counts",
  );

  await main
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const exportDialog = main.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await expect(
    exportDialog.getByLabel("Export measurement values", { exact: true }),
  ).toHaveValue("compensated");
  await exportDialog
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  await expect(
    exportDialog.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeEnabled({ timeout: 60000 });
  const savedFile = path.join(profile, "pooled-population.fcs");
  await application.evaluate(({ session }, target) => {
    session.defaultSession.once("will-download", (_event, item) =>
      item.setSavePath(target),
    );
  }, savedFile);
  await exportDialog
    .getByRole("button", { name: "Save event file", exact: true })
    .click();
  await expect(exportDialog).toHaveCount(0);
  const exported = readFileSync(savedFile);
  const start = Number(exported.toString("ascii", 26, 34));
  const measurements = Array.from({ length: 16 }, (_, i) =>
    exported.readDoubleLE(start + i * 8),
  );
  assert.deepEqual(
    measurements,
    [0.5, 0.5, 1, 0, 1.5, 1, 1, 1, 2.5, 1.5, 2, 2, 3.5, 2, 2, 3],
  );
  evidence.export_sha256 = sha(exported);
  passed(
    "The native Save dialog writes the selected four-event pooled FCS with float64 corrected values and qualified original member/event identities",
  );

  await main
    .getByRole("button", { name: "Analyze pooled group", exact: true })
    .click();
  await expect(main.locator(".analysis-config-footer")).toContainText(
    "2 samples",
  );
  await main.getByRole("button", { name: "Run PCA", exact: true }).click();
  await expect
    .poll(
      async () => (await request(`/workspaces/${workspaceId}/jobs`))[0]?.status,
      { timeout: 60000 },
    )
    .toBe("succeeded");
  const job = (await request(`/workspaces/${workspaceId}/jobs`))[0];
  assert.deepEqual(job.request.inputs, [
    { sample_id: sourceIds[0], gate_id: gates[0].id },
    { sample_id: sourceIds[1], gate_id: gates[1].id },
  ]);
  passed(
    "Joint PCA is configured and executed through the native UI with both original sample IDs and their mapped population IDs",
  );
  await main.getByRole("button", { name: "Analysis", exact: true }).click();
  await main.getByLabel("View all events", { exact: true }).click();
  await readyPlot(main);
  await main.getByRole("button", { name: "3D", exact: true }).click();
  await main.getByLabel("3D event cloud", { exact: true }).waitFor();
  await expect(
    main.getByLabel("3D event cloud", { exact: true }),
  ).toHaveAttribute("data-ready", "true", { timeout: 60000 });
  await expect(main.locator(".metric-row strong").first()).toHaveText("8");
  assert.equal((await view(popup)).mode, "density");
  passed(
    "The pooled native 3D viewer renders all eight source events through the explicit software test path while its peer retains a separate two-dimensional view",
  );
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await readyPlot(main);
  const draw = async (name) => {
    await main.getByRole("button", { name: /^Range gate/ }).click();
    const plot = main
      .locator('.primary-plot canvas[data-ready="true"]')
      .first();
    const box = await plot.boundingBox();
    assert(box);
    const y = box.y + box.height * 0.5;
    await main.mouse.move(box.x + 64 + (box.width - 88) * 0.25, y);
    await main.mouse.down();
    await main.mouse.move(box.x + 64 + (box.width - 88) * 0.75, y, {
      steps: 12,
    });
    await main.mouse.up();
    const draft = main.getByRole("dialog", {
      name: "Create population",
      exact: true,
    });
    await draft.getByLabel("Population name", { exact: true }).fill(name);
    await draft
      .getByRole("button", { name: "Create population", exact: true })
      .click();
    return {
      draft,
      review: main.getByRole("dialog", {
        name: "Apply gates to pooled group",
        exact: true,
      }),
    };
  };
  let pending = await draw("Shared drawn range");
  await expect(pending.review.getByLabel("Pooled gate counts")).toBeVisible();
  assert.equal((await documentFor()).gates.length, 2);
  await pending.review
    .getByRole("button", { name: "Apply to 2 samples", exact: true })
    .click();
  await expect(pending.review).toHaveCount(0);
  await expect(pending.draft).toHaveCount(0);
  doc = await documentFor();
  const shared = doc.gates.filter((g) => g.name === "Shared drawn range");
  assert.equal(shared.length, 2);
  assert.deepEqual(new Set(shared.map((g) => g.sample_id)), new Set(sourceIds));
  assert.deepEqual(doc.samples, original.samples);
  assert.deepEqual(doc.compensations, original.compensations);
  passed(
    "Drawing a range in a pooled native plot reviews every sample before creating the shared strategy in one atomic transaction",
  );
  await main.getByLabel("Undo", { exact: true }).click();
  await expect.poll(async () => (await documentFor()).gates.length).toBe(2);
  await main.getByLabel("Redo", { exact: true }).click();
  await expect.poll(async () => (await documentFor()).gates.length).toBe(4);
  passed(
    "Native Undo and Redo restore the complete multi-sample gate transaction and preserve original event arrays and matrices",
  );

  pending = await draw("Canceled shared range");
  await expect(
    pending.review.getByRole("button", {
      name: "Apply to 2 samples",
      exact: true,
    }),
  ).toBeEnabled();
  await pending.review
    .getByRole("button", { name: "Return to draft", exact: true })
    .click();
  await expect(pending.draft).toBeVisible();
  await pending.draft
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  assert.equal((await documentFor()).gates.length, 4);
  passed(
    "Canceling the multi-sample review returns to the editable gate draft and leaves every original sample unchanged",
  );

  pending = await draw("Stale shared range");
  await expect(
    pending.review.getByRole("button", {
      name: "Apply to 2 samples",
      exact: true,
    }),
  ).toBeEnabled();
  doc = await documentFor();
  doc = await request(
    `/workspaces/${workspaceId}`,
    { revision: doc.revision, name: "Revision during pooled review" },
    "PATCH",
  );
  await expect(pending.review.getByRole("alert")).toContainText(
    "Workspace changed",
  );
  await expect(
    pending.review.getByRole("button", {
      name: "Apply to 2 samples",
      exact: true,
    }),
  ).toBeDisabled();
  await pending.review
    .getByRole("button", { name: "Return to draft", exact: true })
    .click();
  await pending.draft
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  assert.equal((await documentFor()).gates.length, 4);
  passed(
    "A concurrent revision disables the reviewed group gate proposal before it can mutate any sample",
  );

  await main
    .getByRole("button", { name: /^Shared drawn range/ })
    .first()
    .click();
  await readyPlot(main);
  await main
    .getByRole("button", { name: "Delete population", exact: true })
    .click();
  const removal = main.getByRole("dialog", {
    name: "Delete Shared drawn range?",
    exact: true,
  });
  await removal
    .getByRole("button", { name: "Delete population", exact: true })
    .click();
  const deleteReview = main.getByRole("dialog", {
    name: "Apply gates to pooled group",
    exact: true,
  });
  await expect(deleteReview.getByLabel("Pooled gate counts")).toBeVisible();
  assert.equal((await documentFor()).gates.length, 4);
  await deleteReview
    .getByRole("button", { name: "Apply to 2 samples", exact: true })
    .click();
  await expect(removal).toHaveCount(0);
  await expect.poll(async () => (await documentFor()).gates.length).toBe(2);
  await main.getByLabel("Undo", { exact: true }).click();
  await expect.poll(async () => (await documentFor()).gates.length).toBe(4);
  await main.getByLabel("View all events", { exact: true }).click();
  await readyPlot(main);
  passed(
    "Removing a pooled population reviews all affected original branches and deletes the group strategy in one reversible native transaction",
  );
  await main.screenshot({
    path: "artifacts/screenshots/desktop-virtual-groups-pooled.png",
  });
  const mainState = await view(main),
    popupState = await view(popup),
    saved = await documentFor();
  await closeDesktop();
  await launch();
  await expect.poll(async () => (await view(main)).pooled).toBe(true);
  assert.deepEqual(await view(main), mainState);
  popup = (await application.windows()).find((page) =>
    page.url().includes("plotWindow="),
  );
  assert(popup);
  await popup.waitForLoadState("networkidle");
  assert.deepEqual(await view(popup), popupState);
  assert.deepEqual(await documentFor(), saved);
  assert.deepEqual(
    rawPaths.map((p) => sha(readFileSync(p))),
    rawHashes,
  );
  assert.deepEqual(errors, []);
  passed(
    "A clean native restart restores independent pooled and single-sample windows exactly, with unchanged event bytes and scientific workspace data",
  );
  evidence.workspace_id = workspaceId;
  evidence.original_event_sha256 = rawHashes;
  evidence.renderer_errors = errors;
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
