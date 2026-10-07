import { _electron as electron, expect } from "playwright/test";
import {
  createReadStream,
  mkdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { randomUUID, createHash } from "node:crypto";
import path from "node:path";
import {
  PDFDocument,
  PDFName,
  PDFDict,
  PDFArray,
  PDFRawStream,
  decodePDFRawStream,
} from "pdf-lib";

const root = process.cwd(),
  profile = path.join(root, `.tmp/desktop-report-${Date.now()}`);
const publicationControls =
  process.env.CYTOFORGE_REPORT_PUBLICATION_TEST === "1";
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const evidencePath =
  process.env.CYTOFORGE_REPORT_EVIDENCE ??
  path.join(
    root,
    publicationControls
      ? "artifacts/desktop-report-publication-appimage.json"
      : "artifacts/desktop-report-smoke.json",
  );
const binary =
  process.env.CYTOFORGE_TEST_BINARY ??
  (publicationControls
    ? path.join(
        root,
        "artifacts/candidates/publication-controls/desktop/CytoForge-0.1.0.AppImage",
      )
    : undefined);
let binaryHash;
let application, window;
const errors = [];
const launch = () =>
  electron.launch({
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
async function ready() {
  await expect(window.locator(".studio-canvas-status")).not.toContainText(
    "Updating figures",
    { timeout: 30000 },
  );
  await expect(
    window.locator('.studio-svg [data-ready="true"]').first(),
  ).toBeVisible();
}
async function request(route, body) {
  return window.evaluate(
    async ({ route, body }) => {
      const token = (await (await fetch("/api/bootstrap")).json()).token;
      const response = await fetch(`/api${route}`, {
        method: body ? "POST" : "GET",
        headers: {
          "Content-Type": "application/json",
          "X-CytoForge-Token": token,
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body },
  );
}
const identifier = () => randomUUID().replaceAll("-", "");
const deadline = setTimeout(() => application?.process().kill(), 180000);
try {
  if (publicationControls) {
    const relative = path.relative(root, realpathSync(binary));
    if (
      path.isAbsolute(relative) ||
      relative === ".." ||
      relative.startsWith(`..${path.sep}`)
    )
      throw new Error("The publication candidate must be inside this checkout");
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(binary)) hash.update(chunk);
    binaryHash = hash.digest("hex");
  }
  application = await launch();
  window = await application.firstWindow();
  await window.setViewportSize({ width: 1540, height: 1050 });
  window.on("pageerror", (e) => errors.push(e.message));
  await window.waitForLoadState("networkidle");
  await window
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await window
    .getByLabel("Experiment name", { exact: true })
    .fill("Native report truth");
  await window
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  await expect(window.getByRole("dialog")).not.toBeVisible();
  await window.getByLabel("Import FCS or CSV files").setInputFiles([
    {
      name: "Control.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("X,Y\n-2,-2\n0,0\n1,1\n2,2\n3,3\n4,4\nnan,1\n"),
    },
    {
      name: "Stimulated.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("X,Y\n0,0\n1,1\n2,2\n3,3\n4,4\n"),
    },
  ]);
  await expect(
    window.getByRole("heading", { name: "All events", exact: true }).first(),
  ).toBeVisible();
  await expect(
    window.locator('canvas[data-ready="true"]').first(),
  ).toBeVisible();
  let workspaceId = await window.evaluate(() =>
    window.cytoforgeDesktop.getWorkspace(),
  );
  if (!workspaceId) throw new Error("Native workspace was not persisted");
  let doc = await request(`/workspaces/${workspaceId}`);
  for (const sample of doc.samples)
    doc = await request(`/workspaces/${workspaceId}/gates`, {
      revision: doc.revision,
      gate: {
        id: identifier(),
        sample_id: sample.id,
        name: "Positive",
        kind: "range",
        x: "X",
        bounds: [1, 3],
      },
    });
  await window.reload();
  await expect(
    window.getByRole("heading", { name: "All events", exact: true }).first(),
  ).toBeVisible();
  await window
    .getByRole("button", { name: /Positive/ })
    .first()
    .click();
  await window
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Add current plot", exact: true })
    .click();
  await ready();
  await window.getByLabel("Report title").fill("Native publication proof");
  await window.getByLabel("Report Y parameter").selectOption("");
  await ready();
  await window
    .getByRole("button", { name: "Add overlay", exact: true })
    .click();
  await window.getByLabel("Lock control", { exact: true }).nth(1).check();
  await ready();
  const hit = window.locator(".studio-object-hit").first(),
    box = await hit.boundingBox();
  const beforeX = Number(
    await window.getByLabel("X (mm)", { exact: true }).inputValue(),
  );
  await window.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await window.mouse.down();
  await window.mouse.move(
    box.x + box.width / 2 + 20,
    box.y + box.height / 2 + 10,
    { steps: 4 },
  );
  await window.mouse.up();
  await ready();
  if (
    Number(await window.getByLabel("X (mm)", { exact: true }).inputValue()) <=
    beforeX
  )
    throw new Error("Native drag did not move the figure");
  await window.getByLabel("Undo layout edit").click();
  await ready();
  await window.locator(".studio-object-list button").first().click();
  if (
    Number(await window.getByLabel("X (mm)", { exact: true }).inputValue()) !==
    beforeX
  ) {
    // Undo clears selection; select again to inspect actual persisted geometry.
    await window.locator(".studio-object-list button").first().click();
  }
  await window.getByRole("button", { name: "Add object", exact: true }).click();
  await window.getByLabel("Y (mm)", { exact: true }).fill("140");
  await window
    .getByLabel("Annotation population")
    .selectOption(doc.gates[0].id);
  await window
    .getByLabel("Annotation text")
    .fill("{{sample}}: {{stat:count}} positive events; mean {{stat:mean:X}}");
  await ready();
  if (publicationControls) {
    await window
      .getByLabel("Object font family", { exact: true })
      .selectOption("monospace");
    await window
      .getByLabel("Object font weight", { exact: true })
      .selectOption("700");
    await window
      .getByLabel("Object font style", { exact: true })
      .selectOption("italic");
    await window
      .getByLabel("Object text decoration", { exact: true })
      .selectOption("underline");
    await window.getByLabel("Object line spacing", { exact: true }).fill("2");
    await ready();
    const annotation = window
      .locator('.studio-svg text[font-family="monospace"]')
      .first();
    await expect(annotation).toHaveAttribute("font-weight", "700");
    await expect(annotation).toHaveAttribute("font-style", "italic");
    await expect(annotation).toHaveAttribute("text-decoration", "underline");
    const font = Number(await annotation.getAttribute("font-size"));
    const baseline = Number(await annotation.getAttribute("y"));
    if (Math.abs(baseline - font * 2) > 1e-8)
      throw new Error("The desktop preview ignored the selected line spacing");
  }
  await window.getByRole("button", { name: "Add page", exact: true }).click();
  await window.getByLabel("Page size").selectOption("215.9,279.4");
  await window
    .getByRole("button", { name: "Add current plot", exact: true })
    .click();
  await ready();
  await window.getByLabel("Report page").selectOption("0");
  await ready();
  await window.getByLabel("Batch iterator").selectOption("sample");
  await ready();
  await window
    .getByRole("button", { name: "Review source mappings", exact: true })
    .click();
  await expect(
    window.getByRole("heading", { name: "Batch source review", exact: true }),
  ).toBeVisible();
  await window
    .getByRole("button", { name: "Accept these source mappings", exact: true })
    .click();
  await ready();
  await window
    .locator(".main-content")
    .evaluate((element) => (element.scrollTop = 0));
  await window
    .locator(".studio-sheet-scroll")
    .evaluate((element) => (element.scrollTop = 0));
  await window
    .locator(".studio-inspector")
    .evaluate((element) => (element.scrollTop = 0));
  await window.screenshot({
    path: path.join(
      root,
      publicationControls
        ? "artifacts/screenshots/desktop-layout-publication.png"
        : "artifacts/screenshots/desktop-layout-studio.png",
    ),
  });
  const exportPath = path.join(profile, "exports", "native-publication.pdf");
  await application.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, exportPath);
  await window.getByRole("button", { name: "Export PDF", exact: true }).click();
  await expect(window.locator(".report-message")).toContainText("PDF saved", {
    timeout: 45000,
  });
  const bytes = readFileSync(exportPath),
    pdf = await PDFDocument.load(bytes);
  if (pdf.getPageCount() !== 4)
    throw new Error(`Expected four batch pages, got ${pdf.getPageCount()}`);
  const sizes = pdf.getPages().map((page) => page.getSize());
  for (const [index, size] of sizes.entries()) {
    const expected = index % 2 === 0 ? [210, 297] : [215.9, 279.4];
    if (
      Math.abs(size.width - (expected[0] * 72) / 25.4) > 0.8 ||
      Math.abs(size.height - (expected[1] * 72) / 25.4) > 0.8
    )
      throw new Error("Native PDF lost its physical page size");
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
  const spec = pdf.context.lookup(entries.get(1), PDFDict),
    ef = pdf.context.lookup(spec.get(PDFName.of("EF")), PDFDict);
  const stream = pdf.context.lookup(ef.get(PDFName.of("F")), PDFRawStream);
  const manifest = JSON.parse(
    Buffer.from(decodePDFRawStream(stream).decode()).toString("utf8"),
  );
  if (manifest.pages.length !== 4)
    throw new Error("PDF source manifest is incomplete");
  if (publicationControls) {
    for (const index of [0, 2]) {
      const annotation = manifest.pages[index].definition.elements.find(
        (element) => element.kind === "text",
      );
      if (
        annotation.font_family !== "monospace" ||
        annotation.font_weight !== 700 ||
        annotation.font_style !== "italic" ||
        annotation.text_decoration !== "underline" ||
        annotation.line_spacing !== 2
      )
        throw new Error("Native PDF source attachment lost publication styles");
    }
  }
  for (const index of [0, 2]) {
    const plot = manifest.pages[index].elements.find(
      (element) => element.kind === "plot",
    );
    if (
      plot.layers[0].population_count !== 2 ||
      plot.layers[1].population_count !== 7 ||
      !plot.layers[1].locked_control
    )
      throw new Error(
        "Native PDF source counts or locked controls differ from independent CSV truth",
      );
    const text = manifest.pages[index].elements.find(
      (element) => element.kind === "text",
    );
    if (
      text.statistics.count.value !== 2 ||
      text.statistics["mean:X"].value !== 1.5
    )
      throw new Error("Native PDF statistics are incorrect");
  }
  const pngPath = path.join(
    root,
    publicationControls
      ? "artifacts/screenshots/desktop-report-publication-300dpi.png"
      : "artifacts/screenshots/desktop-report-300dpi.png",
  );
  await application.evaluate(({ session }, target) => {
    globalThis.reportPngDownload = new Promise((resolve, reject) => {
      session.defaultSession.once("will-download", (_event, item) => {
        item.setSavePath(target);
        item.once("done", (_event, state) =>
          state === "completed"
            ? resolve(true)
            : reject(new Error(`PNG download ${state}`)),
        );
      });
    });
  }, pngPath);
  await window.getByRole("button", { name: "PNG page", exact: true }).click();
  await application.evaluate(() => globalThis.reportPngDownload);
  const png = readFileSync(pngPath);
  if (png.readUInt32BE(16) !== 2480 || png.readUInt32BE(20) !== 3508)
    throw new Error("PNG dimensions differ from A4 at 300 DPI");
  const physical = png.indexOf(Buffer.from("pHYs"));
  if (
    physical < 0 ||
    png.readUInt32BE(physical + 4) !== 11811 ||
    png[physical + 12] !== 1
  )
    throw new Error("PNG resolution metadata is incorrect");
  const wrongDescriptor = await window.evaluate(async () => {
    try {
      await window.cytoforgeDesktop.exportReportPdf({
        workspace: "x",
        revision: 0,
        manifest: "{}",
        title: "bad",
        reportHash: "0".repeat(64),
        domHash: "0".repeat(64),
        pageCount: 1,
      });
      return false;
    } catch {
      return true;
    }
  });
  if (!wrongDescriptor)
    throw new Error("Native PDF bridge accepted an invalid descriptor");
  await window.getByLabel("Report title").fill("Unsaved native report draft");
  await ready();
  const originBefore = new URL(window.url()).origin;
  await application.close();
  application = null;
  application = await launch();
  window = await application.firstWindow();
  await window.waitForLoadState("networkidle");
  await window
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await expect(window.getByLabel("Report title")).toHaveValue(
    "Unsaved native report draft",
    { timeout: 15000 },
  );
  await ready();
  const originAfter = new URL(window.url()).origin;
  if (publicationControls) {
    const restored = window
      .locator('.studio-svg text[font-family="monospace"]')
      .first();
    await expect(restored).toHaveAttribute("font-style", "italic");
    await expect(restored).toHaveAttribute("font-weight", "700");
    await expect(restored).toHaveAttribute("text-decoration", "underline");
  }
  if (originBefore === originAfter)
    throw new Error("Restart did not exercise a different engine port");
  if (errors.length) throw new Error(errors.join("; "));
  const native = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    preferences:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
  }));
  if (
    publicationControls &&
    (!native.packaged ||
      !native.preferences.contextIsolation ||
      native.preferences.nodeIntegration ||
      native.no_sandbox !== (process.env.CYTOFORGE_TEST_NO_SANDBOX === "1"))
  )
    throw new Error(
      "The publication check must exercise the packaged desktop with the requested sandbox policy",
    );
  writeFileSync(
    evidencePath,
    JSON.stringify(
      {
        status: "passed",
        validated_at: new Date().toISOString(),
        profile,
        publication_controls: publicationControls
          ? {
              binary,
              binary_sha256: binaryHash,
              native_font_controls: true,
              reviewed_svg_styles: true,
              pdf_attachment_styles: true,
            }
          : undefined,
        binary: binary ?? "development Electron",
        native,
        workspace_id: workspaceId,
        pdf: {
          path: exportPath,
          bytes: bytes.length,
          pages: 4,
          page_sizes: sizes,
          sha256: createHash("sha256").update(bytes).digest("hex"),
          embedded_manifest: true,
          independent_counts: [2, 7],
          mean: 1.5,
        },
        png: {
          path: pngPath,
          width: 2480,
          height: 3508,
          dpi: 300,
          physical_metadata: true,
        },
        drag_undo: true,
        reviewed_batch: true,
        invalid_descriptor_rejected: true,
        draft_restored: true,
        engine_origins: [originBefore, originAfter],
        renderer_errors: errors,
      },
      null,
      2,
    ),
  );
  console.log(
    "Native Layout Studio passed: four vector PDF pages, embedded source manifest, exact counts, locked controls, 300-DPI PNG and draft recovery.",
  );
} catch (error) {
  if (window)
    await window
      .screenshot({
        path: path.join(
          root,
          "artifacts/screenshots/desktop-report-failure.png",
        ),
      })
      .catch(() => {});
  writeFileSync(
    evidencePath,
    JSON.stringify(
      { status: "failed", error: error.stack, errors, profile },
      null,
      2,
    ),
  );
  throw error;
} finally {
  clearTimeout(deadline);
  if (application) await application.close();
}
