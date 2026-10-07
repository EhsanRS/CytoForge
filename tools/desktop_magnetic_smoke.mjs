// Actual native desktop windows: magnetic review, synchronization and persistence.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", "desktop-magnetic-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_MAGNETIC_EVIDENCE ||
  path.join(root, "artifacts/desktop-magnetic-source.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
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
const errors = [];
let application, main, population;
writeFileSync(evidencePath, JSON.stringify(evidence));
const deadline = setTimeout(() => application?.process().kill(), 240000);
const uid = () => randomUUID().replaceAll("-", "");
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
  main = await application.firstWindow();
  main.on("pageerror", (error) => errors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  const result = await main.evaluate(
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
      if (value.id && Array.isArray(value.gates))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(value.id);
      return value;
    },
    { route, body, method },
  );
  if (result.id && Array.isArray(result.gates))
    await expect(
      main.getByRole("button", {
        name: `Revision ${result.revision} · History`,
        exact: true,
      }),
    ).toBeVisible();
  return result;
}
const documentFor = (id) => request(`/workspaces/${id}`);
async function ready(page) {
  await expect(page.locator(".primary-plot canvas")).toBeVisible();
  await expect(page.locator(".primary-plot .plot-updating")).toHaveCount(0);
  await expect(page.locator(".primary-plot .loading")).toHaveCount(0);
}
async function showInspector(page) {
  const button = page.getByRole("button", {
    name: "Show inspector",
    exact: true,
  });
  if (await button.isVisible()) await button.click();
  await expect(page.locator(".inspector-count strong")).toBeVisible();
}
async function importCsv(name, rows) {
  const file = path.join(profile, name);
  writeFileSync(file, ["X,Y", ...rows].join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(file);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(main);
}
async function openPlot() {
  const created = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  const page = await created;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1260, height: 850 });
  await ready(page);
  await showInspector(page);
  return page;
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
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Magnetic desktop truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const rows = [
    ...Array.from(
      { length: 257 },
      (_, i) => `${1.2 + (i % 17) * 0.0003},${1.1 + (i % 19) * 0.0003}`,
    ),
    ...Array.from(
      { length: 1024 },
      (_, i) => `${7 + (i % 13) * 0.0003},${7 + (i % 11) * 0.0003}`,
    ),
  ];
  await importCsv("shifted-source.csv", rows);
  const id = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor(id);
  const sample = doc.samples[0];
  const gate = {
    id: uid(),
    sample_id: sample.id,
    name: "Following λ",
    kind: "rectangle",
    x: "X",
    y: "Y",
    bounds: [-0.5, 0.5, -0.5, 0.5],
  };
  doc = await request(`/workspaces/${id}/gates`, {
    revision: doc.revision,
    gate,
  });
  await main.locator(".gate-row").filter({ hasText: "Following λ" }).click();
  await expect(main.locator(".inspector-count strong")).toHaveText("0");
  population = await openPlot();
  await main
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await main
    .getByRole("checkbox", { name: "Magnetic gate", exact: true })
    .check();
  const revision = doc.revision;
  await main
    .getByRole("button", { name: "Preview magnetic position", exact: true })
    .click();
  await expect(main.locator(".magnetic-preview")).toContainText(
    "0 → 257 of 1,281 parent events",
  );
  assert.equal((await documentFor(id)).revision, revision);
  evidence.checks.push(
    "Read-only native preview reports all-event counts and leaves workspace revision unchanged",
  );
  await main
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(main.locator(".inspector-count strong")).toHaveText("257");
  await expect(population.locator(".inspector-count strong")).toHaveText("257");
  await expect(
    main.locator(".gate-tree").getByLabel("Magnetic gate", { exact: true }),
  ).toHaveCount(1);
  doc = await documentFor(id);
  let following = doc.gates.find((g) => g.id === gate.id);
  assert.deepEqual(following.bounds, gate.bounds);
  assert.equal(following.magnetic.algorithm, "local-window-count-v1");
  evidence.checks.push(
    "Native save preserves the original shape and synchronizes resolved counts and magnet icons across windows",
  );
  const xmlPath = path.join(profile, "reviewed-magnetic.gates.xml");
  await application.evaluate(({ session }, filePath) => {
    globalThis.magneticDownload = new Promise((resolve, reject) => {
      session.defaultSession.once("will-download", (_event, item) => {
        item.setSavePath(filePath);
        item.once("done", (_done, state) => {
          if (state === "completed") resolve(filePath);
          else reject(new Error("GatingML download " + state));
        });
      });
    });
  }, xmlPath);
  await expect(
    main.getByRole("button", { name: "Export GatingML snapshot", exact: true }),
  ).toHaveAttribute("title", /static gates/);
  await main
    .getByRole("button", { name: "Export GatingML snapshot", exact: true })
    .click();
  await application.evaluate(() => globalThis.magneticDownload);
  assert(existsSync(xmlPath));
  assert(readFileSync(xmlPath, "utf8").includes("static-snapshot"));
  evidence.gatingml_snapshot = xmlPath;
  evidence.checks.push(
    "The native export control explicitly identifies GatingML snapshots and writes resolved static geometry with tracking provenance",
  );
  await population
    .getByRole("button", { name: "Fit magnetic positions", exact: true })
    .click();
  await ready(population);
  const fitted = await population.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert(fitted.bounds[0] < 0 && fitted.bounds[2] < 0);
  await expect(
    main.getByRole("button", { name: "Reset zoom", exact: true }),
  ).toHaveCount(0);
  evidence.fit = { bounds: fitted.bounds };
  await population.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-magnetic-movement.png",
    ),
  });
  evidence.checks.push(
    "Fit magnetic positions makes the original anchor and resolved geometry reviewable in the native popup",
  );
  await population
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await population
    .getByRole("button", { name: "Preview magnetic position", exact: true })
    .click();
  await expect(population.locator(".magnetic-preview")).toContainText(
    "0 → 257",
  );
  doc = await request(
    `/workspaces/${id}/gates/${gate.id}`,
    {
      revision: doc.revision,
      gate: { ...following, name: "Reviewed following λ" },
    },
    "PUT",
  );
  await expect(population.locator(".gate-draft-conflict")).toBeVisible();
  await expect(
    population.getByRole("button", {
      name: "Preview magnetic position",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(population.locator(".magnetic-preview")).toHaveCount(0);
  await population
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await population
    .getByRole("button", { name: "Preview magnetic position", exact: true })
    .click();
  await expect(population.locator(".magnetic-preview")).toContainText(
    "0 → 257",
  );
  await population
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(main.locator(".analysis-heading h1")).toHaveText("Following λ");
  evidence.checks.push(
    "Concurrent edits retain the magnetic draft, invalidate its old preview and require explicit review before saving",
  );
  doc = await documentFor(id);
  const parent = {
    id: uid(),
    sample_id: sample.id,
    name: "Restricted parent",
    kind: "range",
    x: "Y",
    bounds: [0, 2],
  };
  doc = await request(`/workspaces/${id}/gates`, {
    revision: doc.revision,
    gate: parent,
  });
  following = doc.gates.find((g) => g.id === gate.id);
  doc = await request(
    `/workspaces/${id}/gates/${gate.id}`,
    { revision: doc.revision, gate: { ...following, parent_id: parent.id } },
    "PUT",
  );
  await expect(population.locator(".inspector-count strong")).toHaveText("257");
  let storedParent = doc.gates.find((g) => g.id === parent.id);
  doc = await request(
    `/workspaces/${id}/gates/${parent.id}`,
    { revision: doc.revision, gate: { ...storedParent, bounds: [6, 8] } },
    "PUT",
  );
  await expect(main.locator(".inspector-count strong")).toHaveText("0");
  await expect(population.locator(".inspector-count strong")).toHaveText("0");
  doc = await request(`/workspaces/${id}/undo`, { revision: doc.revision });
  await expect(population.locator(".inspector-count strong")).toHaveText("257");
  evidence.checks.push(
    "Editing the parent recomputes the magnetic child within its search bound; undo restores both native window counts",
  );
  // Remove the source-specific parent before copying the geometric strategy.
  following = doc.gates.find((g) => g.id === gate.id);
  doc = await request(
    `/workspaces/${id}/gates/${gate.id}`,
    { revision: doc.revision, gate: { ...following, parent_id: null } },
    "PUT",
  );
  const targetRows = rows.map((row, i) =>
    i < 257
      ? row
          .split(",")
          .map((v) => -Number(v))
          .join(",")
      : row,
  );
  await importCsv("shifted-target.csv", targetRows);
  doc = await documentFor(id);
  const target = doc.samples.find((s) => s.id !== sample.id);
  doc = await request(`/workspaces/${id}/gates/apply`, {
    revision: doc.revision,
    source_sample_id: sample.id,
    target_sample_ids: [target.id],
    replace: false,
  });
  const copied = doc.gates.find(
    (g) => g.sample_id === target.id && g.name === "Following λ",
  );
  assert(copied);
  assert.deepEqual(copied.bounds, gate.bounds);
  await main
    .locator(".sidebar-sample")
    .filter({ hasText: "shifted-target.csv" })
    .click();
  await main.locator(".gate-row").filter({ hasText: "Following λ" }).click();
  await expect(main.locator(".inspector-count strong")).toHaveText("257");
  const body = { revision: doc.revision, gate: copied };
  const targetPosition = await request(
    `/workspaces/${id}/gates/preview-magnetic`,
    body,
  );
  assert(targetPosition.magnetic.shift[0] < 0);
  assert.equal(
    (
      await population.evaluate(() =>
        globalThis.cytoforgeDesktop.getPlotWindow(),
      )
    ).sampleId,
    sample.id,
  );
  evidence.checks.push(
    "Copied native gate strategies retain anchors and follow the other sample independently; the source popup remains pinned",
  );
  await main
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await main
    .getByRole("button", { name: "Preview magnetic position", exact: true })
    .click();
  await expect(main.locator(".magnetic-preview")).toContainText("0 → 257");
  await main
    .getByRole("button", { name: "Freeze reviewed position", exact: true })
    .click();
  await expect(
    main.getByRole("checkbox", { name: "Magnetic gate", exact: true }),
  ).not.toBeChecked();
  await main
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  doc = await documentFor(id);
  const frozen = doc.gates.find((g) => g.id === copied.id);
  assert(!("magnetic" in frozen));
  assert.deepEqual(frozen.bounds, targetPosition.gate.bounds);
  await expect(main.locator(".inspector-count strong")).toHaveText("257");
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    main.locator(".gate-tree").getByLabel("Magnetic gate", { exact: true }),
  ).toHaveCount(1);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(
    main.locator(".gate-tree").getByLabel("Magnetic gate", { exact: true }),
  ).toHaveCount(0);
  evidence.checks.push(
    "Freezing commits the reviewed geometry as static, preserves membership and supports native undo/redo",
  );
  await closeDesktop();
  await launch();
  await expect(main.locator(".inspector-count strong")).toHaveText("257");
  await expect.poll(() => application.windows().length).toBe(2);
  const restored = application.windows().find((p) => p !== main);
  await showInspector(restored);
  await expect(restored.locator(".inspector-count strong")).toHaveText("257");
  const restoredState = await restored.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert.equal(restoredState.sampleId, sample.id);
  assert.deepEqual(restoredState.bounds, fitted.bounds);
  doc = await documentFor(id);
  assert(doc.gates.find((g) => g.id === gate.id).magnetic);
  assert(!doc.gates.find((g) => g.id === copied.id).magnetic);
  evidence.checks.push(
    "Desktop restart restores magnetic settings, frozen geometry, source popup state and exact population counts",
  );
  assert.deepEqual(errors, []);
  evidence.status = "passed";
  evidence.gates = doc.gates;
  evidence.validated_at = new Date().toISOString();
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack || String(error);
  if (main && !main.isClosed())
    await main
      .screenshot({
        path: path.join(
          root,
          "artifacts/screenshots/desktop-magnetic-failure.png",
        ),
      })
      .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
