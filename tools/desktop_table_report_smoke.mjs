// Native editor and PDF checks against eight acquisitions with independent truth.
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import {
  createReadStream,
  mkdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";
import {
  PDFDocument,
  PDFName,
  PDFDict,
  PDFArray,
  PDFRawStream,
  decodePDFRawStream,
} from "pdf-lib";

const root = process.cwd();
const geometryCheck = process.env.CYTOFORGE_REPORT_TABLE_GEOMETRY_TEST === "1";
const profile = path.join(root, `.tmp/desktop-table-report-${Date.now()}`);
const binary =
  process.env.CYTOFORGE_TEST_BINARY ??
  (geometryCheck
    ? path.join(
        root,
        "artifacts/candidates/table-geometry/desktop/CytoForge-0.1.0.AppImage",
      )
    : undefined);
let binaryHash;
const evidencePath =
  process.env.CYTOFORGE_TABLE_REPORT_EVIDENCE ??
  path.join(
    root,
    geometryCheck
      ? "artifacts/desktop-table-geometry-appimage.json"
      : "artifacts/desktop-table-report-smoke.json",
  );
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const identifier = () => randomUUID().replaceAll("-", "");
let application, window;
const errors = [];
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
      ...(binary?.endsWith(".AppImage")
        ? { APPIMAGE_EXTRACT_AND_RUN: "1" }
        : {}),
      CYTOFORGE_HOME: profile,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
}
async function connect() {
  window = await application.firstWindow();
  await window.setViewportSize({ width: 1540, height: 1050 });
  window.on("pageerror", (error) => errors.push(error.message));
  await window.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  return window.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch(`/api${route}`, {
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
async function ready() {
  await expect(window.locator(".studio-canvas-status")).not.toContainText(
    "Updating figures",
    { timeout: 30000 },
  );
  await expect(window.locator(".studio-svg")).toContainText("Rows", {
    timeout: 30000,
  });
  await expect(window.locator(".form-error")).toHaveCount(0);
}
async function addTable(tableId, view) {
  await window.getByLabel("Report content").selectOption(`table:${tableId}`);
  await window.getByRole("button", { name: "Add object", exact: true }).click();
  await window.getByLabel("Report table view").selectOption(view);
  await window.getByLabel("Table row count").fill("2");
  await window.getByLabel("Table columns per page").fill("2");
  await expect(window.getByLabel("Automatically continue table")).toBeChecked();
  await ready();
}
async function openMeasures() {
  const summary = window.getByText("Measures to include", { exact: true });
  if ((await summary.locator("..").getAttribute("open")) === null)
    await summary.click();
}
const timeout = setTimeout(() => application?.process().kill(), 180000);
try {
  if (geometryCheck) {
    const relative = path.relative(root, realpathSync(binary));
    if (
      path.isAbsolute(relative) ||
      relative === ".." ||
      relative.startsWith(`..${path.sep}`)
    )
      throw new Error(
        "The table geometry candidate must be inside this checkout",
      );
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(binary)) hash.update(chunk);
    binaryHash = hash.digest("hex");
  }
  application = await launch();
  await connect();
  await window
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await window
    .getByLabel("Experiment name", { exact: true })
    .fill("Native continued cohort truth");
  await window
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(window.getByRole("dialog")).not.toBeVisible();
  await window.getByLabel("Import FCS or CSV files").setInputFiles(
    Array.from({ length: 8 }, (_, index) => ({
      name: `Acquisition ${index + 1}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(
        "X\n" +
          Array(index + 1)
            .fill(String(index + 1))
            .join("\n") +
          "\n",
      ),
    })),
  );
  await expect(
    window.getByRole("button", { name: "Continue", exact: true }),
  ).toBeVisible();
  await window.getByRole("button", { name: "Continue", exact: true }).click();
  const workspaceId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  let doc = await request(`/workspaces/${workspaceId}`);
  assert.equal(doc.samples.length, 8);
  for (const [index, sample] of doc.samples.entries()) {
    doc = await request(
      `/workspaces/${workspaceId}/samples/${sample.id}`,
      {
        revision: doc.revision,
        name: sample.name,
        tags: { Donor: `D${index % 4}`, Treatment: index < 4 ? "A" : "B" },
      },
      "PATCH",
    );
  }
  const [donor, treatment, signal, count, zero] = [
    { name: "Donor", kind: "metadata", metadata_key: "Donor" },
    { name: "Treatment", kind: "metadata", metadata_key: "Treatment" },
    { name: "Signal", kind: "statistic", statistic: "mean", channel: "X" },
    { name: "Events", kind: "statistic", statistic: "count", decimals: 0 },
    { name: "Zero", kind: "formula", expression: "0", decimals: 0 },
  ].map((column) => ({ ...column, id: identifier() }));
  const tableId = identifier();
  doc = await request(`/workspaces/${workspaceId}/tables/save`, {
    revision: doc.revision,
    definition: {
      id: tableId,
      name: "Full cohort truth",
      row_mode: "samples",
      columns: [donor, treatment, signal, count, zero],
      pivot: {
        rows: [donor.id],
        columns: [treatment.id],
        measures: [signal.id, count.id, zero.id],
        aggregation: "mean",
      },
      comparison: {
        group_column: treatment.id,
        group_a: "A",
        group_b: "B",
        measures: [signal.id, count.id, zero.id],
        method: "welch",
        adjustment: "holm",
      },
    },
  });
  await window.reload();
  await window
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await window
    .getByLabel("Report title")
    .fill("Continued full-cohort analysis");
  await addTable(tableId, "data");
  await openMeasures();
  for (const name of ["Signal", "Events", "Zero"])
    await window.getByLabel(`Report measure ${name}`, { exact: true }).check();
  if (geometryCheck) {
    await window.getByText("Column and cell layout", { exact: true }).click();
    await window
      .getByLabel("Default column width (mm)", { exact: true })
      .fill("50");
    await window
      .getByLabel("Default row height (mm)", { exact: true })
      .fill("10");
    await window.getByLabel("Heading height (mm)", { exact: true }).fill("10");
    await window
      .getByLabel("Formatted table column", { exact: true })
      .selectOption("0");
    await window
      .getByLabel("Selected column width (mm)", { exact: true })
      .fill("40");
    await window
      .getByLabel("Formatted table column", { exact: true })
      .selectOption("1");
    await window
      .getByLabel("Selected column width (mm)", { exact: true })
      .fill("35");
    await window
      .getByLabel("Formatted table column", { exact: true })
      .selectOption("2");
    await window
      .getByLabel("Format table heading cell", { exact: true })
      .uncheck();
    await window.getByLabel("Formatted table row", { exact: true }).fill("2");
    await window
      .getByLabel("Selected row height (mm)", { exact: true })
      .fill("20");
    await window.getByLabel("Cell font size (pt)", { exact: true }).fill("12");
    await window
      .getByLabel("Cell text alignment", { exact: true })
      .selectOption("right");
    await ready();
    const styled = window.locator(
      '.studio-svg svg[data-table-row="1"][data-table-column="2"]',
    );
    await expect(styled.locator("text").first()).toHaveAttribute(
      "text-anchor",
      "end",
    );
    await expect(styled).toHaveAttribute("height", "20");
    assert.ok(
      Math.abs(
        Number(await styled.locator("text").first().getAttribute("font-size")) -
          (12 * 25.4) / 72,
      ) < 1e-9,
    );
  }
  await ready();
  await window.getByRole("button", { name: "Add page", exact: true }).click();
  await window.getByLabel("Page size").selectOption("215.9,279.4");
  await addTable(tableId, "pivot");
  await expect(
    window.getByLabel("Show pivot contributing counts"),
  ).toBeChecked();
  await expect(window.locator(".studio-svg")).toContainText("n=1");
  await window.getByRole("button", { name: "Add page", exact: true }).click();
  await addTable(tableId, "comparisons");
  await openMeasures();
  await window.getByLabel("Report measure Signal", { exact: true }).check();
  for (const name of [
    "n (B)",
    "Mean (A)",
    "Mean (B)",
    "Raw p",
    "Confidence interval",
  ])
    await window
      .getByLabel(`Comparison field ${name}`, { exact: true })
      .uncheck();
  await ready();
  await window
    .getByRole("button", { name: "Output preview", exact: true })
    .click();
  await ready();
  await expect(window.getByLabel("Report page").locator("option")).toHaveCount(
    16,
  );
  await window.getByLabel("Report page").selectOption("8");
  await ready();
  await expect(window.locator(".studio-canvas-status")).toContainText(
    "215.9 × 279.4",
  );
  await window.getByLabel("Report page").selectOption("15");
  await ready();
  await expect(window.locator(".studio-svg")).toContainText("Adjusted p");
  await window.getByRole("button", { name: "Design", exact: true }).click();
  await ready();
  await window.getByRole("button", { name: /^Save layout/ }).click();
  await expect(window.getByLabel("Saved report")).not.toHaveValue("");
  await ready();
  const exportPath = path.join(profile, "exports", "continued-cohort.pdf");
  await application.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, exportPath);
  await window.getByRole("button", { name: "Export PDF", exact: true }).click();
  await expect(window.locator(".report-message")).toContainText("PDF saved", {
    timeout: 60000,
  });
  const bytes = readFileSync(exportPath);
  const pdf = await PDFDocument.load(bytes);
  assert.equal(pdf.getPageCount(), 16);
  for (const [index, page] of pdf.getPages().entries()) {
    const expected = index >= 8 && index < 14 ? [215.9, 279.4] : [210, 297];
    const size = page.getSize();
    assert(Math.abs(size.width - (expected[0] * 72) / 25.4) < 0.8);
    assert(Math.abs(size.height - (expected[1] * 72) / 25.4) < 0.8);
  }
  const names = pdf.context.lookup(
    pdf.catalog.get(PDFName.of("Names")),
    PDFDict,
  );
  const files = pdf.context.lookup(
    names.get(PDFName.of("EmbeddedFiles")),
    PDFDict,
  );
  const entries = pdf.context.lookup(files.get(PDFName.of("Names")), PDFArray);
  const spec = pdf.context.lookup(entries.get(1), PDFDict);
  const ef = pdf.context.lookup(spec.get(PDFName.of("EF")), PDFDict);
  const stream = pdf.context.lookup(ef.get(PDFName.of("F")), PDFRawStream);
  const manifest = JSON.parse(
    Buffer.from(decodePDFRawStream(stream).decode()).toString("utf8"),
  );
  assert.equal(manifest.pages.length, 16);
  if (geometryCheck) {
    const dataPages = manifest.pages.filter(
      (page) => page.elements[0]?.table_view === "data",
    );
    assert.equal(dataPages.length, 8);
    for (const page of dataPages) {
      const geometry = page.elements[0].physical_geometry;
      assert.equal(geometry.header_height_mm, 10);
      assert.deepEqual(
        geometry.columns.slice(0, 2).map((column) => column.width_mm),
        [40, 35],
      );
      const row = geometry.rows.find((row) => row.index === 1);
      if (row) assert.equal(row.height_mm, 20);
      const saved = page.definition.elements.find(
        (element) => element.table_view === "data",
      ).table_geometry;
      assert.equal(saved.cell_styles[0].font_size_pt, 12);
      assert.equal(saved.cell_styles[0].row, 1);
      assert.equal(saved.cell_styles[0].column, 2);
    }
  }
  const rawCells = new Map(),
    pivotCells = new Map();
  for (const page of manifest.pages) {
    const frame = page.elements[0];
    assert.equal(frame.input_rows, 8);
    for (const row of frame.rows) {
      for (const column of frame.columns) {
        if (frame.table_view === "data") {
          const key = `${row.sample_id}:${column.id}`;
          assert(!rawCells.has(key));
          rawCells.set(key, row.values[column.id]);
        } else if (frame.table_view === "pivot") {
          const key = `${row.group[0]}:${column.dimension[0]}:${column.measure}`;
          assert(!pivotCells.has(key));
          pivotCells.set(key, [row.values[column.id], row.counts[column.id]]);
        } else {
          assert.equal(row.column_id, signal.id);
          assert.equal(row.n_a, 4);
          assert.equal(row.n_b, 4);
          assert(Math.abs(row.mean_difference + 4) < 1e-12);
          assert(Math.abs(row.p_value - 0.004659214943993934) < 1e-12);
          assert(Math.abs(row.adjusted_p_value - 0.009318429887987869) < 1e-12);
        }
      }
    }
  }
  assert.equal(rawCells.size, 24);
  assert.equal(pivotCells.size, 24);
  for (const [index, sample] of doc.samples.entries())
    for (const column of [signal, count, zero])
      assert.equal(
        rawCells.get(`${sample.id}:${column.id}`),
        column.id === zero.id ? 0 : index + 1,
      );
  for (let i = 0; i < 4; i++)
    for (const condition of ["A", "B"])
      for (const column of [signal, count, zero])
        assert.deepEqual(pivotCells.get(`D${i}:${condition}:${column.id}`), [
          column.id === zero.id ? 0 : i + (condition === "A" ? 1 : 5),
          1,
        ]);
  // Verify draft persistence retains the new table controls through a new engine port.
  await window.locator(".studio-object-list button").first().click();
  await window.getByLabel("Table row count").fill("3");
  await ready();
  const originBefore = new URL(window.url()).origin;
  await application.close();
  application = null;
  application = await launch();
  await connect();
  await window
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await ready();
  await window.locator(".studio-object-list button").first().click();
  await expect(window.getByLabel("Table row count")).toHaveValue("3");
  await expect(window.getByLabel("Automatically continue table")).toBeChecked();
  await window
    .getByRole("button", { name: "Output preview", exact: true })
    .click();
  await ready();
  await expect(window.getByLabel("Report page").locator("option")).toHaveCount(
    14,
  );
  const originAfter = new URL(window.url()).origin;
  if (geometryCheck) {
    await window.getByLabel("Report page").selectOption("0");
    await ready();
    const restored = window.locator(
      '.studio-svg svg[data-table-row="1"][data-table-column="2"]',
    );
    await expect(restored).toHaveAttribute("height", "20");
    await expect(restored.locator("text").first()).toHaveAttribute(
      "text-anchor",
      "end",
    );
  }
  assert.notEqual(originBefore, originAfter);
  assert.deepEqual(errors, []);
  await window.screenshot({
    path: path.join(
      root,
      geometryCheck
        ? "artifacts/screenshots/desktop-table-geometry.png"
        : "artifacts/screenshots/desktop-continued-table.png",
    ),
  });
  const native = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    preferences:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
  }));
  if (geometryCheck) {
    assert(native.packaged);
    assert(native.preferences.contextIsolation);
    assert(!native.preferences.nodeIntegration);
    assert.equal(
      native.no_sandbox,
      process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
    );
    const restored = await request(`/workspaces/${workspaceId}`);
    assert.equal(
      restored.layouts[0].elements[0].table_geometry.row_heights_mm["1"],
      20,
    );
  }
  writeFileSync(
    evidencePath,
    JSON.stringify(
      {
        status: "passed",
        validated_at: new Date().toISOString(),
        binary: binary ?? "development Electron",
        native,
        table_geometry: geometryCheck
          ? {
              binary_sha256: binaryHash,
              native_size_and_cell_controls: true,
              actual_svg_cell_geometry: true,
              pdf_attachment_geometry: true,
              saved_geometry_restored: true,
            }
          : undefined,
        fixture_setup:
          "CSV imported through desktop; sample metadata and saved table configured through its private engine; report views, selection, pagination, save and export exercised through native UI",
        pdf: {
          path: exportPath,
          bytes: bytes.length,
          pages: pdf.getPageCount(),
          embedded_manifest: true,
          sha256: createHash("sha256").update(bytes).digest("hex"),
          mixed_a4_letter: true,
        },
        cohort_samples: 8,
        raw_cells_checked: rawCells.size,
        pivot_cells_checked: pivotCells.size,
        full_cohort_comparison: {
          n_a: 4,
          n_b: 4,
          mean_difference: -4,
          p_value: 0.004659214943993934,
          holm_adjusted_p_value: 0.009318429887987869,
          valid_correction_family: 2,
        },
        new_controls_restored: true,
        engine_origins: [originBefore, originAfter],
        renderer_errors: errors,
      },
      null,
      2,
    ),
  );
  console.log(
    "Native continued report passed: 16 PDF pages, all raw/pivot cells, full-cohort corrected comparison, mixed page geometry and draft recovery.",
  );
} catch (error) {
  await window
    ?.screenshot({
      path: path.join(
        root,
        geometryCheck
          ? "artifacts/screenshots/desktop-table-geometry-failure.png"
          : "artifacts/screenshots/desktop-table-report-failure.png",
      ),
    })
    .catch(() => {});
  writeFileSync(
    evidencePath,
    JSON.stringify(
      {
        status: "failed",
        validated_at: new Date().toISOString(),
        error: error.message,
        renderer_errors: errors,
      },
      null,
      2,
    ),
  );
  throw error;
} finally {
  clearTimeout(timeout);
  if (application) await application.close().catch(() => {});
}
