// Real native main/popup density gating, independent labels and hole editing.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp", "desktop-autogating-" + Date.now());
const binary = process.env.CYTOFORGE_TEST_BINARY;
const evidencePath =
  process.env.CYTOFORGE_AUTOGATING_EVIDENCE ||
  path.join(root, "artifacts/desktop-autogating-source.json");
assert(path.resolve(evidencePath).startsWith(root + path.sep));
mkdirSync(profile, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
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
const rows = [];
for (let x = 12; x < 20; x++)
  for (let y = 12; y < 20; y++)
    if (x < 14 || x >= 18 || y < 14 || y >= 18)
      for (let n = 0; n < 5; n++)
        rows.push([-2 + (x + 0.5) / 8, -2 + (y + 0.5) / 8]);
for (let n = 0; n < 40; n++) rows.push([1.5625, 1.5625]);
assert.equal(rows.length, 280);
function inside(point, ring) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x, y] = point,
      [a, b] = ring[j],
      [c, d] = ring[i];
    if (
      (x - a) * (d - b) === (y - b) * (c - a) &&
      x >= Math.min(a, c) &&
      x <= Math.max(a, c) &&
      y >= Math.min(b, d) &&
      y <= Math.max(b, d)
    )
      return true;
    if (b > y !== d > y && x < a + ((y - b) * (c - a)) / (d - b)) hit = !hit;
  }
  return hit;
}
const independentCount = (gate) =>
  rows.filter(
    (p) =>
      inside(p, gate.vertices) &&
      !(gate.holes || []).some((ring) => inside(p, ring)),
  ).length;
let application, main, popup, workspaceId, sampleId;
const plots = new Map(),
  suggestions = new Map(),
  latestAutomaticRequests = new Map(),
  drafts = new Map(),
  errors = [];
function observe(page) {
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", async (response) => {
    if (!response.ok()) return;
    try {
      if (response.url().includes("/plot?")) {
        const payload = await response.json();
        if (payload.bounds.length === 4) plots.set(page, payload);
      }
      if (response.url().endsWith("/gates/automatic-preview")) {
        const payload = await response.json();
        if (latestAutomaticRequests.get(page) === response.request())
          suggestions.set(page, payload);
      }
    } catch {}
  });
  page.on("request", (request) => {
    if (request.url().endsWith("/gates/automatic-preview"))
      latestAutomaticRequests.set(page, request);
    if (request.url().endsWith("/gates/preview-shape"))
      drafts.set(page, request.postDataJSON().gate);
  });
}
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
  }));
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
  observe(main);
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  const value = await main.evaluate(
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
      if (method !== "GET" && result.id && Array.isArray(result.gates))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(result.id);
      return result;
    },
    { route, body, method },
  );
  if (value.id && Array.isArray(value.gates))
    await expect(
      main.getByRole("button", {
        name: `Revision ${value.revision} · History`,
        exact: true,
      }),
    ).toBeVisible();
  return value;
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
const ready = (page) =>
  expect(page.locator('.primary-plot canvas[data-ready="true"]')).toBeVisible();
