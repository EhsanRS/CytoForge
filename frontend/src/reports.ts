import type {
  LayoutDefinition,
  ReportElement,
  ReportLayout,
  ReportPage,
  ReportRender,
} from "./types";
import { id } from "./types";
import { saveBlob } from "./api";

export const a4 = (): ReportPage => ({
  width_mm: 210,
  height_mm: 297,
  margin_mm: 12,
  background: "#ffffff",
});
export function reportDefaults(value?: LayoutDefinition): ReportLayout {
  const base: ReportLayout = {
    id: id(),
    name: "Experiment report",
    description: "",
    plots: [],
    pages: [a4()],
    elements: [],
    batch: {
      mode: "off",
      group_id: null,
      sample_ids: [],
      iterator_keyword: "",
      discriminator_keyword: "",
      panel_size: 2,
      tile_columns: 1,
      tile_rows: 1,
      order: "across",
      overrides: {},
      population_overrides: {},
    },
    export_policy: "current",
    show_header: true,
    show_footer: true,
    ...value,
  };
  if (base.plots.length) {
    base.pages = Array.from({ length: Math.ceil(base.plots.length / 4) }, a4);
    base.elements = base.plots.map((plot, i) => ({
      ...elementDefaults("plot"),
      id: plot.id,
      plot,
      title: plot.title,
      page: Math.floor(i / 4),
      x_mm: 12 + (i % 2) * 96,
      y_mm: 38 + Math.floor((i % 4) / 2) * 111,
      width_mm: 90,
      height_mm: 101,
    }));
    base.plots = [];
  }
  base.elements = base.elements.map((element) => ({
    ...elementDefaults(element.kind),
    ...element,
  }));
  return base;
}
export function elementDefaults(kind: ReportElement["kind"]): ReportElement {
  return {
    id: id(),
    kind,
    page: 0,
    x_mm: 12,
    y_mm: 38,
    width_mm: 90,
    height_mm: 85,
    rotation: 0,
    group_id: null,
    position_locked: false,
    iterate: true,
    title: "",
    table_view: "data",
    auto_paginate: false,
    row_start: 0,
    row_count: 12,
    column_ids: [],
    column_start: 0,
    columns_per_page: 4,
    pivot_counts: true,
    comparison_fields: [
      "n_a",
      "n_b",
      "mean_a",
      "mean_b",
      "mean_difference",
      "p_value",
      "adjusted_p_value",
      "confidence_interval",
    ],
    compensated: true,
    follow_replacement: true,
    text: "{{workspace}}",
    font_size_pt: 10,
    font_family: "sans-serif",
    font_weight: 400,
    font_style: "normal",
    text_decoration: "none",
    line_spacing: 1.35,
    align: "left",
    color: "#233449",
    fill: "#ffffff",
    stroke: "#087e8b",
    stroke_width_mm: 0.4,
    opacity: 1,
    shape: "rectangle",
  };
}
export const comparisonFields: {
  id: ReportElement["comparison_fields"][number];
  label: string;
}[] = [
  { id: "n_a", label: "n (A)" },
  { id: "n_b", label: "n (B)" },
  { id: "pairs", label: "Complete pairs" },
  { id: "excluded_a", label: "Excluded (A)" },
  { id: "excluded_b", label: "Excluded (B)" },
  { id: "mean_a", label: "Mean (A)" },
  { id: "mean_b", label: "Mean (B)" },
  { id: "mean_difference", label: "Mean difference" },
  { id: "median_difference", label: "Median difference" },
  { id: "statistic", label: "Test statistic" },
  { id: "p_value", label: "Raw p" },
  { id: "adjusted_p_value", label: "Adjusted p" },
  { id: "confidence_interval", label: "Confidence interval" },
];
export async function sha256(value: string) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
    ),
    (n) => n.toString(16).padStart(2, "0"),
  ).join("");
}
function crc32(bytes: Uint8Array) {
  let value = 0xffffffff;
  for (const byte of bytes) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit++)
      value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}
