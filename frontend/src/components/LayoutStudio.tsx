import { useCallback, useEffect, useRef, useState } from "react";
import {
  Copy,
  Download,
  FilePlus2,
  FileUp,
  Lock,
  Plus,
  Redo2,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { download, post, saveBlob } from "../api";
import { defaultComparisonFigureView } from "../comparisons";
import {
  arrangeReportElements,
  selectedReportUnits,
  type ReportArrangement,
} from "../reportGeometry";
import {
  a4,
  comparisonFields,
  elementDefaults,
  exportPng,
  nativePdf,
  reportDefaults,
} from "../reports";
import type {
  PlotDefinition,
  ReportElement,
  ReportLayout,
  ReportPlan,
  ReportRender,
  Workspace,
  ThreeDView,
} from "../types";
import { id } from "../types";
import {
  backgateAncestryPlacement,
  backgateAncestryPlots,
} from "../reportBackgates";
import type { Commit } from "./Editors";
import { GraphControls } from "./GraphControls";
import { ReportTemplateDialog } from "./ReportTemplateDialog";
import { ReportTableGeometryEditor } from "./ReportTableGeometryEditor";

const clone = <T,>(value: T): T => structuredClone(value);
const numeric = (value: string, fallback: number) =>
  Number.isFinite(Number(value)) && value.trim() ? Number(value) : fallback;
const displayIssue = (issues: ReportRender["issues"]) => [
  ...new Set(issues.map((issue) => `${issue.severity}: ${issue.message}`)),
];

export function LayoutPanel({
  workspace,
  current,
  commit,
  busy,
  printRequest = 0,
}: {
  workspace: Workspace;
  current: Omit<PlotDefinition, "id" | "title"> | null;
  commit: Commit;
  busy: boolean;
  printRequest?: number;
}) {
  const [layout, setLayout] = useState<ReportLayout>(() =>
    reportDefaults(workspace.layouts[0]),
  );
  const [dirty, setDirty] = useState(false),
    [draftReady, setDraftReady] = useState(false),
    [draftNotice, setDraftNotice] = useState("");
  const [page, setPage] = useState(0),
    [batchPreview, setBatchPreview] = useState(false),
    [selected, setSelected] = useState<string[]>([]);
  const [undo, setUndo] = useState<ReportLayout[]>([]),
    [redo, setRedo] = useState<ReportLayout[]>([]);
  const [plan, setPlan] = useState<ReportPlan | null>(null),
    [render, setRender] = useState<ReportRender | null>(null);
  const [rendering, setRendering] = useState(true),
    [exporting, setExporting] = useState(false),
    [error, setError] = useState("");
  const [reviewed, setReviewed] = useState(""),
    [reviewOpen, setReviewOpen] = useState(false),
    [dpi, setDpi] = useState(300),
    [zoom, setZoom] = useState(0.78);
  const [contentChoice, setContentChoice] = useState("text"),
    [fileStatus, setFileStatus] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [ancestryOrientation, setAncestryOrientation] = useState<
    "horizontal" | "vertical"
  >("horizontal");
  const svgHost = useRef<HTMLDivElement>(null),
    sequence = useRef(0),
    lastPrint = useRef(0);
  const draftRef = useRef({
    layout,
    dirty,
    page,
    baseRevision: workspace.revision,
  });
  draftRef.current = { layout, dirty, page, baseRevision: workspace.revision };
  const saveDraft = useCallback(() => {
    const value = draftRef.current;
    if (!window.cytoforgeDesktop) return Promise.resolve(true);
    return window.cytoforgeDesktop.saveReportDraft(
      workspace.id,
      value.dirty ? JSON.stringify(value) : null,
    );
  }, [workspace.id]);
  useEffect(() => {
    let active = true;
    void window.cytoforgeDesktop
      ?.getReportDraft(workspace.id)
      .then((value) => {
        if (!active || !value) return;
        const draft = JSON.parse(value);
        if (!draft.layout || !Array.isArray(draft.layout.elements))
          throw new Error("The report draft is invalid");
        setLayout(reportDefaults(draft.layout));
        setDirty(true);
        setPage(
          Math.max(0, Math.min(draft.page || 0, draft.layout.pages.length - 1)),
        );
        setDraftNotice(
          draft.baseRevision === workspace.revision
            ? "Unsaved desktop draft restored."
            : "Desktop draft restored. The workspace changed; review its current sources before saving or exporting.",
        );
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setDraftReady(true);
      });
    if (!window.cytoforgeDesktop) setDraftReady(true);
    return () => {
      active = false;
    };
    // A workspace revision refresh must preserve the open draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);
  useEffect(() => {
    if (!draftReady) return;
    const timer = setTimeout(
      () =>
        void saveDraft()
          .then((saved) => {
            if (!saved) setError("The desktop report draft could not be saved");
          })
          .catch((e) => setError(e.message)),
      300,
    );
    return () => clearTimeout(timer);
  }, [layout, dirty, page, draftReady, saveDraft]);
  useEffect(() => {
    if (!draftReady) return;
    const flush = () => void saveDraft();
    window.addEventListener("cytoforge:flush-report-draft", flush);
    return () => {
      window.removeEventListener("cytoforge:flush-report-draft", flush);
      void saveDraft();
    };
  }, [draftReady, saveDraft]);
  function update(next: ReportLayout, record = true) {
    if (record) {
      setUndo((values) => [...values.slice(-49), clone(layout)]);
      setRedo([]);
    }
    setLayout(next);
    setDirty(true);
    setError("");
    setFileStatus("");
  }
  function history(back: boolean) {
    const values = back ? undo : redo;
    if (!values.length) return;
    const next = values[values.length - 1];
    if (back) {
      setUndo(values.slice(0, -1));
      setRedo((values) => [...values, clone(layout)]);
    } else {
      setRedo(values.slice(0, -1));
      setUndo((values) => [...values, clone(layout)]);
    }
    setLayout(next);
    setDirty(true);
    setSelected([]);
    setPage((value) => Math.min(value, next.pages.length - 1));
  }
  const patchElement = (identifier: string, patch: Partial<ReportElement>) =>
    update({
      ...layout,
      elements: layout.elements.map((element) =>
        element.id === identifier ? { ...element, ...patch } : element,
      ),
    });
  const patchPlot = (patch: Partial<PlotDefinition>) => {
    if (active?.plot)
      patchElement(active.id, { plot: { ...active.plot, ...patch } });
  };
  const active = layout.elements.find((element) => element.id === selected[0]);
  const prototypePage = batchPreview
      ? (plan?.output_pages?.[page]?.prototype_page ?? 0)
      : Math.min(page, layout.pages.length - 1),
    geometry = layout.pages[prototypePage];
  const movableUnitCount = selectedReportUnits(
    layout.elements,
    selected,
    prototypePage,
  ).filter((unit) => !unit.locked).length;
  useEffect(() => {
    if (!draftReady) return;
    const currentSequence = ++sequence.current;
    setRendering(true);
    setError("");
    const timer = setTimeout(() => {
      const body = { revision: workspace.revision, definition: layout };
      void post<ReportPlan>(`/workspaces/${workspace.id}/reports/plan`, body)
        .then(async (fullPlan) => {
          const design = clone(layout);
          design.batch = {
            ...design.batch,
            mode: "off",
            tile_rows: 1,
            tile_columns: 1,
          };
          const index = batchPreview
            ? Math.min(page, Math.max(0, fullPlan.page_count - 1))
            : prototypePage;
          const result = await post<ReportRender>(
            `/workspaces/${workspace.id}/reports/render`,
            {
              revision: workspace.revision,
              definition: batchPreview ? layout : design,
              page: index,
              prototype_page: batchPreview ? undefined : prototypePage,
              review_hash: batchPreview ? fullPlan.review_hash : undefined,
            },
          );
          if (sequence.current === currentSequence) {
            setPlan(fullPlan);
            setRender(result);
            setRendering(false);
          }
        })
        .catch((e) => {
          if (sequence.current === currentSequence) {
            setError(e.message);
            setRendering(false);
            setRender(null);
          }
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      sequence.current++;
    };
  }, [
    layout,
    workspace.id,
    workspace.revision,
    page,
    prototypePage,
    batchPreview,
    draftReady,
  ]);

  function addAncestry() {
    if (!current) return;
    try {
      const plots = backgateAncestryPlots(workspace, current);
      const placement = backgateAncestryPlacement(
        layout,
        plots.length,
        geometry,
        ancestryOrientation,
      );
      const elements = plots.map((plot, index) => ({
        ...elementDefaults("plot"),
        ...placement.placements[index],
        plot,
        title: `{{sample}} · {{population}} · Stage ${index + 1}`,
      }));
      update({
        ...layout,
        pages: placement.pages,
        elements: [...layout.elements, ...elements],
      });
      setSelected(
        elements
          .filter((element) => element.page === elements[0].page)
          .map((element) => element.id),
      );
      setPage(elements[0].page);
      setBatchPreview(false);
      setError("");
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    }
  }

  function add(kind: ReportElement["kind"], reference?: string) {
    const element = elementDefaults(kind);
    element.page = prototypePage;
    const count = layout.elements.filter(
      (item) => item.page === prototypePage,
    ).length;
    element.x_mm = Math.min(
      geometry.width_mm - element.width_mm,
      12 + (count % 2) * 96,
    );
    element.y_mm = Math.min(
      geometry.height_mm - element.height_mm,
      38 + Math.floor((count % 4) / 2) * 100,
    );
    if (kind === "plot") {
      if (!current) return;
      element.plot = {
        ...current,
        id: id(),
        title: "",
        overlays: [],
        color: "#087e8b",
        bins: current.bins ?? 96,
        normalization: "count",
        show_gates: true,
      };
      element.title = "{{sample}} · {{population}}";
    } else if (kind === "table") {
      element.table_id = reference;
      element.title =
        workspace.tables.find((table) => table.id === reference)?.name ?? "";
      element.width_mm = Math.min(180, geometry.width_mm - 24);
      element.height_mm = 70;
      element.auto_paginate = true;
      element.iterate = false;
    } else if (kind === "plate") {
      element.plate_id = reference;
      element.iterate = false;
      element.title =
        workspace.plates.find((plate) => plate.id === reference)?.name ?? "";
    } else if (kind === "biology") {
      const [platform, result, sample] = reference!.split(":");
      element.platform = platform as ReportElement["platform"];
      element.result_id = result;
      element.sample_id = sample;
      element.title = "{{sample}} · " + platform;
    } else if (kind === "population_comparison") {
      const [identifier, target] = reference!.split(":");
      const result = workspace.comparison_results?.find(
        (r) => r.id === identifier,
      );
      const source = result?.request.inputs[Number(target)];
      if (!result || !source) return;
      element.result_id = result.id;
      element.sample_id = source.sample_id;
      element.gate_id = source.gate_id;
      element.comparison_parameter_id = result.request.parameters[0].id;
      element.comparison_view = defaultComparisonFigureView();
      element.title = "{{sample}} · {{population}} · " + result.request.name;
    } else if (kind === "text") {
      element.width_mm = Math.min(180, geometry.width_mm - 24);
      element.height_mm = 25;
      element.sample_id =
        current?.sample_id ?? workspace.samples[0]?.id ?? null;
      element.text = "{{sample}} · {{stat:count}} events";
    } else {
      element.width_mm = 50;
      element.height_mm = 20;
    }
    element.width_mm = Math.min(element.width_mm, geometry.width_mm);
    element.height_mm = Math.min(element.height_mm, geometry.height_mm);
    element.x_mm = Math.max(
      0,
      Math.min(element.x_mm, geometry.width_mm - element.width_mm),
    );
    element.y_mm = Math.max(
      0,
      Math.min(element.y_mm, geometry.height_mm - element.height_mm),
    );
    update({ ...layout, elements: [...layout.elements, element] });
    setSelected([element.id]);
    setBatchPreview(false);
  }
  function duplicate() {
    const additions = layout.elements
      .filter((element) => selected.includes(element.id))
      .map((element) => {
        const value = clone(element),
          size = layout.pages[value.page];
        value.id = id();
        value.group_id = null;
        value.x_mm = Math.min(size.width_mm - value.width_mm, value.x_mm + 3);
        value.y_mm = Math.min(size.height_mm - value.height_mm, value.y_mm + 3);
        return value;
      });
    update({ ...layout, elements: [...layout.elements, ...additions] });
    setSelected(additions.map((element) => element.id));
  }
  function remove() {
    update({
      ...layout,
      elements: layout.elements.filter(
        (element) => !selected.includes(element.id),
      ),
    });
    setSelected([]);
  }
  function arrange(action: ReportArrangement) {
    try {
      const elements = arrangeReportElements(
        layout.elements,
        selected,
        prototypePage,
        action,
        action === "group" ? id() : undefined,
      );
      if (elements !== layout.elements) update({ ...layout, elements });
    } catch (reason) {
      setError(String(reason));
    }
  }
  function pointerStart(
    event: React.PointerEvent,
    element: ReportElement,
    resize = false,
  ) {
    if (batchPreview || exporting || rendering) return;
    event.preventDefault();
    event.stopPropagation();
    const clicked = selectedReportUnits(
      layout.elements,
      [element.id],
      prototypePage,
    ).flatMap((unit) => unit.members.map((value) => value.id));
    const selection = event.shiftKey
      ? selected.includes(element.id)
        ? selected.filter((value) => !clicked.includes(value))
        : [...new Set([...selected, ...clicked])]
      : selected.includes(element.id)
        ? selected
        : clicked;
    setSelected(selection);
    if (element.position_locked) return;
    const moving = selectedReportUnits(
      layout.elements,
      selection,
      prototypePage,
    )
      .filter((unit) => !unit.locked)
      .flatMap((unit) => unit.members);
    if (!moving.some((value) => value.id === element.id)) return;
    const initial = clone(layout),
      startX = event.clientX,
      startY = event.clientY;
    const rect = svgHost.current!.getBoundingClientRect(),
      factor = geometry.width_mm / rect.width;
    let next = initial,
      changed = false;
    const target = event.currentTarget as HTMLElement;
    target.setPointerCapture(event.pointerId);
    const move = (input: PointerEvent) => {
      let dx = (input.clientX - startX) * factor,
        dy = (input.clientY - startY) * factor;
      if (!input.altKey) {
        dx = Math.round(dx * 2) / 2;
        dy = Math.round(dy * 2) / 2;
      }
      if (!resize) {
        dx = Math.max(
          -Math.min(...moving.map((value) => value.x_mm)),
          Math.min(
            dx,
            Math.min(
              ...moving.map(
                (value) => geometry.width_mm - value.x_mm - value.width_mm,
              ),
            ),
          ),
        );
        dy = Math.max(
          -Math.min(...moving.map((value) => value.y_mm)),
          Math.min(
            dy,
            Math.min(
              ...moving.map(
                (value) => geometry.height_mm - value.y_mm - value.height_mm,
              ),
            ),
          ),
        );
      }
      next = {
        ...initial,
        elements: initial.elements.map((value) =>
          !moving.some((item) => item.id === value.id)
            ? value
            : resize && value.id === element.id
              ? {
                  ...value,
                  width_mm: Math.max(
                    5,
                    Math.min(
                      value.width_mm + dx,
                      geometry.width_mm - value.x_mm,
                    ),
                  ),
                  height_mm: Math.max(
                    5,
                    Math.min(
                      value.height_mm + dy,
                      geometry.height_mm - value.y_mm,
                    ),
                  ),
                }
              : { ...value, x_mm: value.x_mm + dx, y_mm: value.y_mm + dy },
        ),
      };
      changed = true;
      for (const value of next.elements.filter((value) =>
        moving.some((item) => item.id === value.id),
      )) {
        const group = svgHost.current?.querySelector(
          `[data-element-id="${value.id}"]`,
        );
        group?.setAttribute(
          "transform",
          `translate(${value.x_mm} ${value.y_mm}) rotate(${value.rotation} ${value.width_mm / 2} ${value.height_mm / 2})`,
        );
      }
    };
    const end = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", end);
      target.removeEventListener("pointercancel", end);
      if (changed) update(next);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  }
  async function preparePages() {
    const revision = workspace.revision,
      definition = clone(layout);
    const review = await post<ReportPlan>(
      `/workspaces/${workspace.id}/reports/plan`,
      { revision, definition },
    );
    if (definition.batch.mode !== "off" && reviewed !== review.review_hash)
      throw new Error(
        "Review and accept the current batch mappings before exporting",
      );
    if (review.page_count > 1024)
      throw new Error("Export at most 1024 pages at a time");
    const pages: ReportRender[] = [];
    let bytes = 0;
    for (let index = 0; index < review.page_count; index++) {
      setFileStatus(`Preparing page ${index + 1} of ${review.page_count}…`);
      const result = await post<ReportRender>(
        `/workspaces/${workspace.id}/reports/render`,
        {
          revision,
          definition,
          page: index,
          review_hash: review.review_hash,
          validate_sources: true,
        },
      );
      if (!result.exportable)
        throw new Error(
          "Report has unresolved source issues. Review the sources or select an explicit export policy.",
        );
      bytes += result.svg.length;
      if (bytes > 128 * 1024 * 1024)
        throw new Error("Report exceeds 128 MiB; use a smaller batch");
      pages.push(result);
    }
    return { pages, review, revision };
  }
  async function exportPdf() {
    if (exporting) return;
    setExporting(true);
    setError("");
    try {
      const prepared = await preparePages();
      const result = await nativePdf(
        prepared.pages,
        workspace.id,
        prepared.revision,
        layout.name,
        prepared.review,
      );
      setFileStatus(
        result.canceled
          ? "PDF export canceled."
          : `PDF saved · ${result.pages} pages · ${result.path}`,
      );
    } catch (e) {
      setError((e as Error).message);
      setFileStatus("");
    } finally {
      setExporting(false);
    }
  }
  const printRef = useRef(exportPdf);
  printRef.current = exportPdf;
  useEffect(() => {
    if (printRequest > lastPrint.current && draftReady && !rendering) {
      lastPrint.current = printRequest;
      void printRef.current();
    }
  }, [printRequest, draftReady, rendering]);
  const canExport =
    !!render &&
    !rendering &&
    !exporting &&
    layout.elements.length > 0 &&
    (layout.batch.mode === "off" || reviewed === plan?.review_hash);
  const canEdit = !batchPreview && !exporting;
  async function saveLayout() {
    const saved = JSON.stringify(layout);
    try {
      await commit(
        "/layouts/save",
        { definition: layout },
        "Report layout saved",
      );
      if (JSON.stringify(draftRef.current.layout) === saved) {
        setDirty(false);
        setDraftNotice("");
      }
    } catch (error) {
      setError((error as Error).message);
    }
  }
  async function exportTemplate() {
    setExporting(true);
    setError("");
    try {
      const filename =
        layout.name.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 100) || "report";
      await download(
        `/workspaces/${workspace.id}/report-templates/export`,
        `${filename}-template.cytoforge-report.json`,
        {
          method: "POST",
          body: JSON.stringify({
            revision: workspace.revision,
            definition: layout,
          }),
        },
      );
      setFileStatus(
        "Report template exported with page design and source bindings.",
      );
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setExporting(false);
    }
  }
  async function exportArtifact(format: "svg" | "zip" | "png" | "manifest") {
    if (!render || !plan) return;
    setExporting(true);
    setError("");
    try {
      if (format === "png") {
        const result = await post<ReportRender>(
          `/workspaces/${workspace.id}/reports/render`,
          {
            revision: workspace.revision,
            definition: layout,
            page: batchPreview ? render.page : prototypePage,
            prototype_page: batchPreview ? undefined : prototypePage,
            review_hash: plan.review_hash,
            validate_sources: true,
          },
        );
        if (!result.exportable)
          throw new Error(
            "Resolve this page's source issues or select an export policy",
          );
        await exportPng(result, dpi);
      } else if (format === "manifest") {
        const prepared = await preparePages();
        saveBlob(
          new Blob(
            [
              JSON.stringify(
                {
                  review: prepared.review,
                  pages: prepared.pages.map((page) => page.manifest),
                },
                null,
                2,
              ),
            ],
            { type: "application/json" },
          ),
          "report-manifest.json",
        );
      } else
        await download(
          `/workspaces/${workspace.id}/reports/export`,
          format === "zip"
            ? "report-pages.zip"
            : `report-page-${render.page + 1}.svg`,
          {
            method: "POST",
            body: JSON.stringify({
              revision: workspace.revision,
              definition: layout,
              page: batchPreview ? render.page : prototypePage,
              prototype_page: batchPreview ? undefined : prototypePage,
              format,
              review_hash: plan.review_hash,
            }),
          },
        );
      setFileStatus("Report exported.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }
  const modelChoices = [
    ...workspace.cell_cycle_results.map((result) => ({
      platform: "cell-cycle",
      result,
    })),
    ...workspace.proliferation_results.map((result) => ({
      platform: "proliferation",
      result,
    })),
    ...workspace.kinetics_results.map((result) => ({
      platform: "kinetics",
      result,
    })),
  ];
  const units = (
    name: string,
    key: "x_mm" | "y_mm" | "width_mm" | "height_mm" | "rotation",
    step = 0.5,
  ) => (
    <label>
      {name}
      <input
        aria-label={name}
        type="number"
        step={step}
        value={active?.[key] ?? 0}
        onChange={(e) =>
          active &&
          patchElement(active.id, {
            [key]: numeric(e.target.value, active[key]),
          })
        }
      />
    </label>
  );
  return (
    <div className="page-panel layout-studio">
      <div className="page-heading">
        <div>
          <span className="eyebrow">PUBLICATION & REPORTING</span>
          <h1>Layout studio</h1>
          <p>Live figures, reusable pages and reviewed batch reports.</p>
        </div>
        <div className="button-group">
          <button
            className="button"
            disabled={!current || !canEdit}
            onClick={() => add("plot")}
          >
            <Plus size={16} />
            Add current plot
          </button>
          <select
            aria-label="Backgate ancestry orientation"
            value={ancestryOrientation}
            onChange={(event) =>
              setAncestryOrientation(
                event.target.value as "horizontal" | "vertical",
              )
            }
          >
            <option value="horizontal">Ancestry across</option>
            <option value="vertical">Ancestry down</option>
          </select>
          <button
            className="button"
            disabled={
              !current || !(current.backgate_id || current.gate_id) || !canEdit
            }
            onClick={addAncestry}
          >
            <Plus size={16} />
            Add backgate ancestry
          </button>
          <button
            className="button"
            disabled={busy || exporting || !layout.name.trim()}
            onClick={() => void saveLayout()}
          >
            <Save size={16} />
            Save layout{dirty ? " •" : ""}
          </button>
          <button
            className="button"
            disabled={busy || exporting}
            onClick={() => setTemplateOpen(true)}
          >
            <FileUp size={16} /> Load template
          </button>
          <button
            className="button"
            disabled={busy || exporting || !layout.name.trim()}
            onClick={() => void exportTemplate()}
          >
            <Download size={16} /> Export template
          </button>
          <button
            className="button primary"
            disabled={!canExport || !window.cytoforgeDesktop}
            onClick={() => void exportPdf()}
          >
            <Download size={16} />
            Export PDF
          </button>
        </div>
      </div>
      {templateOpen && (
        <ReportTemplateDialog
          workspace={workspace}
          keepDraft={dirty}
          onClose={() => setTemplateOpen(false)}
          onApply={async (request) => {
            const changed = await commit(
              "/report-templates/apply",
              request,
              "Report template imported",
            );
            const imported = changed.layouts.find(
              (value) => value.id === request.id,
            );
            if (!imported)
              throw new Error("The imported report is unavailable");
            if (draftRef.current.dirty) {
              setFileStatus(
                `Imported ${imported.name}. Your unsaved report stays open.`,
              );
            } else {
              update(reportDefaults(imported));
              setDirty(false);
              setDraftNotice("");
              setFileStatus(`Imported ${imported.name}.`);
              setPage(0);
              setSelected([]);
              setBatchPreview(false);
            }
          }}
        />
      )}
      {(error || draftNotice || fileStatus) && (
        <div
          className={`report-message ${error ? "form-error" : ""}`}
          role={error ? "alert" : "status"}
        >
          {error || fileStatus || draftNotice}
        </div>
      )}
      <div className="studio-toolbar">
        <select
          aria-label="Saved report"
          value={
            workspace.layouts.some((value) => value.id === layout.id)
              ? layout.id
              : ""
          }
          onChange={(e) => {
            const value = workspace.layouts.find(
              (value) => value.id === e.target.value,
            );
            if (value) {
              update(reportDefaults(value));
              setDirty(false);
              setPage(0);
              setSelected([]);
            }
          }}
        >
          <option value="">Unsaved report</option>
          {workspace.layouts.map((value) => (
            <option key={value.id} value={value.id}>
              {value.name}
            </option>
          ))}
        </select>
        <button
          className="text-button"
          onClick={() => {
            update(reportDefaults());
            setPage(0);
            setSelected([]);
          }}
        >
          <FilePlus2 size={15} />
          New report
        </button>
        <button
          className="icon-button"
          aria-label="Undo layout edit"
          disabled={!undo.length || !canEdit}
          onClick={() => history(true)}
        >
          <Undo2 size={16} />
        </button>
        <button
          className="icon-button"
          aria-label="Redo layout edit"
          disabled={!redo.length || !canEdit}
          onClick={() => history(false)}
        >
          <Redo2 size={16} />
        </button>
        <button
          className={`button small ${!batchPreview ? "active" : ""}`}
          onClick={() => {
            setBatchPreview(false);
            setPage(0);
          }}
        >
          Design
        </button>
        <button
          className={`button small ${batchPreview ? "active" : ""}`}
          onClick={() => {
            setBatchPreview(true);
            setPage(0);
          }}
        >
          Output preview
        </button>
        <select
          aria-label="Report page"
          value={page}
          onChange={(e) => {
            setPage(Number(e.target.value));
            setSelected([]);
          }}
        >
          {Array.from(
            {
              length: batchPreview
                ? plan?.page_count || 1
                : layout.pages.length,
            },
            (_, index) => (
              <option key={index} value={index}>
                Page {index + 1}
                {batchPreview && plan?.output_pages[index]?.continuation
                  ? ` · continuation ${plan.output_pages[index].continuation + 1}`
                  : ""}
              </option>
            ),
          )}
        </select>
        <button
          className="text-button"
          disabled={!canEdit || layout.pages.length >= 32}
          onClick={() => {
            update({ ...layout, pages: [...layout.pages, a4()] });
            setPage(layout.pages.length);
          }}
        >
          Add page
        </button>
        <label className="studio-zoom">
          Zoom
          <select
            aria-label="Report zoom"
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          >
            {[0.5, 0.65, 0.78, 1, 1.25].map((value) => (
              <option key={value} value={value}>
                {Math.round(value * 100)}%
              </option>
            ))}
          </select>
        </label>
        <span className="studio-spacer" />
        <button
          className="button small"
          disabled={!canExport}
          onClick={() => void exportArtifact("svg")}
        >
          SVG page
        </button>
        <button
          className="button small"
          disabled={!canExport}
          onClick={() => void exportArtifact("zip")}
        >
          SVG batch
        </button>
        <select
          aria-label="PNG resolution"
          value={dpi}
          onChange={(e) => setDpi(Number(e.target.value))}
        >
          {[150, 300, 600].map((value) => (
            <option key={value} value={value}>
              {value} DPI
            </option>
          ))}
        </select>
        <button
          className="button small"
          disabled={!canExport}
          onClick={() => void exportArtifact("png")}
        >
          PNG page
        </button>
      </div>
      <div className="studio-body">
        <aside className="studio-inspector">
          <label>
            Report title
            <input
              aria-label="Report title"
              value={layout.name}
              maxLength={160}
              onChange={(e) => update({ ...layout, name: e.target.value })}
            />
          </label>
          <label>
            Description
            <textarea
              aria-label="Report description"
              rows={2}
              value={layout.description}
              onChange={(e) =>
                update({ ...layout, description: e.target.value })
              }
            />
          </label>
          <div className="studio-section">
            <strong>Page setup</strong>
            <select
              aria-label="Page size"
              value=""
              onChange={(e) => {
                const [width_mm, height_mm] = e.target.value
                  .split(",")
                  .map(Number);
                const next = clone(layout);
                next.pages[prototypePage] = {
                  ...geometry,
                  width_mm,
                  height_mm,
                };
                next.elements = next.elements.map((element) =>
                  element.page !== prototypePage
                    ? element
                    : {
                        ...element,
                        width_mm: Math.min(element.width_mm, width_mm),
                        height_mm: Math.min(element.height_mm, height_mm),
                        x_mm: Math.min(
                          element.x_mm,
                          Math.max(0, width_mm - element.width_mm),
                        ),
                        y_mm: Math.min(
                          element.y_mm,
                          Math.max(0, height_mm - element.height_mm),
                        ),
                      },
                );
                update(next);
              }}
            >
              <option value="">
                {geometry.width_mm} × {geometry.height_mm} mm
              </option>
              <option value="210,297">A4 portrait</option>
              <option value="297,210">A4 landscape</option>
              <option value="215.9,279.4">Letter portrait</option>
              <option value="279.4,215.9">Letter landscape</option>
            </select>
            <div className="studio-fields">
              {(["width_mm", "height_mm", "margin_mm"] as const).map((key) => (
                <label key={key}>
                  {key.replace("_mm", "")} (mm)
                  <input
                    type="number"
                    aria-label={`Page ${key}`}
                    value={geometry[key]}
                    min={key === "margin_mm" ? 0 : 30}
                    max={key === "margin_mm" ? 100 : 1200}
                    onChange={(e) => {
                      const next = clone(layout);
                      next.pages[prototypePage] = {
                        ...geometry,
                        [key]: numeric(e.target.value, geometry[key]),
                      };
                      update(next);
                    }}
                  />
                </label>
              ))}
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={layout.show_header}
                onChange={(e) =>
                  update({ ...layout, show_header: e.target.checked })
                }
              />
              Page header
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={layout.show_footer}
                onChange={(e) =>
                  update({ ...layout, show_footer: e.target.checked })
                }
              />
              Source hash footer
            </label>
            <button
              className="text-button danger"
              disabled={layout.pages.length === 1 || !canEdit}
              onClick={() => {
                update({
                  ...layout,
                  pages: layout.pages.filter((_, i) => i !== prototypePage),
                  elements: layout.elements
                    .filter((element) => element.page !== prototypePage)
                    .map((element) => ({
                      ...element,
                      page:
                        element.page > prototypePage
                          ? element.page - 1
                          : element.page,
                    })),
                });
                setPage(Math.max(0, prototypePage - 1));
                setSelected([]);
              }}
            >
              Remove page
            </button>
          </div>
          <div className="studio-section">
            <strong>Add content</strong>
            <select
              aria-label="Report content"
              value={contentChoice}
              onChange={(e) => setContentChoice(e.target.value)}
            >
              <option value="text">Live text / statistics</option>
              <option value="shape">Shape / arrow</option>
              {workspace.tables.map((value) => (
                <option key={value.id} value={`table:${value.id}`}>
                  Table · {value.name}
                </option>
              ))}
              {workspace.plates.map((value) => (
                <option key={value.id} value={`plate:${value.id}`}>
                  Plate · {value.name}
                </option>
              ))}
              {workspace.comparison_results?.flatMap((result) =>
                result.request.inputs.map((source, i) => {
                  const row = result.rows.find(
                    (r) =>
                      r.role === "target" &&
                      r.source.sample_id === source.sample_id &&
                      r.source.gate_id === source.gate_id,
                  );
                  return (
                    <option
                      key={`comparison:${result.id}:${i}`}
                      value={`population_comparison:${result.id}:${i}`}
                    >
                      Comparison · {result.request.name} · {row?.source_name} ·{" "}
                      {row?.population_name}
                    </option>
                  );
                }),
              )}
              {modelChoices.flatMap(({ platform, result }) =>
                result.fits.map((fit) => (
                  <option
                    key={`${result.id}:${fit.sample_id}`}
                    value={`biology:${platform}:${result.id}:${fit.sample_id}`}
                  >
                    {platform} · {result.request.name} ·{" "}
                    {workspace.samples.find(
                      (sample) => sample.id === fit.sample_id,
                    )?.name ?? "Removed acquisition"}
                  </option>
                )),
              )}
            </select>
            <button
              className="button small"
              disabled={!canEdit}
              onClick={() => {
                const [kind, ...ref] = contentChoice.split(":");
                add(kind as ReportElement["kind"], ref.join(":"));
              }}
            >
              <Plus size={14} />
              Add object
            </button>
          </div>
          <div className="studio-section">
            <strong>
              Objects ·{" "}
              {
                layout.elements.filter(
                  (element) => element.page === prototypePage,
                ).length
              }
            </strong>
            <div className="studio-object-list">
              {layout.elements
                .filter((element) => element.page === prototypePage)
                .map((element, index) => (
                  <button
                    key={element.id}
                    className={selected.includes(element.id) ? "selected" : ""}
                    onClick={(event) =>
                      setSelected(
                        event.shiftKey
                          ? [...new Set([...selected, element.id])]
                          : [element.id],
                      )
                    }
                  >
                    {index + 1}. {element.title || element.kind}
                    {element.position_locked && <Lock size={12} />}
                  </button>
                ))}
            </div>
            <div className="button-group">
              <button
                aria-label="Duplicate objects"
                className="icon-button"
                disabled={!selected.length || !canEdit}
                onClick={duplicate}
              >
                <Copy size={15} />
              </button>
              <button
                aria-label="Delete objects"
                className="icon-button"
                disabled={!selected.length || !canEdit}
                onClick={remove}
              >
                <Trash2 size={15} />
              </button>
              <button
                className="text-button"
                disabled={selected.length < 2 || !canEdit}
                onClick={() => arrange("group")}
              >
                Group
              </button>
              <button
                className="text-button"
                disabled={!selected.length || !canEdit}
                onClick={() => arrange("ungroup")}
              >
                Ungroup
              </button>
            </div>
            <div className="button-group">
              {(
                [
                  ["left", "Left"],
                  ["center", "Center"],
                  ["right", "Right"],
                  ["top", "Top"],
                  ["middle", "Middle"],
                  ["bottom", "Bottom"],
                  ["distribute-horizontal", "Horizontal centers"],
                  ["distribute-vertical", "Vertical centers"],
                  ["space-horizontal", "Horizontal gaps"],
                  ["space-vertical", "Vertical gaps"],
                  ["front", "Front"],
                  ["back", "Back"],
                ] as const
              ).map(([action, label]) => (
                <button
                  key={action}
                  className="text-button"
                  aria-label={`Arrange objects: ${label.toLowerCase()}`}
                  disabled={
                    !canEdit ||
                    (action === "front" || action === "back"
                      ? !selected.length
                      : movableUnitCount <
                        (action.startsWith("distribute-") ||
                        action.startsWith("space-")
                          ? 3
                          : 2))
                  }
                  onClick={() => arrange(action)}
                >
                  {label}
                </button>
              ))}
            </div>
            <small>
              Align rotated bounds and space whole groups. Locked groups stay in
              place. Distribution requires three movable objects or groups.
            </small>
          </div>
          {active && (
            <fieldset className="studio-section" disabled={!canEdit}>
              <legend>Selected {active.kind}</legend>
              <label>
                Caption
                <input
                  aria-label="Object caption"
                  value={active.title}
                  onChange={(e) =>
                    patchElement(active.id, { title: e.target.value })
                  }
                />
              </label>
              <div className="studio-fields">
                {units("X (mm)", "x_mm")}
                {units("Y (mm)", "y_mm")}
                {units("Width (mm)", "width_mm")}
                {units("Height (mm)", "height_mm")}
                {units("Rotation (°)", "rotation", 1)}
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={active.position_locked}
                  onChange={(e) =>
                    patchElement(active.id, {
                      position_locked: e.target.checked,
                    })
                  }
                />
                Lock position
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={active.iterate}
                  onChange={(e) =>
                    patchElement(active.id, { iterate: e.target.checked })
                  }
                />
                Iterate acquisition
              </label>
              {(active.kind === "text" || active.kind === "biology") && (
                <label>
                  Acquisition context
                  <select
                    aria-label="Object acquisition"
                    value={active.sample_id ?? ""}
                    onChange={(e) =>
                      patchElement(active.id, {
                        sample_id: e.target.value || null,
                        gate_id: null,
                      })
                    }
                  >
                    <option value="">Default context</option>
                    {workspace.samples.map((sample) => (
                      <option key={sample.id} value={sample.id}>
                        {sample.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {active.kind === "text" && (
                <>
                  <label>
                    Text
                    <textarea
                      aria-label="Annotation text"
                      rows={4}
                      value={active.text}
                      onChange={(e) =>
                        patchElement(active.id, { text: e.target.value })
                      }
                    />
                  </label>
                  <small>
                    Tokens:{" "}
                    {
                      "{{sample}}, {{workspace}}, {{iteration}}, {{keyword:Donor}}, {{stat:count}}, {{stat:median:X}}"
                    }
                  </small>
                  <label>
                    Population
                    <select
                      aria-label="Annotation population"
                      value={active.gate_id ?? ""}
                      onChange={(e) =>
                        patchElement(active.id, {
                          gate_id: e.target.value || null,
                        })
                      }
                    >
                      <option value="">All events</option>
                      {workspace.gates
                        .filter((gate) => gate.sample_id === active.sample_id)
                        .map((gate) => (
                          <option key={gate.id} value={gate.id}>
                            {gate.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Alignment
                    <select
                      value={active.align}
                      onChange={(e) =>
                        patchElement(active.id, {
                          align: e.target.value as ReportElement["align"],
                        })
                      }
                    >
                      {["left", "center", "right"].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              {active.kind === "shape" && (
                <>
                  <label>
                    Shape
                    <select
                      aria-label="Report shape"
                      value={active.shape}
                      onChange={(e) =>
                        patchElement(active.id, {
                          shape: e.target.value as ReportElement["shape"],
                        })
                      }
                    >
                      {["rectangle", "ellipse", "line", "arrow"].map(
                        (value) => (
                          <option key={value}>{value}</option>
                        ),
                      )}
                    </select>
                  </label>
                  <label>
                    Fill
                    <input
                      type="color"
                      value={active.fill}
                      onChange={(e) =>
                        patchElement(active.id, { fill: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Stroke
                    <input
                      type="color"
                      value={active.stroke}
                      onChange={(e) =>
                        patchElement(active.id, { stroke: e.target.value })
                      }
                    />
                  </label>
                </>
              )}
              <div className="studio-fields">
                {active.kind !== "shape" && (
                  <>
                    <label>
                      Font family
                      <select
                        aria-label="Object font family"
                        value={active.font_family}
                        onChange={(e) =>
                          patchElement(active.id, {
                            font_family: e.target
                              .value as ReportElement["font_family"],
                          })
                        }
                      >
                        <option value="sans-serif">Sans serif</option>
                        <option value="serif">Serif</option>
                        <option value="monospace">Monospace</option>
                      </select>
                    </label>
                    <label>
                      Font weight
                      <select
                        aria-label="Object font weight"
                        value={active.font_weight}
                        onChange={(e) =>
                          patchElement(active.id, {
                            font_weight: Number(
                              e.target.value,
                            ) as ReportElement["font_weight"],
                          })
                        }
                      >
                        <option value={400}>Regular</option>
                        <option value={600}>Semibold</option>
                        <option value={700}>Bold</option>
                        <option value={800}>Extra bold</option>
                      </select>
                    </label>
                    <label>
                      Font style
                      <select
                        aria-label="Object font style"
                        value={active.font_style ?? "normal"}
                        onChange={(e) =>
                          patchElement(active.id, {
                            font_style: e.target
                              .value as ReportElement["font_style"],
                          })
                        }
                      >
                        <option value="normal">Normal</option>
                        <option value="italic">Italic</option>
                      </select>
                    </label>
                    <label>
                      Decoration
                      <select
                        aria-label="Object text decoration"
                        value={active.text_decoration ?? "none"}
                        onChange={(e) =>
                          patchElement(active.id, {
                            text_decoration: e.target
                              .value as ReportElement["text_decoration"],
                          })
                        }
                      >
                        <option value="none">None</option>
                        <option value="underline">Underline</option>
                      </select>
                    </label>
                    <label>
                      Line spacing
                      <input
                        aria-label="Object line spacing"
                        type="number"
                        min={1}
                        max={3}
                        step={0.05}
                        value={active.line_spacing ?? 1.35}
                        onChange={(e) =>
                          patchElement(active.id, {
                            line_spacing: numeric(
                              e.target.value,
                              active.line_spacing ?? 1.35,
                            ),
                          })
                        }
                      />
                    </label>
                  </>
                )}
                <label>
                  Font (pt)
                  <input
                    aria-label="Object font size"
                    type="number"
                    min={4}
                    max={144}
                    value={active.font_size_pt}
                    onChange={(e) =>
                      patchElement(active.id, {
                        font_size_pt: numeric(
                          e.target.value,
                          active.font_size_pt,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Text color
                  <input
                    type="color"
                    value={active.color}
                    onChange={(e) =>
                      patchElement(active.id, { color: e.target.value })
                    }
                  />
                </label>
                <label>
                  Opacity
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.1}
                    value={active.opacity}
                    onChange={(e) =>
                      patchElement(active.id, {
                        opacity: numeric(e.target.value, active.opacity),
                      })
                    }
                  />
                </label>
              </div>
              {active.kind !== "shape" && (
                <small>
                  Text styles apply to annotations, figure captions and table
                  cells.
                </small>
              )}
              {active.kind === "table" && (
                <>
                  <ReportTableGeometryEditor
                    key={`${active.id}:${active.table_view}`}
                    element={active}
                    paging={plan?.iterations[0]?.tables?.[active.id]}
                    onChange={(table_geometry) =>
                      patchElement(active.id, { table_geometry })
                    }
                  />
                  <label>
                    Table output
                    <select
                      aria-label="Report table view"
                      value={active.table_view}
                      onChange={(e) =>
                        patchElement(active.id, {
                          table_view: e.target
                            .value as ReportElement["table_view"],
                          table_geometry: null,
                          column_ids: [],
                          column_start: 0,
                          row_start: 0,
                        })
                      }
                    >
                      <option value="data">Live data rows</option>
                      <option
                        value="pivot"
                        disabled={
                          !workspace.tables.find(
                            (t) => t.id === active.table_id,
                          )?.pivot
                        }
                      >
                        Saved pivot
                      </option>
                      <option
                        value="comparisons"
                        disabled={
                          !workspace.tables.find(
                            (t) => t.id === active.table_id,
                          )?.comparison
                        }
                      >
                        Saved comparisons
                      </option>
                    </select>
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      aria-label="Automatically continue table"
                      checked={active.auto_paginate}
                      onChange={(e) =>
                        patchElement(active.id, {
                          auto_paginate: e.target.checked,
                        })
                      }
                    />
                    Continue all remaining rows and columns on output pages
                  </label>
                  <label>
                    Start row
                    <input
                      aria-label="Table start row"
                      type="number"
                      min={1}
                      value={active.row_start + 1}
                      onChange={(e) =>
                        patchElement(active.id, {
                          row_start: Math.max(0, Number(e.target.value) - 1),
                        })
                      }
                    />
                  </label>
                  <label>
                    Maximum rows per page
                    <input
                      aria-label="Table row count"
                      type="number"
                      min={1}
                      max={200}
                      value={active.row_count}
                      onChange={(e) =>
                        patchElement(active.id, {
                          row_count: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    First measure column
                    <input
                      aria-label="Table start column"
                      type="number"
                      min={1}
                      max={512}
                      value={active.column_start + 1}
                      onChange={(e) =>
                        patchElement(active.id, {
                          column_start: Math.max(0, Number(e.target.value) - 1),
                        })
                      }
                    />
                  </label>
                  <label>
                    Measure columns per page
                    <input
                      aria-label="Table columns per page"
                      type="number"
                      min={1}
                      max={16}
                      value={active.columns_per_page}
                      onChange={(e) =>
                        patchElement(active.id, {
                          columns_per_page: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <details>
                    <summary>Measures to include</summary>
                    <p className="muted">
                      Select measures in report order. Clear the selection to
                      use all visible data columns or all configured
                      pivot/comparison measures.
                    </p>
                    {workspace.tables
                      .find((t) => t.id === active.table_id)
                      ?.columns.filter(
                        (column) =>
                          active.table_view === "data" ||
                          (active.table_view === "pivot"
                            ? workspace.tables
                                .find((t) => t.id === active.table_id)
                                ?.pivot?.measures.includes(column.id)
                            : workspace.tables
                                .find((t) => t.id === active.table_id)
                                ?.comparison?.measures.includes(column.id)),
                      )
                      .map((column) => (
                        <label key={column.id} className="check">
                          <input
                            type="checkbox"
                            aria-label={`Report measure ${column.name}`}
                            checked={active.column_ids.includes(column.id)}
                            onChange={(e) =>
                              patchElement(active.id, {
                                column_start: 0,
                                column_ids: e.target.checked
                                  ? [...active.column_ids, column.id]
                                  : active.column_ids.filter(
                                      (value) => value !== column.id,
                                    ),
                              })
                            }
                          />
                          {column.name}
                        </label>
                      ))}
                  </details>
                  {active.table_view === "pivot" && (
                    <label className="check">
                      <input
                        type="checkbox"
                        aria-label="Show pivot contributing counts"
                        checked={active.pivot_counts}
                        onChange={(e) =>
                          patchElement(active.id, {
                            pivot_counts: e.target.checked,
                          })
                        }
                      />
                      Show finite contributing count in every pivot cell
                    </label>
                  )}
                  {active.table_view === "comparisons" && (
                    <details open>
                      <summary>Comparison fields</summary>
                      {comparisonFields.map((field) => (
                        <label className="check" key={field.id}>
                          <input
                            type="checkbox"
                            aria-label={`Comparison field ${field.label}`}
                            checked={active.comparison_fields.includes(
                              field.id,
                            )}
                            disabled={
                              active.comparison_fields.length === 1 &&
                              active.comparison_fields.includes(field.id)
                            }
                            onChange={(e) =>
                              patchElement(active.id, {
                                column_start: 0,
                                comparison_fields: e.target.checked
                                  ? [...active.comparison_fields, field.id]
                                  : active.comparison_fields.filter(
                                      (value) => value !== field.id,
                                    ),
                              })
                            }
                          />
                          {field.label}
                        </label>
                      ))}
                    </details>
                  )}
                  <p className="muted">
                    Table comparisons use the selected cohort. Turn off “Iterate
                    sources” to retain the full saved table scope in every
                    batch. Output preview shows the generated continuation
                    pages.
                  </p>
                </>
              )}
              {active.kind === "biology" && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={active.follow_replacement}
                    onChange={(e) =>
                      patchElement(active.id, {
                        follow_replacement: e.target.checked,
                      })
                    }
                  />
                  Follow replacement fits
                </label>
              )}
              {active.kind === "population_comparison" &&
                (() => {
                  const result = workspace.comparison_results?.find(
                    (r) => r.id === active.result_id,
                  );
                  const presentation =
                    active.comparison_view ?? defaultComparisonFigureView();
                  const editView = (patch: Partial<typeof presentation>) =>
                    patchElement(active.id, {
                      comparison_view: { ...presentation, ...patch },
                    });
                  return (
                    <>
                      <label>
                        Compared population
                        <select
                          aria-label="Report comparison population"
                          value={
                            result?.request.inputs.findIndex(
                              (s) =>
                                s.sample_id === active.sample_id &&
                                s.gate_id === active.gate_id,
                            ) ?? -1
                          }
                          onChange={(e) => {
                            const source =
                              result?.request.inputs[Number(e.target.value)];
                            if (source)
                              patchElement(active.id, {
                                sample_id: source.sample_id,
                                gate_id: source.gate_id,
                              });
                          }}
                        >
                          <option value={-1} disabled>
                            Choose compared population
                          </option>
                          {result?.request.inputs.map((source, i) => {
                            const row = result.rows.find(
                              (r) =>
                                r.role === "target" &&
                                r.source.sample_id === source.sample_id &&
                                r.source.gate_id === source.gate_id,
                            );
                            return (
                              <option key={i} value={i}>
                                {row?.source_name} · {row?.population_name}
                              </option>
                            );
                          })}
                        </select>
                      </label>
                      <label>
                        Comparison parameter
                        <select
                          aria-label="Report comparison parameter"
                          value={active.comparison_parameter_id ?? ""}
                          onChange={(e) =>
                            patchElement(active.id, {
                              comparison_parameter_id: e.target.value,
                            })
                          }
                        >
                          {result?.request.parameters.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.label || p.channel}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Comparison graph
                        <select
                          aria-label="Report comparison graph"
                          value={presentation.mode}
                          onChange={(e) =>
                            editView({
                              mode: e.target.value as typeof presentation.mode,
                            })
                          }
                        >
                          <option value="histogram">Histogram</option>
                          <option value="cdf">Cumulative distribution</option>
                          <option value="difference">Signed difference</option>
                        </select>
                      </label>
                      <label>
                        Display smoothing
                        <input
                          aria-label="Report comparison smoothing"
                          type="number"
                          min={0}
                          max={16}
                          step={0.25}
                          disabled={presentation.mode === "cdf"}
                          value={presentation.smoothing}
                          onChange={(e) =>
                            editView({
                              smoothing: Math.max(
                                0,
                                Math.min(
                                  16,
                                  numeric(
                                    e.target.value,
                                    presentation.smoothing,
                                  ),
                                ),
                              ),
                            })
                          }
                        />
                      </label>
                      <label>
                        Control tint
                        <input
                          aria-label="Report comparison control tint"
                          type="color"
                          value={presentation.control_color}
                          onChange={(e) =>
                            editView({ control_color: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        Target tint
                        <input
                          aria-label="Report comparison target tint"
                          type="color"
                          value={presentation.target_color}
                          onChange={(e) =>
                            editView({ target_color: e.target.value })
                          }
                        />
                      </label>
                      {presentation.mode === "difference" && (
                        <label>
                          Difference vertical scale
                          <input
                            aria-label="Report comparison difference scale"
                            type="number"
                            min={0.1}
                            max={10}
                            step={0.1}
                            value={presentation.difference_scale}
                            onChange={(e) =>
                              editView({
                                difference_scale: Math.max(
                                  0.1,
                                  Math.min(
                                    10,
                                    numeric(
                                      e.target.value,
                                      presentation.difference_scale,
                                    ),
                                  ),
                                ),
                              })
                            }
                          />
                        </label>
                      )}
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={presentation.show_individuals}
                          onChange={(e) =>
                            editView({ show_individuals: e.target.checked })
                          }
                        />
                        Individual controls
                      </label>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={active.follow_replacement}
                          onChange={(e) =>
                            patchElement(active.id, {
                              follow_replacement: e.target.checked,
                            })
                          }
                        />
                        Follow reviewed refits
                      </label>
                    </>
                  );
                })()}
              {active.plot && (
                <>
                  <label>
                    Parameter X
                    <select
                      aria-label="Report X parameter"
                      value={active.plot.x}
                      onChange={(e) => patchPlot({ x: e.target.value })}
                    >
                      {workspace.samples
                        .find((sample) => sample.id === active.plot!.sample_id)
                        ?.channels.map((channel) => (
                          <option key={channel.name}>{channel.name}</option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Parameter Y
                    <select
                      aria-label="Report Y parameter"
                      value={active.plot.y ?? ""}
                      onChange={(e) =>
                        patchPlot({
                          y: e.target.value || null,
                          mode: e.target.value ? "density" : "histogram",
                          bounds: null,
                          normalization: "count",
                        })
                      }
                    >
                      <option value="">Histogram</option>
                      {workspace.samples
                        .find((sample) => sample.id === active.plot!.sample_id)
                        ?.channels.map((channel) => (
                          <option key={channel.name}>{channel.name}</option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Display
                    <select
                      aria-label="Report plot display"
                      value={active.plot.mode}
                      onChange={(e) =>
                        patchPlot({
                          mode: e.target.value,
                          normalization: "count",
                          bounds: null,
                          ...(e.target.value === "3d"
                            ? {
                                three_d: active.plot!.three_d ?? {
                                  z:
                                    workspace.samples.find(
                                      (s) => s.id === active.plot!.sample_id,
                                    )?.channels[2]?.name ?? active.plot!.x,
                                },
                              }
                            : {}),
                        })
                      }
                    >
                      {(active.plot.y
                        ? [
                            "density",
                            "scatter",
                            "pseudocolor",
                            "contour",
                            "zebra",
                            "3d",
                          ]
                        : ["histogram", "cdf"]
                      ).map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                  {active.plot.mode === "3d" && active.plot.three_d && (
                    <ReportThreeDSettings
                      view={active.plot.three_d}
                      workspace={workspace}
                      plot={active.plot}
                      onChange={(three_d) =>
                        patchPlot({
                          three_d,
                          ...(three_d.z !== active.plot!.three_d!.z ||
                          three_d.compensation !==
                            active.plot!.three_d!.compensation
                            ? { bounds: null }
                            : {}),
                        })
                      }
                    />
                  )}
                  <GraphControls
                    mode={active.plot.mode}
                    options={active.plot.graph_options ?? {}}
                    bins={active.plot.bins ?? 96}
                    onBinsChange={(bins) => patchPlot({ bins })}
                    onChange={(graph_options) =>
                      patchPlot({
                        graph_options,
                        ...(graph_options.axis_extent !==
                        active.plot!.graph_options?.axis_extent
                          ? { bounds: null }
                          : {}),
                      })
                    }
                  />
                  {active.plot.mode === "histogram" && (
                    <label>
                      Histogram scale
                      <select
                        aria-label="Histogram normalization"
                        value={active.plot.normalization ?? "count"}
                        onChange={(e) =>
                          patchPlot({
                            normalization: e.target
                              .value as PlotDefinition["normalization"],
                          })
                        }
                      >
                        <option value="count">Event count</option>
                        <option value="percent_population">
                          Percent of population
                        </option>
                        <option value="unit_area">
                          Unit area in displayed coordinates
                        </option>
                        <option value="peak">Relative peak</option>
                      </select>
                    </label>
                  )}
                  <label>
                    Axis bounds
                    <input
                      aria-label="Report axis bounds"
                      key={active.id + JSON.stringify(active.plot.bounds)}
                      defaultValue={active.plot.bounds?.join(", ") ?? ""}
                      placeholder={
                        active.plot.mode === "3d"
                          ? "Auto · xmin, xmax, ymin, ymax, zmin, zmax"
                          : active.plot.y
                            ? "Auto · xmin, xmax, ymin, ymax"
                            : "Auto · min, max"
                      }
                      onBlur={(e) => {
                        const value = e.target.value.trim();
                        patchPlot({
                          bounds: value ? value.split(",").map(Number) : null,
                        });
                      }}
                    />
                  </label>
                  <label>
                    Bins
                    <input
                      aria-label="Report bins"
                      type="number"
                      min={16}
                      max={384}
                      value={active.plot.bins ?? 96}
                      onChange={(e) =>
                        patchPlot({ bins: Number(e.target.value) })
                      }
                    />
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={active.plot.show_gates ?? true}
                      onChange={(e) =>
                        patchPlot({ show_gates: e.target.checked })
                      }
                    />
                    Population outlines
                  </label>
                  {[active.plot, ...(active.plot.overlays ?? [])].map(
                    (layer, index) => (
                      <div className="studio-layer" key={layer.id}>
                        <strong>
                          {index ? `Overlay ${index}` : "Primary acquisition"}
                        </strong>
                        <label>
                          Legend label
                          <input
                            aria-label={`Layer ${index + 1} legend`}
                            placeholder="Automatic acquisition / population"
                            value={
                              index
                                ? active.plot!.overlays![index - 1].label
                                : active.plot!.title
                            }
                            onChange={(e) => {
                              if (!index) patchPlot({ title: e.target.value });
                              else
                                patchPlot({
                                  overlays: active.plot!.overlays!.map(
                                    (value) =>
                                      value.id === layer.id
                                        ? { ...value, label: e.target.value }
                                        : value,
                                  ),
                                });
                            }}
                          />
                        </label>
                        <select
                          aria-label={`Layer ${index + 1} acquisition`}
                          value={layer.sample_id}
                          onChange={(e) => {
                            if (!index)
                              patchPlot({
                                sample_id: e.target.value,
                                gate_id: null,
                                coordinate_gate_id: null,
                                backgate_id: null,
                              });
                            else
                              patchPlot({
                                overlays: active.plot!.overlays!.map((value) =>
                                  value.id === layer.id
                                    ? {
                                        ...value,
                                        sample_id: e.target.value,
                                        gate_id: null,
                                        coordinate_gate_id: null,
                                        backgate_id: null,
                                      }
                                    : value,
                                ),
                              });
                          }}
                        >
                          {workspace.samples.map((sample) => (
                            <option key={sample.id} value={sample.id}>
                              {sample.name}
                            </option>
                          ))}
                        </select>
                        <select
                          aria-label={`Layer ${index + 1} population`}
                          value={layer.gate_id ?? ""}
                          onChange={(e) => {
                            if (!index)
                              patchPlot({ gate_id: e.target.value || null });
                            else
                              patchPlot({
                                overlays: active.plot!.overlays!.map((value) =>
                                  value.id === layer.id
                                    ? {
                                        ...value,
                                        gate_id: e.target.value || null,
                                      }
                                    : value,
                                ),
                              });
                          }}
                        >
                          <option value="">All events</option>
                          {workspace.gates
                            .filter(
                              (gate) => gate.sample_id === layer.sample_id,
                            )
                            .map((gate) => (
                              <option key={gate.id} value={gate.id}>
                                {gate.name}
                              </option>
                            ))}
                        </select>
                        <label>
                          Backgate highlight
                          <select
                            aria-label={`Layer ${index + 1} backgate`}
                            value={layer.backgate_id ?? ""}
                            onChange={(e) => {
                              if (!index)
                                patchPlot({
                                  backgate_id: e.target.value || null,
                                });
                              else
                                patchPlot({
                                  overlays: active.plot!.overlays!.map(
                                    (value) =>
                                      value.id === layer.id
                                        ? {
                                            ...value,
                                            backgate_id: e.target.value || null,
                                          }
                                        : value,
                                  ),
                                });
                            }}
                          >
                            <option value="">None</option>
                            {workspace.gates
                              .filter(
                                (gate) => gate.sample_id === layer.sample_id,
                              )
                              .map((gate) => (
                                <option key={gate.id} value={gate.id}>
                                  {gate.name}
                                </option>
                              ))}
                          </select>
                        </label>
                        <div className="button-group">
                          <input
                            aria-label={`Layer ${index + 1} color`}
                            type="color"
                            value={layer.color ?? "#087e8b"}
                            onChange={(e) => {
                              if (!index) patchPlot({ color: e.target.value });
                              else
                                patchPlot({
                                  overlays: active.plot!.overlays!.map(
                                    (value) =>
                                      value.id === layer.id
                                        ? { ...value, color: e.target.value }
                                        : value,
                                  ),
                                });
                            }}
                          />
                          <label className="check">
                            <input
                              type="checkbox"
                              checked={layer.locked_control ?? false}
                              onChange={(e) => {
                                if (!index)
                                  patchPlot({
                                    locked_control: e.target.checked,
                                  });
                                else
                                  patchPlot({
                                    overlays: active.plot!.overlays!.map(
                                      (value) =>
                                        value.id === layer.id
                                          ? {
                                              ...value,
                                              locked_control: e.target.checked,
                                            }
                                          : value,
                                    ),
                                  });
                              }}
                            />
                            Lock control
                          </label>
                          {index > 0 && (
                            <button
                              className="icon-button"
                              aria-label={`Remove overlay ${index}`}
                              onClick={() =>
                                patchPlot({
                                  overlays: active.plot!.overlays!.filter(
                                    (value) => value.id !== layer.id,
                                  ),
                                })
                              }
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </div>
                      </div>
                    ),
                  )}
                  <button
                    className="text-button"
                    disabled={
                      (active.plot.overlays?.length ?? 0) >= 15 ||
                      !workspace.samples.length
                    }
                    onClick={() =>
                      patchPlot({
                        overlays: [
                          ...(active.plot!.overlays ?? []),
                          {
                            id: id(),
                            sample_id: workspace.samples[0].id,
                            gate_id: null,
                            label: "",
                            color: "#b34772",
                            locked_control: false,
                          },
                        ],
                      })
                    }
                  >
                    Add overlay
                  </button>
                </>
              )}
            </fieldset>
          )}
          <div className="studio-section">
            <strong>Batch & sources</strong>
            <label>
              Iterator
              <select
                aria-label="Batch iterator"
                value={layout.batch.mode}
                onChange={(e) => {
                  update({
                    ...layout,
                    batch: {
                      ...layout.batch,
                      mode: e.target.value as ReportLayout["batch"]["mode"],
                      iterator_keyword:
                        layout.batch.iterator_keyword || "Donor",
                    },
                  });
                  setPage(0);
                }}
              >
                <option value="off">Single report</option>
                <option value="sample">Acquisition</option>
                <option value="keyword">Keyword / tag</option>
                <option value="panel">Ordered panel</option>
              </select>
            </label>
            {layout.batch.mode !== "off" && (
              <>
                <label>
                  Sample group
                  <select
                    aria-label="Batch group"
                    value={layout.batch.group_id ?? ""}
                    onChange={(e) =>
                      update({
                        ...layout,
                        batch: {
                          ...layout.batch,
                          group_id: e.target.value || null,
                        },
                      })
                    }
                  >
                    <option value="">All acquisitions</option>
                    {workspace.groups.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name}
                      </option>
                    ))}
                  </select>
                </label>
                {layout.batch.mode === "keyword" && (
                  <>
                    <label>
                      Iterator keyword
                      <input
                        aria-label="Iterator keyword"
                        value={layout.batch.iterator_keyword}
                        onChange={(e) =>
                          update({
                            ...layout,
                            batch: {
                              ...layout.batch,
                              iterator_keyword: e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                    <label>
                      Overlay discriminator
                      <input
                        aria-label="Discriminator keyword"
                        placeholder="e.g. Treatment"
                        value={layout.batch.discriminator_keyword}
                        onChange={(e) =>
                          update({
                            ...layout,
                            batch: {
                              ...layout.batch,
                              discriminator_keyword: e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                  </>
                )}
                {layout.batch.mode === "panel" && (
                  <label>
                    Acquisitions per panel
                    <input
                      aria-label="Panel size"
                      type="number"
                      min={1}
                      max={32}
                      value={layout.batch.panel_size}
                      onChange={(e) =>
                        update({
                          ...layout,
                          batch: {
                            ...layout.batch,
                            panel_size: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                )}
                <div className="studio-fields">
                  {(["tile_columns", "tile_rows"] as const).map((key) => (
                    <label key={key}>
                      {key.replace("tile_", "")}
                      <input
                        aria-label={`Batch ${key}`}
                        type="number"
                        min={1}
                        max={8}
                        value={layout.batch[key]}
                        onChange={(e) =>
                          update({
                            ...layout,
                            batch: {
                              ...layout.batch,
                              [key]: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
                <button
                  className="button small"
                  disabled={rendering}
                  onClick={() => setReviewOpen((value) => !value)}
                >
                  Review source mappings
                </button>
                <span>
                  {plan?.page_count ?? "…"} output pages ·{" "}
                  {reviewed === plan?.review_hash
                    ? "mappings accepted"
                    : "review required"}
                </span>
              </>
            )}
            <label>
              Export policy
              <select
                aria-label="Report export policy"
                value={layout.export_policy}
                onChange={(e) =>
                  update({
                    ...layout,
                    export_policy: e.target
                      .value as ReportLayout["export_policy"],
                  })
                }
              >
                <option value="current">Current results only</option>
                <option value="snapshots">
                  Include labeled historical snapshots
                </option>
                <option value="placeholders">
                  Include visible missing-source placeholders
                </option>
              </select>
            </label>
            <button
              className="text-button"
              disabled={!canExport}
              onClick={() => void exportArtifact("manifest")}
            >
              Export source manifest
            </button>
          </div>
        </aside>
        <div className="studio-workspace">
          <div className="studio-canvas-status">
            <span>
              {batchPreview ? "Report output" : "Page design"} ·{" "}
              {render?.geometry.width_mm ?? geometry.width_mm} ×{" "}
              {render?.geometry.height_mm ?? geometry.height_mm} mm
            </span>
            <span>
              {rendering
                ? "Updating figures…"
                : `${render?.issues.length ?? 0} source issues`}
            </span>
          </div>
          <div className="studio-sheet-scroll">
            <div
              className="studio-sheet"
              style={{
                width:
                  (render?.geometry.width_mm ?? geometry.width_mm) *
                  3.7795 *
                  zoom,
                aspectRatio: `${render?.geometry.width_mm ?? geometry.width_mm} / ${render?.geometry.height_mm ?? geometry.height_mm}`,
              }}
            >
              <div
                className="studio-svg"
                ref={svgHost}
                dangerouslySetInnerHTML={{ __html: render?.svg ?? "" }}
              />
              {!batchPreview && (
                <div
                  className="studio-selection-layer"
                  onPointerDown={() => setSelected([])}
                >
                  {layout.elements
                    .filter((element) => element.page === prototypePage)
                    .map((element) => (
                      <div
                        key={element.id}
                        className={`studio-object-hit ${selected.includes(element.id) ? "selected" : ""}`}
                        role="button"
                        tabIndex={0}
                        aria-label={`Select ${element.kind}: ${element.title || element.id}`}
                        style={{
                          left: `${(element.x_mm / geometry.width_mm) * 100}%`,
                          top: `${(element.y_mm / geometry.height_mm) * 100}%`,
                          width: `${(element.width_mm / geometry.width_mm) * 100}%`,
                          height: `${(element.height_mm / geometry.height_mm) * 100}%`,
                          transform: `rotate(${element.rotation}deg)`,
                        }}
                        onPointerDown={(event) => pointerStart(event, element)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ")
                            setSelected([element.id]);
                          if (event.key === "Delete") remove();
                          if (
                            event.key.startsWith("Arrow") &&
                            !element.position_locked
                          ) {
                            event.preventDefault();
                            patchElement(element.id, {
                              x_mm: Math.max(
                                0,
                                Math.min(
                                  geometry.width_mm - element.width_mm,
                                  element.x_mm +
                                    (event.key === "ArrowLeft"
                                      ? -1
                                      : event.key === "ArrowRight"
                                        ? 1
                                        : 0),
                                ),
                              ),
                              y_mm: Math.max(
                                0,
                                Math.min(
                                  geometry.height_mm - element.height_mm,
                                  element.y_mm +
                                    (event.key === "ArrowUp"
                                      ? -1
                                      : event.key === "ArrowDown"
                                        ? 1
                                        : 0),
                                ),
                              ),
                            });
                          }
                        }}
                      >
                        {selected.includes(element.id) &&
                          !element.position_locked &&
                          element.rotation === 0 && (
                            <span
                              className="studio-resize"
                              aria-label="Resize object"
                              onPointerDown={(event) =>
                                pointerStart(event, element, true)
                              }
                            />
                          )}
                      </div>
                    ))}
                </div>
              )}
              {!layout.elements.length && (
                <div className="studio-empty">
                  <Plus size={30} />
                  <h2>Build your report</h2>
                  <p>
                    Add the current plot, a saved table, a fitted model or a
                    live annotation.
                  </p>
                </div>
              )}
            </div>
          </div>
          {render && render.issues.length > 0 && (
            <div className="studio-issues" role="status">
              {displayIssue(render.issues).map((value) => (
                <p key={value}>{value}</p>
              ))}
            </div>
          )}
          {reviewOpen && plan && (
            <section className="studio-review">
              <h2>Batch source review</h2>
              <p>
                Each acquisition and population is resolved explicitly. Locked
                controls remain fixed. Changes invalidate this review.
              </p>
              {plan.notices.map((notice, index) => (
                <p key={index} className="form-error">
                  {notice.message}
                </p>
              ))}
              {plan.iterations.map((iteration) => (
                <details key={iteration.key} open={plan.iterations.length <= 6}>
                  <summary>
                    {iteration.label} · {iteration.issues.length} issues
                  </summary>
                  {iteration.issues.map((notice, index) => (
                    <p key={index} className="form-error">
                      {notice.message}
                    </p>
                  ))}
                  {plan.prototype_sources.map((source) => (
                    <label key={source}>
                      {workspace.samples.find((sample) => sample.id === source)
                        ?.name ?? "Missing acquisition"}{" "}
                      →
                      <select
                        aria-label={`Target ${iteration.label} ${source}`}
                        value={iteration.mapping[source] ?? ""}
                        onChange={(e) => {
                          const overrides = clone(layout.batch.overrides),
                            mapping = { ...(overrides[iteration.key] ?? {}) };
                          if (e.target.value) mapping[source] = e.target.value;
                          else delete mapping[source];
                          overrides[iteration.key] = mapping;
                          update({
                            ...layout,
                            batch: { ...layout.batch, overrides },
                          });
                        }}
                      >
                        <option value="">Automatic mapping / unresolved</option>
                        {workspace.samples
                          .filter((sample) =>
                            iteration.sample_ids.includes(sample.id),
                          )
                          .map((sample) => (
                            <option key={sample.id} value={sample.id}>
                              {sample.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  ))}
                  {iteration.bindings
                    .filter((binding) => binding.source_gate_id)
                    .map((binding, index) => (
                      <label key={`${binding.element_id}:${index}`}>
                        Population{" "}
                        {
                          workspace.gates.find(
                            (gate) => gate.id === binding.source_gate_id,
                          )?.name
                        }{" "}
                        →{" "}
                        {
                          workspace.samples.find(
                            (sample) => sample.id === binding.sample_id,
                          )?.name
                        }
                        <select
                          disabled={binding.locked_control}
                          aria-label={`Population mapping ${iteration.label} ${binding.source_gate_id}`}
                          value={
                            Object.hasOwn(
                              layout.batch.population_overrides[
                                iteration.key
                              ] ?? {},
                              binding.source_gate_id!,
                            )
                              ? (layout.batch.population_overrides[
                                  iteration.key
                                ][binding.source_gate_id!] ?? "all")
                              : "auto"
                          }
                          onChange={(e) => {
                            const overrides = clone(
                                layout.batch.population_overrides,
                              ),
                              mapping = { ...(overrides[iteration.key] ?? {}) };
                            if (e.target.value === "auto")
                              delete mapping[binding.source_gate_id!];
                            else
                              mapping[binding.source_gate_id!] =
                                e.target.value === "all"
                                  ? null
                                  : e.target.value;
                            overrides[iteration.key] = mapping;
                            update({
                              ...layout,
                              batch: {
                                ...layout.batch,
                                population_overrides: overrides,
                              },
                            });
                          }}
                        >
                          <option value="auto">
                            Exact path ·{" "}
                            {workspace.gates.find(
                              (gate) => gate.id === binding.gate_id,
                            )?.name ?? "Unresolved"}
                          </option>
                          <option value="all">Explicitly use All events</option>
                          {workspace.gates
                            .filter(
                              (gate) => gate.sample_id === binding.sample_id,
                            )
                            .map((gate) => (
                              <option key={gate.id} value={gate.id}>
                                {gate.name}
                              </option>
                            ))}
                        </select>
                      </label>
                    ))}
                  {iteration.bindings
                    .filter((binding) => binding.locked_control)
                    .map((binding, index) => (
                      <small key={index}>
                        Fixed control:{" "}
                        {
                          workspace.samples.find(
                            (sample) => sample.id === binding.sample_id,
                          )?.name
                        }
                      </small>
                    ))}
                </details>
              ))}
              <button
                className="button primary"
                disabled={rendering || !plan.exportable}
                onClick={() => {
                  setReviewed(plan.review_hash);
                  setBatchPreview(true);
                  setPage(0);
                }}
              >
                Accept these source mappings
              </button>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function ReportThreeDSettings({
  view,
  workspace,
  plot,
  onChange,
}: {
  view: ThreeDView;
  workspace: Workspace;
  plot: PlotDefinition;
  onChange: (view: ThreeDView) => void;
}) {
  const names = new Set(
    workspace.samples
      .find((s) => s.id === plot.sample_id)
      ?.channels.map((c) => c.name) ?? [],
  );
  for (const dim of workspace.gates.find(
    (g) => g.id === plot.coordinate_gate_id,
  )?.dimensions ?? [])
    names.add(dim.channel);
  const patch = (value: Partial<ThreeDView>) => onChange({ ...view, ...value });
  const selector = (field: "z" | "color_by" | "size_by", label: string) => (
    <label>
      {label}
      <select
        aria-label={`Report 3D ${label}`}
        value={view[field] ?? ""}
        onChange={(e) =>
          patch({
            [field]: e.target.value || null,
            ...(field === "z"
              ? { z_transform: null }
              : field === "color_by"
                ? { color_transform: null, color_bounds: null }
                : { size_transform: null, size_bounds: null }),
          })
        }
      >
        {field !== "z" && <option value="">Uniform</option>}
        {Array.from(names).map((name) => (
          <option key={name}>{name}</option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="report-three-d-settings">
      {selector("z", "Z parameter")}
      {selector("color_by", "color parameter")}
      {selector("size_by", "size parameter")}
      <label>
        Coordinates
        <select
          aria-label="Report 3D compensation"
          value={view.compensation ?? "coordinate"}
          onChange={(e) =>
            patch({
              compensation: e.target.value as ThreeDView["compensation"],
            })
          }
        >
          <option value="coordinate">Current compensation</option>
          <option value="uncompensated">Uncompensated</option>
        </select>
      </label>
      {(
        [
          {
            field: "yaw",
            label: "Yaw (radians)",
            fallback: -0.65,
            min: -Math.PI,
            max: Math.PI,
          },
          {
            field: "pitch",
            label: "Pitch (radians)",
            fallback: 0.45,
            min: -1.5,
            max: 1.5,
          },
          {
            field: "zoom",
            label: "Camera zoom",
            fallback: 1,
            min: 0.1,
            max: 10,
          },
          {
            field: "point_size",
            label: "Point size",
            fallback: 2,
            min: 0.5,
            max: 12,
          },
          {
            field: "opacity",
            label: "Opacity",
            fallback: 0.7,
            min: 0.05,
            max: 1,
          },
        ] as const
      ).map(({ field, label, fallback, min, max }) => (
        <label key={field}>
          {label}
          <input
            aria-label={`Report 3D ${field}`}
            type="number"
            step="any"
            min={min}
            max={max}
            value={view[field] ?? fallback}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value && Number.isFinite(n) && n >= min && n <= max)
                patch({ [field]: n });
            }}
          />
        </label>
      ))}
      {[0, 1].map((i) => (
        <label key={i}>
          Pan {i === 0 ? "X" : "Y"}
          <input
            aria-label={`Report 3D pan ${i === 0 ? "X" : "Y"}`}
            type="number"
            min="-5"
            max="5"
            step="any"
            value={view.pan?.[i] ?? 0}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (e.target.value && Number.isFinite(n) && Math.abs(n) <= 5) {
                const pan: [number, number] = [...(view.pan ?? [0, 0])];
                pan[i] = n;
                patch({ pan });
              }
            }}
          />
        </label>
      ))}
      {(
        [
          { field: "show_cube", label: "Show cube" },
          { field: "show_labels", label: "Show labels" },
          { field: "all_events", label: "All events in view" },
        ] as const
      ).map(({ field, label }) => (
        <label className="check" key={field}>
          <input
            aria-label={`Report 3D ${label}`}
            type="checkbox"
            checked={view[field] !== false}
            onChange={(e) => patch({ [field]: e.target.checked })}
          />
          {label}
        </label>
      ))}
    </div>
  );
}
