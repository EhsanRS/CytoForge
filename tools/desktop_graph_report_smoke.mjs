// Native publication export for every new graph, using a fresh synthetic project.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
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
const profile = path.join(root, ".tmp/desktop-graph-report-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_GRAPH_REPORT_EVIDENCE ||
  path.join(root, "artifacts/desktop-graph-report.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(profile, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
};
writeFileSync(evidencePath, JSON.stringify(evidence));
const id = () => randomUUID().replaceAll("-", "");
let application, main;
const errors = [];
const deadline = setTimeout(() => application?.process().kill(), 180000);
async function request(route, body) {
  return main.evaluate(
    async ({ route, body }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        method: body ? "POST" : "GET",
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body },
  );
}
async function ready() {
  await expect(
    main.locator('.studio-svg [data-ready="true"]').first(),
  ).toBeVisible({ timeout: 30000 });
  await expect(main.locator(".studio-canvas-status")).not.toContainText(
    "Updating figures",
  );
}
try {
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
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native probability publication");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const source = path.join(profile, "graph-reference.csv");
  writeFileSync(
    source,
    "X,Y,Z,C,S\n0,0,0,0,0\n0,0,0,0,0\n1,1,1,10,100\n2,2,2,20,200\n3,3,3,30,300\nnan,0,0,0,0\n",
  );
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(source);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await main.locator('canvas[data-ready="true"]').waitFor();
  const workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await request("/workspaces/" + workspaceId);
  const sample = doc.samples[0];
  assert.equal(sample.event_count, 6);
  const modes = ["cdf", "contour", "zebra", "pseudocolor", "3d"];
  const layout = {
    id: id(),
    name: "Native probability publication",
    show_header: true,
    show_footer: true,
    pages: modes.map(() => ({ width_mm: 210, height_mm: 297, margin_mm: 12 })),
    batch: { mode: "off" },
    elements: modes.map((mode, page) => ({
      id: id(),
      kind: "plot",
      page,
      title: mode,
      x_mm: 15,
      y_mm: 40,
      width_mm: 175,
      height_mm: 150,
      plot: {
        id: id(),
        sample_id: sample.id,
        x: "X",
        y: mode === "cdf" ? null : "Y",
        mode,
        bins: 32,
        bounds:
          mode === "3d"
            ? [0, 3, 0, 3, 0, 3]
            : mode === "cdf"
              ? [0, 2]
              : [0, 3, 0, 3],
        ...(mode === "3d"
          ? {
              three_d: {
                z: "Z",
                color_by: "C",
                size_by: "S",
                yaw: 0.7,
                pitch: -0.4,
                zoom: 1.3,
                pan: [0.1, -0.2],
              },
            }
          : {}),
        graph_options: {
          palette: "viridis",
          axis_extent: "full",
          smooth: true,
        },
      },
    })),
  };
  doc = await request("/workspaces/" + workspaceId + "/layouts/save", {
    revision: doc.revision,
    definition: layout,
  });
  await main.reload();
  await main
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await ready();
  await expect(main.getByLabel("Report title")).toHaveValue(layout.name);
  for (let page = 0; page < modes.length; page++) {
    await main.getByLabel("Report page").selectOption(String(page));
    await ready();
    const response = await request(
      "/workspaces/" + workspaceId + "/reports/render",
      {
        revision: doc.revision,
        definition: layout,
        page,
        validate_sources: true,
      },
    );
    assert(response.exportable);
    const panel = response.manifest.elements[0];
    assert.equal(panel.mode, modes[page]);
    assert.equal(panel.layers[0].population_count, 6);
    assert.equal(panel.layers[0].finite_count, 5);
    if (page === 0) {
      assert.equal(panel.layers[0].cdf_denominator, 5);
      assert.equal(panel.layers[0].displayed_values[0], 40);
      assert.equal(panel.layers[0].displayed_values.at(-1), 80);
    }
    await main.screenshot({
      path: path.join(
        root,
        "artifacts/screenshots/desktop-report-" + modes[page] + ".png",
      ),
    });
  }
  const review = main.getByRole("button", {
    name: "Review source mappings",
    exact: true,
  });
  if (await review.isVisible()) {
    await review.click();
    await main
      .getByRole("button", {
        name: "Accept these source mappings",
        exact: true,
      })
      .click();
  }
  await ready();
  const output = path.join(profile, "exports/probability-graphs.pdf");
  await application.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, output);
  await main.getByRole("button", { name: "Export PDF", exact: true }).click();
  await expect(main.locator(".report-message")).toContainText("PDF saved", {
    timeout: 45000,
  });
  const bytes = readFileSync(output);
  const pdf = await PDFDocument.load(bytes);
  assert.equal(pdf.getPageCount(), modes.length);
  for (const page of pdf.getPages()) {
    assert(Math.abs(page.getWidth() - (210 * 72) / 25.4) < 0.8);
    assert(Math.abs(page.getHeight() - (297 * 72) / 25.4) < 0.8);
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
  assert.deepEqual(
    manifest.pages.map((p) => p.elements[0].mode),
    modes,
  );
  for (const page of manifest.pages) {
    const scientific = page.elements[0].layers[0];
    assert.equal(scientific.population_count, 6);
    assert.equal(scientific.finite_count, 5);
    assert.equal(scientific.sample_sha256, sample.sha256);
    assert.equal(scientific.graph_options.palette, "viridis");
  }
  assert.equal(manifest.pages[0].elements[0].layers[0].cdf_denominator, 5);
  assert.equal(
    manifest.pages[0].elements[0].layers[0].displayed_values.at(-1),
    80,
  );
  assert(manifest.pages[1].elements[0].layers[0].contour_vertices > 0);
  assert(manifest.pages[2].elements[0].layers[0].probability_levels.length > 0);
  assert.deepEqual(errors, []);
  const native = await application.evaluate(({ app }) => ({
    packaged: app.isPackaged,
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
  }));
  Object.assign(evidence, {
    status: "passed",
    validated_at: new Date().toISOString(),
    native,
    pdf: {
      path: output,
      bytes: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      pages: modes.length,
    },
    modes,
    source_sha256: sample.sha256,
    manifest,
    renderer_errors: errors,
  });
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  console.log(
    JSON.stringify({
      status: "passed",
      packaged: native.packaged,
      pages: modes.length,
      report: evidencePath,
    }),
  );
} catch (error) {
  Object.assign(evidence, {
    status: "failed",
    error: String(error.stack || error),
    renderer_errors: errors,
  });
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  throw error;
} finally {
  clearTimeout(deadline);
  try {
    if (application) await application.close();
  } catch {
    application?.process().kill("SIGTERM");
  }
}
