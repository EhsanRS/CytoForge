// Exercise the actual packaged desktop in an owned profile, including native
// template export/import, destination counts, draft preservation and a popup.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = realpathSync(process.cwd());
const binary = realpathSync(
  path.resolve(
    root,
    process.env.CYTOFORGE_TEST_BINARY ||
      "artifacts/candidates/report-templates/desktop/CytoForge-0.1.0.AppImage",
  ),
);
assert(binary.startsWith(root + path.sep));
const profile = path.join(root, `.tmp/desktop-report-templates-${Date.now()}`);
const output = path.resolve(
  root,
  process.env.CYTOFORGE_TEMPLATE_EVIDENCE ||
    "artifacts/desktop-report-templates-appimage.json",
);
assert(output.startsWith(root + path.sep));
mkdirSync(profile, { recursive: true });
mkdirSync(path.dirname(output), { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const file = path.join(profile, "reusable.cytoforge-report.json");
const noSandbox = process.env.CYTOFORGE_TEST_NO_SANDBOX === "1";
const identifier = () => randomUUID().replaceAll("-", "");
const digest = (filename) =>
  createHash("sha256").update(readFileSync(filename)).digest("hex");
const evidence = {
  status: "running",
  binary: path.relative(root, binary),
  binary_sha256: digest(binary),
  profile: path.relative(root, profile),
  checks: [],
  scope:
    "Linux x64 packaged desktop, isolated headless profile, hardware GPU disabled",
};
const errors = [];
let application, main;
const save = () =>
  writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n");
const passed = (name) => {
  evidence.checks.push(name);
  save();
};
const deadline = setTimeout(() => application?.process().kill(), 240000);

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
      return response.json();
    },
    { route, body, method },
  );
}

