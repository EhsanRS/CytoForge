const { parentPort, workerData } = require("node:worker_threads");
const { PDFDocument } = require("pdf-lib");
const {
  writeFileSync,
  renameSync,
  unlinkSync,
  existsSync,
} = require("node:fs");
const crypto = require("node:crypto");

void (async () => {
  const temporary = `${workerData.filename}.${crypto.randomBytes(8).toString("hex")}.tmp`;
  try {
    const pdf = await PDFDocument.load(workerData.bytes);
    if (pdf.getPageCount() !== workerData.pageCount)
      throw new Error("PDF page count differs from the reviewed report");
    pdf.setTitle(workerData.title);
    pdf.setAuthor("CytoForge");
    pdf.setSubject(
      `Workspace ${workerData.workspace}, revision ${workerData.revision}`,
    );
    pdf.setProducer("CytoForge / Chromium / pdf-lib");
    pdf.setKeywords(["cytometry", "analysis report", workerData.reportHash]);
    await pdf.attach(
      Buffer.from(workerData.manifest, "utf8"),
      "cytoforge-report-manifest.json",
      {
        mimeType: "application/json",
        description:
          "Scientific sources, mappings, measurements and page geometry",
      },
    );
    const bytes = await pdf.save();
    writeFileSync(temporary, bytes, { mode: 0o600, flag: "wx", flush: true });
    renameSync(temporary, workerData.filename);
    parentPort.postMessage({
      path: workerData.filename,
      bytes: bytes.length,
      pages: pdf.getPageCount(),
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    });
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
})().catch((error) => {
  parentPort.postMessage({ error: error.message });
  process.exitCode = 1;
});
