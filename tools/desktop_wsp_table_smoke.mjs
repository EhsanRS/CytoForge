// Saved FlowJo tables through the native desktop, with independent literal truth.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp/desktop-wsp-table-" + Date.now());
const fixture = path.join(root, "artifacts/wsp-table-fixture");
const exportsDirectory = path.join(profile, "exports");
const binary = process.env.CYTOFORGE_TEST_BINARY;
const evidencePath =
  process.env.CYTOFORGE_WSP_TABLE_EVIDENCE ??
  path.join(root, "artifacts/desktop-wsp-table-smoke.json");
const truth = JSON.parse(readFileSync(path.join(fixture, "truth.json")));
const acquisitionNames = ["Zulu control.fcs", "Alpha treated.fcs"];
mkdirSync(exportsDirectory, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
let application, window;
const errors = [];
const timeout = setTimeout(() => application?.process().kill(), 180000);
const digest = (filename) =>
  createHash("sha256").update(readFileSync(filename)).digest("hex");
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
}
async function request(route, body, method = body ? "POST" : "GET") {
  return window.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CytoForge-Token": token,
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body, method },
  );
}
async function nativeDownload(filename, click) {
  const target = path.join(exportsDirectory, filename);
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
    target,
  );
  await click();
  await completion;
  assert.ok(readFileSync(target).length > 0);
  return target;
}
function assertTruth(result) {
  assert.equal(result.total_rows, 2);
  assert.deepEqual(
    result.rows.map((row) => row.sample),
    acquisitionNames,
  );
  for (const [name, expected] of Object.entries(truth)) {
    const column = result.columns.find((column) => column.name === name);
    assert.ok(column, "Missing imported column " + name);
    for (const [index, value] of expected.entries()) {
      const actual = result.rows[index].values[column.id];
      if (typeof value === "number") {
        assert.ok(
          Math.abs(actual - value) <= 1e-10 * Math.max(1, Math.abs(value)),
          name + " row " + index + ": " + actual + " != " + value,
        );
      } else assert.equal(actual, value, name + " row " + index);
    }
  }
  assert.equal(
    result.columns.find((column) => column.name === "Hidden events").hidden,
    true,
  );
  assert.equal(result.provenance.column_compensations.length, 2);
  assert.equal(Object.values(result.rows[0].status).length, 1);
  assert.equal(Object.values(result.rows[1].status).length, 1);
}
async function evaluate(doc, table) {
  const result = await request("/workspaces/" + doc.id + "/tables/evaluate", {
    definition: table,
    revision: doc.revision,
    offset: 0,
    limit: 100,
  });
  assertTruth(result);
  return result;
}
async function showTable(id) {
  await window.getByRole("button", { name: "Statistics", exact: true }).click();
  await window
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await window.getByLabel("Custom saved table").selectOption(id);
  const grid = window.getByRole("table", {
    name: "Custom statistics table",
    exact: true,
  });
  await expect(grid.locator("tbody tr")).toHaveCount(2);
  await expect(grid.locator("tbody tr").first()).toContainText(
    acquisitionNames[0],
  );
  await expect(grid.locator("tbody tr").last()).toContainText(
    acquisitionNames[1],
  );
  await expect(grid.locator("thead")).toContainText("Relative signal");
  await expect(grid.locator("thead")).not.toContainText("Hidden events");
  await expect(
    window.getByRole("button", { name: "JSON", exact: true }),
  ).toBeEnabled();
  await expect(window.locator(".form-error")).toHaveCount(0);
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
    .fill("Native saved FlowJo tables");
  await window
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(window.getByRole("dialog")).not.toBeVisible();
  const chooser = window.waitForEvent("filechooser");
  await window.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (
    await chooser
  ).setFiles(acquisitionNames.map((name) => path.join(fixture, name)));
  await expect(
    window.getByRole("heading", { name: "2 samples imported", exact: true }),
  ).toBeVisible();
  await window.getByRole("button", { name: "Continue", exact: true }).click();
  const workspaceId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  let doc = await request("/workspaces/" + workspaceId);
  assert.deepEqual(
    doc.samples.map((sample) => sample.name),
    acquisitionNames,
  );
  // Only annotation setup uses the private API; migration and exports use the desktop UI.
  for (const [index, sample] of doc.samples.entries()) {
    doc = await request(
      "/workspaces/" + workspaceId + "/samples/" + sample.id,
      {
        revision: doc.revision,
        name: sample.name,
        tags: { dose: String(index + 2) },
      },
      "PATCH",
    );
  }
  await window.reload();
  await window.waitForLoadState("networkidle");
  await window
    .getByRole("button", { name: "Import gates", exact: true })
    .click();
  await window
    .getByLabel("Choose gate XML or FlowJo workspace")
    .setInputFiles(path.join(fixture, "tables.wsp"));
  await expect(window.getByLabel("Saved FlowJo tables")).toContainText(
    "WSP Main",
  );
  await expect(window.getByLabel("Saved FlowJo tables")).toContainText(
    "Control only",
  );
  const sourceSections = window.locator(
    ".interchange-mappings .interchange-source",
  );
  await expect(sourceSections).toHaveCount(2);
  for (const [index, name] of acquisitionNames.entries()) {
    await sourceSections
      .nth(index)
      .getByRole("checkbox", { name, exact: true })
      .check();
  }
  const applyButton = window.getByRole("button", {
    name: "Import selected gates & tables",
    exact: true,
  });
  await expect(applyButton).toBeDisabled();
  await window.getByRole("checkbox", { name: /I reviewed the report/ }).check();
  await applyButton.click();
  await expect(window.locator(".interchange-result")).toContainText(
    "4 gates imported",
  );
  await expect(window.locator(".interchange-result")).toContainText(
    "2 saved tables imported",
  );
  const retainedSource = await nativeDownload("retained-source.wsp", () =>
    window.getByRole("button", { name: "Source XML", exact: true }).click(),
  );
  assert.equal(
    digest(retainedSource),
    digest(path.join(fixture, "tables.wsp")),
  );
  const reportPath = await nativeDownload("conversion-report.json", () =>
    window
      .getByRole("button", { name: "Download report", exact: true })
      .click(),
  );
  const downloadedRecord = JSON.parse(readFileSync(reportPath));
  const report = downloadedRecord.report;
  assert.ok(
    report.issues.some((issue) => issue.code === "native-table-statistics"),
  );
  assert.ok(report.issues.some((issue) => issue.code === "keyword-conflict"));
  await window.getByRole("button", { name: "Done", exact: true }).click();
  doc = await request("/workspaces/" + workspaceId);
  assert.equal(doc.tables.length, 2);
  assert.equal(doc.interchanges[0].table_ids.length, 2);
  const table = doc.tables.find((table) => table.name === "WSP Main");
  assert.equal(table.columns.length, 15);
  const result = await evaluate(doc, table);
  await showTable(table.id);
  const jsonPath = await nativeDownload("imported-table.json", () =>
    window.getByRole("button", { name: "JSON", exact: true }).click(),
  );
  assertTruth(JSON.parse(readFileSync(jsonPath)));
  const xlsxPath = await nativeDownload("imported-table.xlsx", () =>
    window.getByRole("button", { name: "XLSX", exact: true }).click(),
  );
  const archive = await nativeDownload("wsp-tables.cytoforge", () =>
    window.getByRole("button", { name: "Save project", exact: true }).click(),
  );
  const oldPort = new URL(window.url()).port;
  await application.close();
  application = await launch();
  await connect();
  await expect(window.locator(".sidebar-bottom")).toContainText("2 samples");
  const newPort = new URL(window.url()).port;
  assert.notEqual(newPort, oldPort);
  const reopened = await request("/workspaces/" + workspaceId);
  assert.deepEqual(reopened.tables, doc.tables);
  await evaluate(
    reopened,
    reopened.tables.find((value) => value.id === table.id),
  );
  await window
    .getByLabel("Open CytoForge project archive")
    .setInputFiles(archive);
  await expect
    .poll(() => window.evaluate(() => window.cytoforgeDesktop.getWorkspace()))
    .not.toBe(workspaceId);
  await expect(window.locator(".sidebar-bottom")).toContainText("2 samples");
  const restoredId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  const restored = await request("/workspaces/" + restoredId);
  assert.deepEqual(restored.tables, doc.tables);
  assert.deepEqual(restored.interchanges, doc.interchanges);
  await evaluate(
    restored,
    restored.tables.find((value) => value.id === table.id),
  );
  await showTable(table.id);
  await window.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-wsp-table-restored.png",
    ),
  });
  assert.deepEqual(errors, []);
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
        fixture_setup:
          "Owned FCS bytes and WSP; target dose annotations set through the private API. File import, migration, exports, reopening and restore use the desktop UI.",
        columns: table.columns.length,
        imported_tables: doc.tables.length,
        literal_truth: truth,
        evaluated: result,
        ports: [oldPort, newPort],
        page_errors: errors,
        exports: Object.fromEntries(
          Object.entries({
            json: jsonPath,
            xlsx: xlsxPath,
            source: retainedSource,
            report: reportPath,
            project: archive,
          }).map(([name, filename]) => [
            name,
            { path: filename, sha256: digest(filename) },
          ]),
        ),
        workflow: [
          "native FCS chooser",
          "saved-table preview",
          "explicit conversion acknowledgement",
          "source keyword conflicts preserve target annotations",
          "stable source gate and compensation bindings",
          "first-source control",
          "numeric formulas and row references",
          "hidden formula input",
          "native JSON and XLSX downloads",
          "byte-exact retained XML",
          "restart with a new private engine port",
          "portable project restore",
        ],
      },
      null,
      2,
    ) + "\n",
  );
  process.stdout.write("Native desktop saved FlowJo tables passed\n");
} finally {
  clearTimeout(timeout);
  await application?.close().catch(() => {});
}