async function point(page, world, editor = false) {
  return page
    .locator(
      editor
        ? ".inline-gate-editor .gate-shape-overlay"
        : ".primary-plot .plot-stage",
    )
    .evaluate(
      (element, { world, fallback, editor }) => {
        const limits = editor
          ? JSON.parse(element.getAttribute("data-shape-bounds"))
          : fallback;
        const box = element.getBoundingClientRect();
        return [
          box.left +
            64 +
            ((world[0] - limits[0]) / (limits[1] - limits[0])) *
              (box.width - 88),
          box.top +
            24 +
            (1 - (world[1] - limits[2]) / (limits[3] - limits[2])) *
              (box.height - 78),
        ];
      },
      { world, fallback: plots.get(page)?.bounds, editor },
    );
}
async function automatic(page, seed = [-0.4375, -0.4375]) {
  await ready(page);
  await expect.poll(() => plots.get(page)?.bounds.length).toBe(4);
  await page
    .getByRole("button", { name: "Automatic density gate", exact: true })
    .click();
  await page.mouse.click(...(await point(page, seed)));
  await expect(
    page.getByRole("region", {
      name: "Automatic density gate review",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Automatic density bounds", { exact: true })
    .fill("-2, 2, -2, 2");
  await page
    .getByLabel("Automatic density resolution", { exact: true })
    .fill("32");
  await page
    .getByLabel("Automatic density smoothing", { exact: true })
    .fill("0");
  await page
    .getByLabel("Automatic density coverage", { exact: true })
    .fill("99.5");
  await expect
    .poll(() => {
      const audit = suggestions.get(page)?.audit;
      return (
        audit && {
          bins: audit.bins,
          sigma: audit.sigma,
          coverage: audit.coverage,
          domain: audit.domain,
        }
      );
    })
    .toEqual({ bins: 32, sigma: 0, coverage: 0.995, domain: [-2, 2, -2, 2] });
  await expect(
    page.getByRole("button", { name: "Use automatic gate", exact: true }),
  ).toBeEnabled();
  return suggestions.get(page);
}
async function rootPopulation(page) {
  await page
    .getByRole("button", { name: "View all events", exact: true })
    .click();
  await ready(page);
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
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native automatic gate truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "donut.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(csv);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  for (const channel of ["X", "Y"])
    doc = await request(`/workspaces/${workspaceId}/transforms`, {
      revision: doc.revision,
      sample_ids: [sampleId],
      channel,
      transform: { kind: "linear" },
    });
  await main.getByLabel("X axis channel", { exact: true }).selectOption("X");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("Y");
  await ready(main);
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(main);
  await expect(
    main.getByRole("button", { name: "Automatic density gate", exact: true }),
  ).toBeDisabled();
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("a");
  await expect(
    main.getByRole("button", { name: "Automatic density gate", exact: true }),
  ).not.toHaveAttribute("aria-pressed", "true");
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("Y");
  await ready(main);
  evidence.checks.push(
    "Automatic gating toolbar and A shortcut reject native one-dimensional views",
  );
  const before = await documentFor(),
    history = await request(`/workspaces/${workspaceId}/history`);
  const suggested = await automatic(main);
  assert.equal(suggested.count, 240);
  assert.equal(independentCount(suggested.gate), 240);
  assert.equal(suggested.gate.holes.length, 1);
  assert.equal(suggested.audit.component_count, 2);
  await expect(main.locator(".automatic-gate-count")).toContainText(
    "240 / 280 events",
  );
  assert.deepEqual(await documentFor(), before);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  evidence.checks.push(
    "Full-event native preview keeps the donut hole and disconnected region separate, reports independently counted 240/280 events and writes no workspace/history",
  );
  await main
    .getByLabel("Automatic density coverage", { exact: true })
    .fill("100");
  await expect(
    main.getByRole("button", { name: "Use automatic gate", exact: true }),
  ).toBeDisabled();
  await main
    .getByLabel("Automatic density coverage", { exact: true })
    .fill("99.5");
  await expect(
    main.getByRole("button", { name: "Use automatic gate", exact: true }),
  ).toBeEnabled();
  await main
    .getByRole("button", { name: "Use automatic gate", exact: true })
    .click();
  await expect(
    main.getByRole("dialog", { name: "Create population", exact: true }),
  ).toBeVisible();
  await main
    .getByLabel("Population name", { exact: true })
    .fill("Automatic donut");
  await expect(
    main.getByLabel("Excluded ring 1 in transformed coordinates", {
      exact: true,
    }),
  ).toBeVisible();
  await main
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(main.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  let saved = doc.gates.find((g) => g.name === "Automatic donut");
  assert(saved);
  assert.deepEqual(saved.vertices, suggested.gate.vertices);
  assert.deepEqual(saved.holes, suggested.gate.holes);
  assert.equal(doc.revision, before.revision + 1);
  assert.equal(
    saved.provenance.automatic_gate.algorithm,
    "highest-density-seeded-contour-v1",
  );
  evidence.checks.push(
    "Validation prevents invalid parameters; accepting opens native numeric review and saves the exact outer/hole coordinates with provenance in one revision",
  );
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1260, height: 900 });
  await ready(popup);
  await rootPopulation(popup);
  const popupSuggestion = await automatic(popup, [1.5625, 1.5625]);
  assert.equal(popupSuggestion.count, 40);
  assert.equal(independentCount(popupSuggestion.gate), 40);
  assert.equal(popupSuggestion.gate.holes?.length || 0, 0);
  await popup
    .getByRole("button", { name: "Cancel automatic gate", exact: true })
    .click();
  assert.equal((await documentFor()).revision, doc.revision);
  evidence.checks.push(
    "Independent native popup selects the second component with 40 exact events; cancelling leaves the scientific revision unchanged",
  );
  await automatic(popup);
  await application.evaluate(({ BrowserWindow, dialog }) => {
    globalThis.automaticOriginalMessageBox = dialog.showMessageBox;
    globalThis.automaticCloseMessages = [];
    dialog.showMessageBox = async (_parent, options) => {
      globalThis.automaticCloseMessages.push(options.message);
      return { response: 0 };
    };
    BrowserWindow.getAllWindows()
      .find((w) => w.webContents.getURL().includes("plotWindow="))
      .close();
  });
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.automaticCloseMessages.length),
    )
    .toBe(1);
  assert(!popup.isClosed());
  await application.evaluate(({ dialog }) => {
    dialog.showMessageBox = globalThis.automaticOriginalMessageBox;
  });
  evidence.checks.push(
    "Unaccepted automatic gate drafts protect native popup close and keep the window and outline available for review",
  );
  const retained = JSON.stringify(suggestions.get(popup).gate.vertices);
  doc = await request(
    `/workspaces/${workspaceId}`,
    {
      revision: doc.revision,
      name: doc.name + " changed",
      description: "Native stale preview test",
    },
    "PATCH",
  );
  await expect(
    popup.getByRole("button", { name: "Use automatic gate", exact: true }),
  ).toBeDisabled();
  await expect(popup.locator('.plot-draft-notice[role="alert"]')).toContainText(
    "retained",
  );
  assert.equal(JSON.stringify(suggestions.get(popup).gate.vertices), retained);
  await popup
    .getByRole("button", { name: "Cancel automatic gate", exact: true })
    .click();
  evidence.checks.push(
    "Concurrent workspace change blocks acceptance while retaining the popup outline until explicit cancel",
  );
  await main
    .locator(".gate-row")
    .filter({ hasText: "Automatic donut" })
    .click();
  await ready(main);
  const showInspector = main.getByRole("button", {
    name: "Show inspector",
    exact: true,
  });
  if (await showInspector.isVisible()) await showInspector.click();
  await main
    .getByRole("button", { name: "Edit gate on plot", exact: true })
    .click();
  await expect(
    main.locator('.inline-gate-editor [data-shape-handle="h0v0"]'),
  ).toBeVisible();
  const handle = main.locator('.inline-gate-editor [data-shape-handle="h0v0"]');
  await handle.focus();
  await main.keyboard.press("ArrowRight");
  await expect
    .poll(() => JSON.stringify(drafts.get(main)?.holes))
    .not.toBe(JSON.stringify(saved.holes));
  assert.equal((await documentFor()).revision, doc.revision);
  await main
    .getByRole("button", { name: "Apply gate edit", exact: true })
    .click();
  await expect(main.locator(".inline-gate-editor")).toHaveCount(0);
  doc = await documentFor();
  saved = doc.gates.find((g) => g.id === saved.id);
  assert.notDeepEqual(saved.holes, suggested.gate.holes);
  const counts = await request(
    `/workspaces/${workspaceId}/samples/${sampleId}/counts`,
  );
  assert.equal(
    counts.find((g) => g.id === saved.id).count,
    independentCount(saved),
  );
  evidence.checks.push(
    "Native inline hole vertex handles retain the draft until Apply and save independently verified full-event membership",
  );
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await documentFor()).gates.find((g) => g.id === saved.id).holes,
    )
    .toEqual(suggested.gate.holes);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await documentFor()).gates.find((g) => g.id === saved.id).holes,
    )
    .toEqual(saved.holes);
  await expect
    .poll(
      () =>
        plots.get(popup)?.overlays.find((g) => g.id === saved.id)?.holes
          ?.length,
    )
    .toBe(1);
  evidence.checks.push(
    "Undo/redo restores exact hole coordinates and synchronizes saved contours to the independent popup",
  );
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-autogating.png"),
  });
  const finalDoc = await documentFor();
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  await expect.poll(async () => (await application.windows()).length).toBe(2);
  assert.deepEqual((await documentFor()).gates, finalDoc.gates);
  assert.equal((await documentFor()).revision, finalDoc.revision);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Normal desktop restart restores automatic gate geometry, holes, provenance and revision with no renderer errors",
  );
  assert.equal(evidence.checks.length, 9);
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
  evidence.revision = finalDoc.revision;
  evidence.renderer_errors = errors;
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.message;
  evidence.checkpoint = evidence.checks.length;
  evidence.renderer_errors = errors;
  await main
    ?.screenshot({
      path: path.join(
        root,
        "artifacts/screenshots/desktop-autogating-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