async function fixture(name, filename, count, bounds) {
  let doc = await request("/workspaces", { name });
  doc = await main.evaluate(
    async ({ doc, filename, count }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const form = new FormData();
      form.append(
        "files",
        new Blob([
          "X,Y\n" +
            Array.from(
              { length: count },
              (_, index) => `${index},${index + 10}`,
            ).join("\n"),
        ]),
        filename,
      );
      const response = await fetch(
        `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
        { method: "POST", headers: { "X-CytoForge-Token": token }, body: form },
      );
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).workspace;
    },
    { doc, filename, count },
  );
  return request(`/workspaces/${doc.id}/gates`, {
    revision: doc.revision,
    gate: {
      id: identifier(),
      sample_id: doc.samples[0].id,
      name: "Positive",
      kind: "range",
      x: "X",
      bounds,
    },
  });
}

async function studio(doc) {
  await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.saveWorkspace(id),
    doc.id,
  );
  await main.reload();
  await main.locator('canvas[data-ready="true"]').first().waitFor();
  await main
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await expect(
    main.getByRole("heading", { name: "Layout studio", exact: true }),
  ).toBeVisible();
}

async function loadTemplate(source, target, name, keepDraft = false) {
  await main
    .getByRole("button", { name: "Load template", exact: true })
    .click();
  const dialog = main.getByRole("dialog", { name: "Load report template" });
  await dialog.getByLabel("Choose report template").setInputFiles(file);
  const sample = dialog.getByLabel(
    `Sample destination for ${source.samples[0].name}`,
    { exact: true },
  );
  await expect(sample).toBeEnabled();
  await sample.selectOption(target.samples[0].id);
  await expect(
    dialog.getByLabel("Population destination for Positive", { exact: true }),
  ).toBeDisabled();
  await dialog.getByLabel("Imported report name").fill(name);
  await dialog
    .getByRole("button", { name: "Review bindings", exact: true })
    .click();
  const apply = dialog.getByRole("button", {
    name: keepDraft
      ? "Import and keep current draft"
      : "Import and open report",
    exact: true,
  });
  await expect(apply).toBeEnabled({ timeout: 30000 });
  await dialog.getByLabel("Template preview page").selectOption("1");
  await dialog
    .getByRole("button", { name: "Review bindings", exact: true })
    .click();
  await expect(apply).toBeEnabled({ timeout: 30000 });
  await expect(dialog.locator(".template-page-preview svg")).toContainText(
    "4 / 3.50",
  );
  await apply.click();
  await expect(dialog).not.toBeVisible();
  return request(`/workspaces/${target.id}`);
}

const scientific = (doc) =>
  Object.fromEntries(
    Object.entries(doc).filter(
      ([key]) => !["layouts", "revision", "updated_at"].includes(key),
    ),
  );

save();
try {
  application = await electron.launch({
    executablePath: binary,
    args: [
      "--headless",
      "--ozone-platform=headless",
      "--disable-gpu",
      ...(noSandbox ? ["--no-sandbox"] : []),
    ],
    chromiumSandbox: !noSandbox,
    env: {
      ...process.env,
      APPIMAGE_EXTRACT_AND_RUN: "1",
      CYTOFORGE_HOME: profile,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
  main = await application.firstWindow();
  const flags = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    context_isolation:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences()
        .contextIsolation,
    node_integration:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences()
        .nodeIntegration,
  }));
  assert(flags.packaged && flags.context_isolation && !flags.node_integration);
  assert.equal(flags.no_sandbox, noSandbox);
  evidence.desktop_isolation = flags;
  main.on("pageerror", (error) => errors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
  let source = await fixture(
    "Native template prototype",
    "Prototype.csv",
    5,
    [1, 3],
  );
  const target = await fixture(
    "Native template destination",
    "Destination.csv",
    6,
    [2, 6],
  );
  const definition = {
    id: identifier(),
    name: "Native reusable report",
    pages: [{}, { width_mm: 279.4, height_mm: 215.9 }],
    elements: [
      {
        id: identifier(),
        kind: "plot",
        plot: {
          id: identifier(),
          sample_id: source.samples[0].id,
          gate_id: source.gates[0].id,
          x: "X",
          bins: 16,
          bounds: [0, 8],
        },
      },
      {
        id: identifier(),
        kind: "text",
        page: 1,
        sample_id: source.samples[0].id,
        gate_id: source.gates[0].id,
        text: "{{stat:count}} / {{stat:median:X}}",
        height_mm: 20,
      },
    ],
  };
  source = await request(`/workspaces/${source.id}/layouts/save`, {
    revision: source.revision,
    definition,
  });
  const beforeSource = structuredClone(source);
  const acquired = new Map(
    [source, target].map((doc) => {
      const filename = path.join(
        profile,
        "data/events",
        doc.id,
        doc.samples[0].id + ".npy",
      );
      return [filename, digest(filename)];
    }),
  );
  await studio(source);
  await main
    .getByLabel("Saved report", { exact: true })
    .selectOption(definition.id);
  await expect(main.getByLabel("Report title", { exact: true })).toHaveValue(
    definition.name,
  );
  await application.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, file);
  await main
    .getByRole("button", { name: "Export template", exact: true })
    .click();
  await expect(
    main.getByText(
      "Report template exported with page design and source bindings.",
      { exact: true },
    ),
  ).toBeVisible({ timeout: 30000 });
  const portable = JSON.parse(readFileSync(file, "utf8"));
  assert.equal(portable.format, "cytoforge-report-template");
  assert.equal(portable.source_workspace_id, source.id);
  passed(
    "Native export writes a portable composition through the desktop save dialog",
  );
  await studio(target);
  let imported = await loadTemplate(source, target, "First native import");
  assert.equal(imported.layouts.length, 1);
  assert.equal(
    imported.layouts[0].template_origin.template_sha256,
    portable.sha256,
  );
  assert.deepEqual(scientific(imported), scientific(target));
  await expect(main.getByLabel("Report title", { exact: true })).toHaveValue(
    "First native import",
  );
  passed(
    "Native binding review renders actual destination count and median on the second prototype page",
  );
  let moved = await request(`/workspaces/${target.id}/undo`, {
    revision: imported.revision,
  });
  assert.equal(moved.layouts.length, 0);
  moved = await request(`/workspaces/${target.id}/redo`, {
    revision: moved.revision,
  });
  assert.deepEqual(moved.layouts[0], imported.layouts[0]);
  passed("Imported report is one undoable workspace step with exact redo");
  await studio(moved);
  await main
    .getByLabel("Saved report", { exact: true })
    .selectOption(imported.layouts[0].id);
  await main
    .getByLabel("Report title", { exact: true })
    .fill("Keep my unsaved report");
  imported = await loadTemplate(source, moved, "Second native import", true);
  await expect(main.getByLabel("Report title", { exact: true })).toHaveValue(
    "Keep my unsaved report",
  );
  assert.equal(imported.layouts.length, 2);
  assert.equal(imported.layouts[1].name, "Second native import");
  assert.deepEqual(scientific(imported), scientific(target));
  passed(
    "Import preserves the active unsaved draft and adds a separate saved report",
  );
  const opened = application.waitForEvent("window");
  await main.locator(".sidebar-sample").first().dblclick();
  const popup = await opened;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup
    .locator('.plot-window canvas[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  const context = await popup.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert.equal(context.workspaceId, target.id);
  assert.equal(context.sampleId, target.samples[0].id);
  assert.notEqual(popup, main);
  await popup.evaluate(() => globalThis.cytoforgeDesktop.closePlotWindow());
  await expect.poll(() => popup.isClosed()).toBe(true);
  await expect(main.getByLabel("Report title", { exact: true })).toHaveValue(
    "Keep my unsaved report",
  );
  passed(
    "A separate native plot window opens and closes while the report draft remains intact",
  );
  assert.deepEqual(await request(`/workspaces/${source.id}`), beforeSource);
  for (const [filename, sha256] of acquired)
    assert.equal(digest(filename), sha256);
  assert.deepEqual(errors, []);
  await main.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-report-templates-appimage.png",
    ),
  });
  passed(
    "Source workspace and acquired event bytes are unchanged; renderer reports no errors",
  );
  evidence.status = "passed";
  save();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.message;
  evidence.renderer_errors = errors;
  save();
  throw error;
} finally {
  clearTimeout(deadline);
  await application?.close();
}