export function pngResolution(
  bytes: Uint8Array,
  dpi: number,
): Uint8Array<ArrayBuffer> {
  const chunk = new Uint8Array(21),
    view = new DataView(chunk.buffer);
  view.setUint32(0, 9);
  chunk.set(new TextEncoder().encode("pHYs"), 4);
  const metres = Math.round(dpi / 0.0254);
  view.setUint32(8, metres);
  view.setUint32(12, metres);
  chunk[16] = 1;
  view.setUint32(17, crc32(chunk.subarray(4, 17)));
  const pieces = [bytes.subarray(0, 8)];
  let cursor = 8,
    inserted = false;
  while (cursor < bytes.length) {
    const length =
      new DataView(bytes.buffer, bytes.byteOffset + cursor, 4).getUint32(0) +
      12;
    const type = String.fromCharCode(...bytes.subarray(cursor + 4, cursor + 8));
    if (type !== "pHYs") pieces.push(bytes.subarray(cursor, cursor + length));
    if (type === "IHDR") {
      pieces.push(chunk);
      inserted = true;
    }
    cursor += length;
  }
  if (!inserted) throw new Error("Invalid PNG output");
  const output = new Uint8Array(
    pieces.reduce((sum, piece) => sum + piece.length, 0),
  );
  let offset = 0;
  for (const piece of pieces) {
    output.set(piece, offset);
    offset += piece.length;
  }
  return output;
}
export async function exportPng(page: ReportRender, dpi: number) {
  const width = Math.round((page.geometry.width_mm / 25.4) * dpi),
    height = Math.round((page.geometry.height_mm / 25.4) * dpi);
  if (width * height > 64_000_000 || Math.max(width, height) > 16384)
    throw new Error(
      "This page exceeds the PNG pixel limit; use SVG or a lower resolution",
    );
  const href = URL.createObjectURL(
    new Blob([page.svg], { type: "image/svg+xml" }),
  );
  try {
    const image = new Image();
    image.src = href;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("PNG rendering is unavailable");
    context.drawImage(image, 0, 0, width, height);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) =>
          value ? resolve(value) : reject(new Error("PNG rendering failed")),
        "image/png",
      ),
    );
    saveBlob(
      new Blob([pngResolution(new Uint8Array(await blob.arrayBuffer()), dpi)], {
        type: "image/png",
      }),
      `report-page-${page.page + 1}-${dpi}dpi.png`,
    );
    canvas.width = canvas.height = 0;
  } finally {
    URL.revokeObjectURL(href);
  }
}

export async function nativePdf(
  pages: ReportRender[],
  workspace: string,
  revision: number,
  title: string,
  review: unknown,
) {
  if (!window.cytoforgeDesktop)
    throw new Error("PDF export requires the CytoForge desktop application");
  const manifest = JSON.stringify({
    version: 2,
    title,
    workspace_id: workspace,
    revision,
    review,
    pages: pages.map((page) => page.manifest),
  });
  const reportHash = await sha256(manifest);
  const root = document.createElement("section");
  root.id = "cytoforge-report-print";
  root.dataset.reportHash = reportHash;
  const style = document.createElement("style");
  style.textContent =
    `@media print { body > #root {display:none!important} #cytoforge-report-print {display:block!important;margin:0} .native-report-sheet{margin:0;break-after:page;overflow:hidden} .native-report-sheet:last-child{break-after:auto} .native-report-sheet>svg{display:block;width:100%;height:100%} } ` +
    pages
      .map(
        (page, i) =>
          `@page cytoforge${i} {size:${page.geometry.width_mm}mm ${page.geometry.height_mm}mm;margin:0} .native-report-sheet:nth-child(${i + 1}) {page:cytoforge${i};width:${page.geometry.width_mm}mm;height:${page.geometry.height_mm}mm}`,
      )
      .join("\n");
  root.style.display = "none";
  pages.forEach((page) => {
    const sheet = document.createElement("article");
    sheet.className = "native-report-sheet";
    sheet.innerHTML = page.svg;
    root.append(sheet);
  });
  document.head.append(style);
  document.body.append(root);
  try {
    await document.fonts.ready;
    const domHash = await sha256(root.innerHTML);
    return await window.cytoforgeDesktop.exportReportPdf({
      workspace,
      revision,
      title,
      manifest,
      reportHash,
      domHash,
      pageCount: pages.length,
    });
  } finally {
    root.remove();
    style.remove();
  }
}
