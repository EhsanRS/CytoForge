import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Copy,
  Download,
  Grid2X2,
  Plus,
  Redo2,
  Save,
  Undo2,
  Upload,
} from "lucide-react";
import { api, download, post } from "../api";
import type {
  PlateDefinition,
  PlateEvaluation,
  PlateGeometry,
  Workspace,
} from "../types";
import { id } from "../types";
import {
  freshPlate,
  initialPlateSession,
  numericColumn,
  parsePlateSession,
  plateSessionKey,
  plateDimensions,
  plateGeometryLabel,
  rowName,
  wellName,
  type PlateSession,
} from "../plates";
import { ErrorState, Modal, Tag } from "./Common";
import type { Commit } from "./Editors";
import { PlateMeasurements } from "./PlateMeasurements";
import { validPlateGeometry } from "../plateGeometry";
import { plateAcquisitionPlot } from "../platePlotWindows";

interface Issue {
  message: string;
  line?: number;
  well?: string;
  plate?: string;
  sample_ids?: string[];
}
interface AnnotationReview {
  changes: {
    well: string;
    sample: string;
    sample_id: string;
    key: string;
    before: string | null;
    after: string | null;
  }[];
  change_count: number;
  sample_count: number;
  empty_wells: string[];
  unchanged: number;
  review_hash: string;
  revision: number;
}
type Review =
  | {
      kind: "stage";
      title: string;
      plate: PlateDefinition;
      original: string;
      lines: string[];
      issues: Issue[];
      records?: Record<string, unknown>[];
    }
  | {
      kind: "discover";
      plates: PlateDefinition[];
      issues: Issue[];
      revision: number;
      original: string;
      assigned_acquisitions: number;
    }
  | {
      kind: "apply";
      data: AnnotationReview;
      body: Record<string, unknown>;
      original: string;
    }
  | { kind: "switch"; plate: PlateDefinition };
const printValue = (value: number | string | null | undefined, decimals = 2) =>
  value == null
    ? "—"
    : typeof value === "number"
      ? value.toLocaleString(undefined, { maximumFractionDigits: decimals })
      : value;
const printDomain = (value: number | null | undefined) =>
  value != null &&
  ((value !== 0 && Math.abs(value) < 0.0001) || Math.abs(value) >= 1e6)
    ? value.toLocaleString(undefined, {
        notation: "scientific",
        maximumFractionDigits: 5,
      })
    : printValue(value, 4);

export function PlatePanel({
  workspace,
  groupId,
  commit,
  busy,
  onOpen,
}: {
  workspace: Workspace;
  groupId: string | null;
  commit: Commit;
  busy: boolean;
  onOpen: (sample: string) => void;
}) {
  const [session, setSession] = useState<PlateSession>(() =>
    initialPlateSession(workspace),
  );
  const plate = session.plate;
  const [geometryRows, setGeometryRows] = useState(
    () => plateDimensions(plate)[0],
  );
  const [geometryColumns, setGeometryColumns] = useState(
    () => plateDimensions(plate)[1],
  );
  const [mappingRows, setMappingRows] = useState(8);
  const [mappingColumns, setMappingColumns] = useState(12);
  const [nativeDraftReady, setNativeDraftReady] = useState(
    !window.cytoforgeDesktop?.getPlateDraft,
  );
  const latestDraft = useRef<string | null>(null);
  const nativeReadyRef = useRef(nativeDraftReady);
  nativeReadyRef.current = nativeDraftReady;
  const [past, setPast] = useState<PlateSession[]>([]),
    [future, setFuture] = useState<PlateSession[]>([]);
  const [selected, setSelected] = useState<string[]>(["A01"]);
  const [focus, setFocus] = useState(0),
    anchor = useRef(0),
    grid = useRef<HTMLTableElement>(null);
  const [column, setColumn] = useState(plate.columns[0].id);
  const [tool, setTool] = useState<"well" | "measurements" | "import">("well");
  const [working, setWorking] = useState(false),
    [error, setError] = useState("");
  const [review, setReview] = useState<Review | null>(null),
    [acknowledged, setAcknowledged] = useState(false);
  const [keyword, setKeyword] = useState("Treatment"),
    [value, setValue] = useState(""),
    [clearValue, setClearValue] = useState(false);
  const [applyAll, setApplyAll] = useState(true),
    [applyMode, setApplyMode] = useState("replace"),
    [identity, setIdentity] = useState(false);
  const [groupName, setGroupName] = useState("Plate selection"),
    [sampleSearch, setSampleSearch] = useState("");
  const [assignment, setAssignment] = useState<string[]>([]);
  const [wellColumn, setWellColumn] = useState("Well ID"),
    [plateColumn, setPlateColumn] = useState(""),
    [clearBlanks, setClearBlanks] = useState(false);
  const [mapping, setMapping] = useState({
    group_id: groupId ?? "",
    well_key: "WELL ID",
    plate_key: "PLATE ID",
    source: "auto",
    format: "",
    include_replicates: false,
  });
  const [series, setSeries] = useState({
    keyword: "Concentration",
    unit: "",
    unit_keyword: "Concentration unit",
    start_well: "A01",
    steps: 8,
    replicates: 1,
    direction: "columns",
    operation: "multiply",
    start: 1,
    factor: 0.5,
    increment: 1,
  });
  const [bounds, setBounds] = useState({
    column: plate.columns[0].id,
    lo: "",
    hi: "",
  });
  const [exportFormat, setExportFormat] = useState("svg");
  const draftJSON = JSON.stringify(plate);
  const saved = workspace.plates.find((p) => p.id === plate.id);
  const savedJSON = saved ? JSON.stringify(saved) : null;
  const dirty = draftJSON !== session.baseline;
  latestDraft.current = dirty ? JSON.stringify(session) : null;
  const conflict = savedJSON !== session.baseline;
  const [debounced, setDebounced] = useState(plate);
  const [rows, cols] = plateDimensions(plate);
  useEffect(() => {
    setGeometryRows(rows);
    setGeometryColumns(cols);
  }, [plate.id, rows, cols]);
  const wellNames = useMemo(
    () =>
      Array.from({ length: rows * cols }, (_, i) =>
        wellName(Math.floor(i / cols), i % cols),
      ),
    [rows, cols],
  );
  const firstWell = selected[0] ?? "A01";
  const assignedJSON = JSON.stringify(plate.assignments[firstWell] ?? []);

  function load(next: PlateDefinition, document: Workspace = workspace) {
    const persisted = document.plates.find((p) => p.id === next.id);
    setSession({
      plate: structuredClone(next),
      baseline: persisted ? JSON.stringify(persisted) : null,
      baseRevision: document.revision,
    });
    setPast([]);
    setFuture([]);
    setSelected(["A01"]);
    setFocus(0);
    anchor.current = 0;
    setColumn(next.columns[0].id);
    setBounds({ column: next.columns[0].id, lo: "", hi: "" });
    setReview(null);
    setError("");
  }
  function change(next: PlateDefinition) {
    setPast((history) => [...history.slice(-19), session]);
    setFuture([]);
    setSession((current) => ({
      ...current,
      plate: next,
      ...(next.id !== current.plate.id
        ? {
            baseline: workspace.plates.find((p) => p.id === next.id)
              ? JSON.stringify(workspace.plates.find((p) => p.id === next.id))
              : null,
            baseRevision: workspace.revision,
          }
        : {}),
    }));
    setError("");
  }
  function set<K extends keyof PlateDefinition>(
    key: K,
    next: PlateDefinition[K],
  ) {
    change({ ...plate, [key]: next });
  }
  function view(patch: Partial<PlateDefinition["view"]>) {
    set("view", { ...plate.view, ...patch });
  }
  function open(next: PlateDefinition) {
    if (dirty) setReview({ kind: "switch", plate: next });
    else load(next);
  }
  function show(next: Review) {
    setReview(next);
    setAcknowledged(false);
  }
  async function act(action: () => Promise<void>) {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "The plate operation failed",
      );
    } finally {
      setWorking(false);
    }
  }
  async function save(): Promise<Workspace> {
    const document = await commit(
      "/plates/save",
      {
        plate: ready ? evaluation!.plate : plate,
        base_revision: session.baseRevision,
      },
      "Saved plate",
    );
    load(
      document.plates.find((p) => p.id === plate.id)!,
      document,
    );
    return document;
  }
  const body = () => ({ revision: workspace.revision, plate });
  const endpoint = (path: string) =>
    `/workspaces/${workspace.id}/plates${path}`;

  useEffect(() => {
    const bridge = window.cytoforgeDesktop;
    if (!bridge?.getPlateDraft) return;
    let cancelled = false;
    bridge
      .getPlateDraft(workspace.id)
      .then((content) => {
        if (cancelled) return;
        if (content) {
          const recovered = parsePlateSession(content);
          if (recovered) setSession(recovered);
          else
            setError(
              "The desktop plate draft is malformed. Saved workspace definitions remain available.",
            );
        }
      })
      .catch((failure) => {
        if (!cancelled)
          setError(`Desktop draft recovery failed: ${failure.message}`);
      })
      .finally(() => {
        if (!cancelled) setNativeDraftReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [workspace.id]);
  useEffect(() => {
    const bridge = window.cytoforgeDesktop;
    if (!nativeDraftReady || !bridge?.savePlateDraft) return;
    const timer = setTimeout(
      () => {
        void bridge
          .savePlateDraft(workspace.id, latestDraft.current)
          .then((saved) => {
            if (!saved)
              setError(
                "The desktop draft could not be cached. Save or export this plate before closing.",
              );
          })
          .catch((failure) =>
            setError(`Desktop draft cache failed: ${failure.message}`),
          );
      },
      dirty ? 150 : 0,
    );
    return () => clearTimeout(timer);
  }, [session, nativeDraftReady, dirty, workspace.id]);
  useEffect(
    () => () => {
      if (nativeReadyRef.current && window.cytoforgeDesktop?.savePlateDraft)
        void window.cytoforgeDesktop
          .savePlateDraft(workspace.id, latestDraft.current)
          .catch(() => {});
    },
    [workspace.id],
  );
  useEffect(() => {
    const flush = () => {
      if (nativeReadyRef.current && window.cytoforgeDesktop?.savePlateDraft)
        void window.cytoforgeDesktop
          .savePlateDraft(workspace.id, latestDraft.current)
          .catch(() => {});
    };
    window.addEventListener("cytoforge:flush-plate-draft", flush);
    return () =>
      window.removeEventListener("cytoforge:flush-plate-draft", flush);
  }, [workspace.id]);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(plate), 250);
    return () => clearTimeout(timer);
  }, [plate]);
  useEffect(() => {
    // Unrelated workspace edits may be rebased only if the saved plate is unchanged.
    if (workspace.revision !== session.baseRevision && !conflict)
      setSession((current) => ({
        ...current,
        baseRevision: workspace.revision,
      }));
    else if (!dirty && conflict)
      load(saved ?? workspace.plates[0] ?? freshPlate());
  }, [workspace.revision, savedJSON, session.baseRevision, conflict, dirty]);
  useEffect(() => {
    try {
      if (dirty)
        sessionStorage.setItem(
          plateSessionKey(workspace.id),
          JSON.stringify(session),
        );
      else sessionStorage.removeItem(plateSessionKey(workspace.id));
    } catch {
      if (!window.cytoforgeDesktop?.savePlateDraft)
        setError(
          "This draft exceeds the session cache capacity. Save or export it before leaving this view.",
        );
    }
  }, [session, dirty, workspace.id]);
  useEffect(() => {
    setAssignment(JSON.parse(assignedJSON));
  }, [firstWell, assignedJSON]);
  useEffect(() => {
    setSelected((current) => current.filter((w) => wellNames.includes(w)));
    setFocus((current) => Math.min(current, wellNames.length - 1));
  }, [wellNames]);

  const query = useQuery({
    queryKey: ["plate", workspace.id, workspace.revision, debounced],
    queryFn: ({ signal }) =>
      api<PlateEvaluation>(endpoint("/evaluate"), {
        method: "POST",
        body: JSON.stringify({
          revision: workspace.revision,
          plate: debounced,
        }),
        signal,
      }),
    placeholderData: keepPreviousData,
  });
  const evaluation = query.data;
  const ready =
    !!evaluation &&
    !query.isPlaceholderData &&
    evaluation.revision === workspace.revision &&
    draftJSON === JSON.stringify(debounced) &&
    !query.error;
  useEffect(() => {
    if (!ready) return;
    const columns = plate.columns.map((c) => {
      const normalized = evaluation!.plate.columns.find((v) => v.id === c.id)!;
      return JSON.stringify(c.formula_refs) ===
        JSON.stringify(normalized.formula_refs)
        ? c
        : { ...c, formula_refs: normalized.formula_refs };
    });
    if (columns.some((c, i) => c !== plate.columns[i]))
      setSession((current) => ({
        ...current,
        plate: { ...current.plate, columns },
      }));
  }, [ready, evaluation, plate.columns]);
  const wellData = new Map(evaluation?.wells.map((w) => [w.well, w]) ?? []);
  const inspected = wellData.get(firstWell);
  const currentColumn =
    plate.columns.find(
      (c) => c.id === (plate.view.primary ?? plate.columns[0].id),
    ) ?? plate.columns[0];
  const numeric = plate.columns.filter(numericColumn);

  function selectWell(
    index: number,
    event: Pick<MouseEvent | KeyboardEvent, "shiftKey" | "ctrlKey" | "metaKey">,
  ) {
    const name = wellNames[index];
    if (event.shiftKey) {
      const a = Math.min(anchor.current, wellNames.length - 1),
        r1 = Math.floor(a / cols),
        c1 = a % cols;
      const r2 = Math.floor(index / cols),
        c2 = index % cols;
      const rectangle = wellNames.filter(
        (_, i) =>
          Math.floor(i / cols) >= Math.min(r1, r2) &&
          Math.floor(i / cols) <= Math.max(r1, r2) &&
          i % cols >= Math.min(c1, c2) &&
          i % cols <= Math.max(c1, c2),
      );
      setSelected(
        event.ctrlKey || event.metaKey
          ? Array.from(new Set([...selected, ...rectangle]))
          : rectangle,
      );
    } else if (event.ctrlKey || event.metaKey) {
      setSelected(
        selected.includes(name)
          ? selected.filter((w) => w !== name)
          : [...selected, name],
      );
      anchor.current = index;
    } else {
      setSelected([name]);
      anchor.current = index;
    }
    setFocus(index);
  }
  function move(index: number, event: KeyboardEvent<HTMLButtonElement>) {
    const delta: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -cols,
      ArrowDown: cols,
    };
    const next =
      event.key === "Home"
        ? event.ctrlKey
          ? 0
          : index - (index % cols)
        : event.key === "End"
          ? event.ctrlKey
            ? wellNames.length - 1
            : index - (index % cols) + cols - 1
          : event.key in delta
            ? Math.max(
                0,
                Math.min(wellNames.length - 1, index + delta[event.key]),
              )
            : null;
    if (next === null) return;
    event.preventDefault();
    selectWell(next, event);
    grid.current
      ?.querySelector<HTMLButtonElement>(`[data-well="${wellNames[next]}"]`)
      ?.focus();
  }
  function selectBand(names: string[], event: MouseEvent<HTMLButtonElement>) {
    setSelected(
      event.ctrlKey || event.metaKey
        ? Array.from(new Set([...selected, ...names]))
        : names,
    );
    anchor.current = wellNames.indexOf(names[0]);
  }
  async function resize(
    format: PlateDefinition["format"],
    geometry?: PlateGeometry,
  ) {
    const result = await post<{
      plate: PlateDefinition;
      removed_wells: string[];
      removed_acquisitions: number;
      removed_annotation_keys: number;
    }>(endpoint("/resize"), {
      ...body(),
      format,
      ...(geometry ? { geometry } : {}),
    });
    show({
      kind: "stage",
      title: "Review plate format",
      plate: result.plate,
      original: draftJSON,
      issues: [],
      lines: [
        `${plateGeometryLabel(plate)} → ${plateGeometryLabel(result.plate)}`,
        `${result.removed_acquisitions} acquisition assignments and ${result.removed_annotation_keys} planned annotation keys removed from this plate.`,
        "Acquisitions and applied sample keywords remain available.",
        `Outside wells: ${result.removed_wells.join(", ") || "none"}`,
      ],
    });
  }
  function stageKeyword() {
    if (
      !keyword.trim() ||
      keyword.length > 160 ||
      /[\x00-\x1f]/.test(keyword)
    ) {
      setError("Choose a nonempty annotation key without control characters.");
      return;
    }
    const annotations = { ...plate.annotations };
    for (const well of selected)
      annotations[well] = {
        ...annotations[well],
        [keyword]: clearValue ? null : value,
      };
    change({ ...plate, annotations });
  }
  async function reviewAnnotations() {
    const wells = applyAll
      ? identity
        ? Array.from(
            new Set([
              ...Object.keys(plate.assignments),
              ...Object.keys(plate.annotations),
            ]),
          )
        : []
      : selected;
    const request = {
      ...body(),
      wells,
      mode: applyMode,
      include_identity: identity,
    };
    const data = await post<AnnotationReview>(
      endpoint("/annotations/review"),
      request,
    );
    show({ kind: "apply", data, body: request, original: draftJSON });
  }
  async function fileImport(file: File, template: boolean) {
    if (file.size > 4 * 1024 * 1024)
      throw new Error("Plate imports support files up to four MiB.");
    const text = await file.text();
    if (template) {
      const result = await post<{ plate: PlateDefinition; warnings: string[] }>(
        endpoint("/template/import"),
        { revision: workspace.revision, text },
      );
      show({
        kind: "stage",
        title: "Review plate template",
        plate: result.plate,
        original: draftJSON,
        issues: result.warnings.map((message) => ({ message })),
        lines: [
          `${result.plate.name}: ${plateGeometryLabel(result.plate)}`,
          "The template carries its annotation plan and measurements. Assign acquisitions after staging.",
        ],
      });
    } else {
      const result = await post<{
        plate: PlateDefinition;
        issues: Issue[];
        staged_rows: number;
        skipped_other_plates: number;
        records: Record<string, unknown>[];
      }>(endpoint("/import/csv"), {
        ...body(),
        text,
        filename: file.name,
        well_column: wellColumn,
        plate_column: plateColumn,
        clear_blanks: clearBlanks,
      });
      show({
        kind: "stage",
        title: "Review CSV annotations",
        plate: result.plate,
        original: draftJSON,
        issues: result.issues,
        records: result.records,
        lines: [
          `${result.staged_rows} valid well rows ready to stage`,
          `${result.skipped_other_plates} rows skipped for other plate identifiers`,
          "Duplicate normalized well rows are omitted together. Empty wells retain their plans until acquisitions are assigned.",
        ],
      });
    }
  }
  const reviewCurrent =
    review && (review.kind === "switch" || review.original === draftJSON);
  const disabled = working || busy || !nativeDraftReady;
  return (
    <div className="plate-workbench">
      <fieldset className="plate-controls" disabled={disabled}>
        <div className="plate-heading">
          <div>
            <span className="eyebrow">EXPERIMENT · PLATE ANALYSIS</span>
            <h2>
              <Grid2X2 size={22} /> Plate workbench
            </h2>
            <p className="muted">
              Map acquisitions, plan treatments, and compare populations across
              the plate.
            </p>
          </div>
          <div className="button-group">
            <Tag>{dirty ? "Unsaved plate draft" : "Saved plate"}</Tag>
            <button
              className="button primary"
              disabled={!ready || conflict || !plate.name.trim()}
              onClick={() =>
                void act(async () => {
                  await save();
                })
              }
            >
              <Save size={15} />
              Save plate
            </button>
          </div>
        </div>
        <div className="plate-definition panel">
          <label className="field">
            Saved plate
            <select
              aria-label="Saved plate"
              value={saved ? plate.id : ""}
              onChange={(e) =>
                open(
                  workspace.plates.find((p) => p.id === e.target.value) ??
                    freshPlate(),
                )
              }
            >
              <option value="">New plate</option>
              {workspace.plates.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Name
            <input
              aria-label="Plate name"
              maxLength={160}
              value={plate.name}
              onChange={(e) => set("name", e.target.value)}
            />
          </label>
          <label className="field">
            Plate identifier
            <input
              aria-label="Plate identifier"
              maxLength={512}
              value={plate.plate_key}
              placeholder="Matches the CSV / instrument plate ID"
              onChange={(e) => set("plate_key", e.target.value)}
            />
          </label>
          <label className="field">
            Format
            <select
              aria-label="Plate format"
              value={plate.format}
              onChange={(e) => {
                if (e.target.value !== "custom")
                  void act(() =>
                    resize(Number(e.target.value) as PlateDefinition["format"]),
                  );
              }}
            >
              {[6, 12, 24, 48, 96, 384, 1536].map((f) => (
                <option key={f} value={f}>
                  {f} wells
                </option>
              ))}
              {plate.format === "custom" && (
                <option value="custom">
                  Custom: {rows} × {cols}
                </option>
              )}
            </select>
          </label>
          <div className="button-group">
            <button className="button small" onClick={() => open(freshPlate())}>
              <Plus size={14} />
              New plate
            </button>
            <button
              className="button small"
              onClick={() =>
                open({
                  ...structuredClone(plate),
                  id: id(),
                  name: `${plate.name.slice(0, 150)} copy`,
                })
              }
            >
              <Copy size={14} />
              Duplicate
            </button>
            <button
              className="icon-button"
              aria-label="Undo plate draft"
              disabled={!past.length}
              onClick={() => {
                const previous = past.at(-1)!;
                setPast(past.slice(0, -1));
                setFuture([...future, session]);
                setSession(previous);
              }}
            >
              <Undo2 size={16} />
            </button>
            <button
              className="icon-button"
              aria-label="Redo plate draft"
              disabled={!future.length}
              onClick={() => {
                const next = future.at(-1)!;
                setFuture(future.slice(0, -1));
                setPast([...past, session]);
                setSession(next);
              }}
            >
              <Redo2 size={16} />
            </button>
          </div>
        </div>
        <details className="plate-custom-geometry">
          <summary>Custom plate dimensions</summary>
          <div className="plate-custom-geometry-fields">
            <label className="field">
              Rows
              <input
                aria-label="Custom plate rows"
                type="number"
                min={1}
                max={96}
                step={1}
                value={geometryRows}
                onChange={(e) => setGeometryRows(Number(e.target.value))}
              />
            </label>
            <label className="field">
              Columns
              <input
                aria-label="Custom plate columns"
                type="number"
                min={1}
                max={96}
                step={1}
                value={geometryColumns}
                onChange={(e) => setGeometryColumns(Number(e.target.value))}
              />
            </label>
            <button
              className="button small"
              disabled={
                busy ||
                working ||
                !validPlateGeometry({
                  rows: geometryRows,
                  columns: geometryColumns,
                })
              }
              onClick={() =>
                void act(() =>
                  resize("custom", {
                    rows: geometryRows,
                    columns: geometryColumns,
                  }),
                )
              }
            >
              Review custom dimensions
            </button>
          </div>
          <p className="form-note">
            Up to 96 rows or columns and 1,536 well positions. Review any
            assignments and planned annotations outside the new dimensions
            before staging.
          </p>
        </details>
        {conflict && (
          <div className="plate-notice" role="alert">
            This saved plate changed in another action. Your draft is retained.
            Export it or reload the saved plate before applying changes.
            <button
              className="button small"
              onClick={() =>
                show({
                  kind: "switch",
                  plate: saved ?? workspace.plates[0] ?? freshPlate(),
                })
              }
            >
              Reload saved plate
            </button>
          </div>
        )}
        {error && <ErrorState error={new Error(error)} />}
        <div className="plate-content">
          <section className="panel plate-canvas">
            <div className="plate-view-controls">
              <label className="field">
                View
                <select
                  aria-label="Plate view"
                  value={plate.view.mode}
                  onChange={(e) =>
                    view({
                      mode: e.target.value as PlateDefinition["view"]["mode"],
                    })
                  }
                >
                  <option value="heatmap">Heatmap</option>
                  <option value="split">Split: two measurements</option>
                  <option value="categories">Keyword colors</option>
                  <option value="faces">Faces: up to ten measurements</option>
                </select>
              </label>
              {plate.view.mode !== "categories" &&
                plate.view.mode !== "faces" && (
                  <label className="field">
                    Primary measure
                    <select
                      aria-label="Plate primary measure"
                      value={plate.view.primary ?? plate.columns[0].id}
                      onChange={(e) => view({ primary: e.target.value })}
                    >
                      {plate.columns.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {numericColumn(c) ? "" : " (text)"}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              {plate.view.mode === "split" && (
                <label className="field">
                  Secondary measure
                  <select
                    aria-label="Plate secondary measure"
                    value={
                      plate.view.secondary ??
                      plate.columns[Math.min(1, plate.columns.length - 1)].id
                    }
                    onChange={(e) => view({ secondary: e.target.value })}
                  >
                    {numeric.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {plate.view.mode === "categories" && (
                <>
                  <label className="field">
                    Keyword source
                    <select
                      aria-label="Plate color keyword source"
                      value={plate.view.category_source}
                      onChange={(e) =>
                        view({
                          category_source: e.target.value as
                            "tags" | "metadata",
                        })
                      }
                    >
                      <option value="tags">Applied sample annotations</option>
                      <option value="metadata">Acquisition metadata</option>
                    </select>
                  </label>
                  <label className="field">
                    Keyword
                    <input
                      aria-label="Plate color keyword"
                      value={plate.view.category_keyword}
                      list="plate-color-keywords"
                      onChange={(e) =>
                        view({ category_keyword: e.target.value })
                      }
                    />
                  </label>
                  <datalist id="plate-color-keywords">
                    {Array.from(
                      new Set(
                        workspace.samples.flatMap((s) =>
                          Object.keys(s[plate.view.category_source]),
                        ),
                      ),
                    ).map((k) => (
                      <option key={k} value={k} />
                    ))}
                  </datalist>
                </>
              )}
              <label className="field">
                Replicate aggregate
                <select
                  aria-label="Plate replicate aggregate"
                  value={plate.aggregate}
                  onChange={(e) =>
                    set(
                      "aggregate",
                      e.target.value as PlateDefinition["aggregate"],
                    )
                  }
                >
                  <option value="median">Median</option>
                  <option value="mean">Mean</option>
                  <option value="sum">Sum</option>
                </select>
              </label>
            </div>
            <div className="plate-selection-bar">
              <span>
                <strong>{selected.length}</strong> wells selected
              </span>
              <div className="button-group">
                <button
                  className="button small"
                  onClick={() => setSelected(wellNames)}
                >
                  Select all wells
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    setSelected(
                      wellNames.filter((w) => !!plate.assignments[w]?.length),
                    )
                  }
                >
                  Select acquired wells
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    setSelected(wellNames.filter((w) => !selected.includes(w)))
                  }
                >
                  Invert selection
                </button>
                <button
                  className="button small"
                  onClick={() => setSelected([])}
                >
                  Clear selection
                </button>
              </div>
            </div>
            <div
              className={`plate-grid-scroll ${ready ? "" : "plate-updating"}`}
              aria-busy={!ready}
            >
              <table
                className={`plate-grid plate-grid-${rows * cols >= 1536 ? 1536 : rows * cols >= 384 ? 384 : 96} ${plate.view.mode === "faces" ? "plate-faces" : ""}`}
                ref={grid}
                aria-label={`${rows * cols}-well plate`}
              >
                <caption className="sr-only">
                  {plate.name}. Click a well to inspect it. Hold Control or
                  Command to toggle wells, Shift for a rectangle. Arrow keys
                  move through wells. Row and column buttons select a band.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Well</th>
                    {Array.from({ length: cols }, (_, c) => (
                      <th scope="col" key={c}>
                        <button
                          aria-label={`Select plate column ${c + 1}`}
                          onClick={(e) =>
                            selectBand(
                              Array.from({ length: rows }, (_, r) =>
                                wellName(r, c),
                              ),
                              e,
                            )
                          }
                        >
                          {c + 1}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: rows }, (_, r) => (
                    <tr key={r}>
                      <th scope="row">
                        <button
                          aria-label={`Select plate row ${rowName(r)}`}
                          onClick={(e) =>
                            selectBand(
                              Array.from({ length: cols }, (_, c) =>
                                wellName(r, c),
                              ),
                              e,
                            )
                          }
                        >
                          {rowName(r)}
                        </button>
                      </th>
                      {Array.from({ length: cols }, (_, c) => {
                        const index = r * cols + c,
                          name = wellNames[index],
                          data = wellData.get(name),
                          acquisitions = plate.assignments[name]?.length ?? 0;
                        const issue = data?.status[currentColumn.id],
                          measured = data?.values[currentColumn.id];
                        const color =
                          plate.view.mode === "categories"
                            ? data?.category_color
                            : data?.color;
                        return (
                          <td key={c}>
                            <button
                              className={`plate-well ${acquisitions ? "has-acquisition" : "empty-well"} ${selected.includes(name) ? "selected" : ""}`}
                              data-well={name}
                              aria-label={`Well ${name}, ${acquisitions} acquisitions${ready && measured != null ? `, ${currentColumn.name} ${printValue(measured, currentColumn.decimals)}` : ""}`}
                              aria-pressed={selected.includes(name)}
                              tabIndex={index === focus ? 0 : -1}
                              title={`${name} · ${acquisitions} acquisitions\n${(data?.samples ?? []).join("; ")}\n${currentColumn.name}: ${ready ? printValue(measured, currentColumn.decimals) : "Updating"}${issue ? `\n${issue}` : ""}${data?.clipped[currentColumn.id] ? "\nColor clipped by display bounds" : ""}`}
                              style={{
                                background:
                                  plate.view.mode === "split"
                                    ? `linear-gradient(90deg, ${color ?? "#273344"} 50%, ${data?.secondary_color ?? "#273344"} 50%)`
                                    : (color ?? "#273344"),
                              }}
                              onFocus={() => setFocus(index)}
                              onClick={(e) => selectWell(index, e)}
                              onKeyDown={(e) => move(index, e)}
                            >
                              {plate.view.mode === "faces" &&
                                data?.face_svg && (
                                  <svg
                                    className="plate-face"
                                    viewBox="0 0 40 40"
                                    aria-hidden="true"
                                    dangerouslySetInnerHTML={{
                                      __html: data.face_svg,
                                    }}
                                  />
                                )}
                              <span className="plate-well-id">{name}</span>
                              {rows * cols <= 96 &&
                                plate.view.mode === "heatmap" && (
                                  <span className="plate-well-value">
                                    {ready
                                      ? printValue(
                                          measured,
                                          currentColumn.decimals,
                                        )
                                      : "…"}
                                  </span>
                                )}
                              {!!Object.keys(plate.annotations[name] ?? {})
                                .length && (
                                <span
                                  className="plate-planned-dot"
                                  title="Staged annotation plan"
                                />
                              )}
                              {acquisitions > 1 && (
                                <span
                                  className="plate-replicates"
                                  title={`${acquisitions} acquisitions`}
                                >
                                  {acquisitions}
                                </span>
                              )}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div
              className="plate-grid-status"
              role="status"
              data-ready={ready ? "true" : "false"}
            >
              {ready
                ? `${evaluation!.mapped_wells} acquired wells · ${evaluation!.acquisition_count} acquisitions · full-event statistics · revision ${evaluation!.revision}`
                : query.error
                  ? "Measurements unavailable: review the error below"
                  : "Updating full-event measurements…"}
            </div>
            {query.error && (
              <ErrorState
                error={query.error}
                onRetry={() => void query.refetch()}
              />
            )}
            <div className="plate-legend">
              {plate.view.mode === "categories"
                ? Object.entries(evaluation?.categories ?? {}).map(
                    ([label, color]) => (
                      <label className="plate-category" key={label}>
                        <input
                          aria-label={`Plate category color ${label}`}
                          type="color"
                          value={color}
                          onChange={(e) =>
                            view({
                              category_colors: {
                                ...plate.view.category_colors,
                                [label]: e.target.value,
                              },
                            })
                          }
                        />
                        {label}
                      </label>
                    ),
                  )
                : (plate.view.mode === "faces"
                    ? evaluation?.face_features.map((f) => ({
                        id: f.column_id,
                        label: `${f.feature}: ${f.name}`,
                      }))
                    : [
                        evaluation?.primary,
                        ...(plate.view.mode === "split"
                          ? [evaluation?.secondary]
                          : []),
                      ]
                        .filter(Boolean)
                        .map((identifier) => ({
                          id: identifier!,
                          label:
                            plate.columns.find((c) => c.id === identifier)
                              ?.name ?? "Measure",
                        }))
                  )?.map((f) => (
                    <div className="plate-numeric-legend" key={f.id}>
                      <span>{f.label}</span>
                      <i />
                      <span>
                        {printDomain(evaluation?.domains[f.id]?.display_min)} →{" "}
                        {printDomain(evaluation?.domains[f.id]?.display_max)}
                      </span>
                    </div>
                  ))}
              <p className="form-note">
                Gray: undefined or unassigned. A colored zero is a measured
                value. A corner dot marks a staged annotation.{" "}
                {plate.view.mode === "faces" &&
                  "Face features follow numeric measurement order; a yellow dot marks missing features."}{" "}
                Click, Ctrl/⌘ click, or Shift click to select wells.
              </p>
            </div>
            <details className="plate-display-options">
              <summary>Measurement scaling and replicate policy</summary>
              <div className="field-grid">
                <label className="field">
                  Missing replicates
                  <select
                    aria-label="Plate missing replicates"
                    value={plate.missing_replicates}
                    onChange={(e) =>
                      set(
                        "missing_replicates",
                        e.target.value as PlateDefinition["missing_replicates"],
                      )
                    }
                  >
                    <option value="strict">Require every acquisition</option>
                    <option value="available">
                      Use available acquisitions, report missing
                    </option>
                  </select>
                </label>
                <label className="checkbox">
                  <input
                    aria-label="Compensated plate statistics"
                    type="checkbox"
                    checked={plate.compensated}
                    onChange={(e) => set("compensated", e.target.checked)}
                  />
                  Compensated intensities
                </label>
              </div>
              <div className="field-grid">
                <label className="field">
                  Measure bounds
                  <select
                    aria-label="Plate bounds measure"
                    value={
                      plate.columns.some((c) => c.id === bounds.column)
                        ? bounds.column
                        : plate.columns[0].id
                    }
                    onChange={(e) => {
                      const domain = plate.view.domains[e.target.value];
                      setBounds({
                        column: e.target.value,
                        lo: domain ? String(domain[0]) : "",
                        hi: domain ? String(domain[1]) : "",
                      });
                    }}
                  >
                    {numeric.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  Minimum
                  <input
                    aria-label="Plate display minimum"
                    type="number"
                    value={bounds.lo}
                    onChange={(e) =>
                      setBounds({ ...bounds, lo: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  Maximum
                  <input
                    aria-label="Plate display maximum"
                    type="number"
                    value={bounds.hi}
                    onChange={(e) =>
                      setBounds({ ...bounds, hi: e.target.value })
                    }
                  />
                </label>
              </div>
              <div className="button-group">
                <button
                  className="button small"
                  onClick={() => {
                    const lo = Number(bounds.lo),
                      hi = Number(bounds.hi);
                    if (
                      !bounds.lo ||
                      !bounds.hi ||
                      !Number.isFinite(lo) ||
                      !Number.isFinite(hi) ||
                      lo >= hi
                    )
                      setError("Display bounds must be finite and increasing.");
                    else
                      view({
                        domains: {
                          ...plate.view.domains,
                          [bounds.column]: [lo, hi],
                        },
                      });
                  }}
                >
                  Apply display bounds
                </button>
                <button
                  className="button small"
                  onClick={() => {
                    const domains = { ...plate.view.domains };
                    delete domains[bounds.column];
                    view({ domains });
                    setBounds({ ...bounds, lo: "", hi: "" });
                  }}
                >
                  Use automatic bounds
                </button>
              </div>
              <p className="form-note">
                Display bounds clip color only. Values, populations and exports
                preserve the underlying statistics. Replicate aggregates use
                acquisition statistics with equal weights, without pooling their
                events.
              </p>
            </details>
            {(evaluation?.notices ?? []).map((notice, i) => (
              <p className="plate-notice" key={i}>
                {notice}
              </p>
            ))}
            <div className="plate-export">
              <label className="field">
                Export
                <select
                  aria-label="Plate export format"
                  value={exportFormat}
                  onChange={(e) => setExportFormat(e.target.value)}
                >
                  <option value="svg">Plate figure (SVG)</option>
                  <option value="csv">Live measurements (CSV)</option>
                  <option value="json">Complete live report (JSON)</option>
                  <option value="annotations">
                    Staged annotation plan (CSV)
                  </option>
                  <option value="template">
                    Reusable plate template (JSON)
                  </option>
                </select>
              </label>
              <button
                className="button"
                disabled={
                  !ready && !["annotations", "template"].includes(exportFormat)
                }
                onClick={() =>
                  void act(() =>
                    download(
                      endpoint(`/export/${exportFormat}`),
                      exportFormat === "annotations"
                        ? "plate-annotations.csv"
                        : exportFormat === "template"
                          ? "plate-template.json"
                          : `plate.${exportFormat}`,
                      { method: "POST", body: JSON.stringify(body()) },
                    ),
                  )
                }
              >
                <Download size={15} />
                Export plate
              </button>
            </div>
          </section>
          <aside className="panel plate-inspector">
            <div
              className="plate-tool-tabs"
              role="tablist"
              aria-label="Plate tools"
            >
              {(
                [
                  ["well", "Well & annotations"],
                  ["measurements", "Measurements"],
                  ["import", "Import & titration"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  role="tab"
                  id={`plate-tab-${key}`}
                  aria-controls={`plate-tools-${key}`}
                  aria-selected={tool === key}
                  onClick={() => setTool(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div
              className="plate-tool-body"
              role="tabpanel"
              id={`plate-tools-${tool}`}
              aria-labelledby={`plate-tab-${tool}`}
            >
              {tool === "well" && (
                <>
                  <h3>
                    {firstWell}{" "}
                    <span className="muted">
                      {selected.length > 1
                        ? `· ${selected.length} wells selected`
                        : "· selected well"}
                    </span>
                  </h3>
                  <div className="plate-well-measures">
                    {plate.columns.map((c) => (
                      <div key={c.id}>
                        <span>{c.name}</span>
                        <strong>
                          {ready
                            ? printValue(inspected?.values[c.id], c.decimals)
                            : "…"}
                        </strong>
                        {inspected?.status[c.id] && (
                          <small>{inspected.status[c.id]}</small>
                        )}
                      </div>
                    ))}
                  </div>
                  <h4>Assigned acquisitions</h4>
                  {(plate.assignments[firstWell] ?? []).map((sid) => {
                    const acquisition = workspace.samples.find(
                      (s) => s.id === sid,
                    );
                    return (
                      <div className="plate-acquisition-row" key={sid}>
                        <button
                          className="plate-acquisition-link"
                          disabled={!acquisition || busy || working}
                          aria-label={`Open ${acquisition?.name ?? "missing acquisition"} in analysis`}
                          onClick={() => onOpen(sid)}
                        >
                          {acquisition?.name ?? "Missing acquisition"} ↗
                        </button>
                        {window.cytoforgeDesktop && (
                          <button
                            className="button small"
                            disabled={!acquisition || busy || working}
                            aria-label={`Open ${acquisition?.name ?? "missing acquisition"} in new plot window`}
                            onClick={() =>
                              void act(async () => {
                                await window.cytoforgeDesktop!.openPlotWindow(
                                  plateAcquisitionPlot(
                                    workspace,
                                    plate,
                                    firstWell,
                                    sid,
                                  ),
                                );
                              })
                            }
                          >
                            New plot window
                          </button>
                        )}
                      </div>
                    );
                  })}
                  <details>
                    <summary>Assign acquisitions to {firstWell}</summary>
                    <p className="form-note">
                      Each acquisition occupies one well in this plate.
                      Assignment changes are previewed before staging.
                    </p>
                    <input
                      aria-label="Find plate acquisition"
                      placeholder="Find acquisition"
                      value={sampleSearch}
                      onChange={(e) => setSampleSearch(e.target.value)}
                    />
                    <div className="plate-acquisition-picker">
                      {workspace.samples
                        .filter((s) =>
                          s.name
                            .toLocaleLowerCase()
                            .includes(sampleSearch.toLocaleLowerCase()),
                        )
                        .map((s) => {
                          const occupied = Object.entries(
                            plate.assignments,
                          ).find(([, ids]) => ids.includes(s.id))?.[0];
                          return (
                            <label className="checkbox" key={s.id}>
                              <input
                                aria-label={`Assign plate acquisition ${s.name}`}
                                type="checkbox"
                                checked={assignment.includes(s.id)}
                                disabled={
                                  assignment.length >= 64 &&
                                  !assignment.includes(s.id)
                                }
                                onChange={(e) =>
                                  setAssignment(
                                    e.target.checked
                                      ? [...assignment, s.id]
                                      : assignment.filter((v) => v !== s.id),
                                  )
                                }
                              />
                              {s.name}
                              {occupied ? ` · ${occupied}` : ""}
                            </label>
                          );
                        })}
                    </div>
                    <button
                      className="button small"
                      disabled={selected.length !== 1}
                      onClick={() => {
                        const assignments = Object.fromEntries(
                          Object.entries(plate.assignments)
                            .map(([well, ids]) => [
                              well,
                              well === firstWell
                                ? []
                                : ids.filter(
                                    (sid) => !assignment.includes(sid),
                                  ),
                            ])
                            .filter(([, ids]) => (ids as string[]).length),
                        ) as Record<string, string[]>;
                        if (assignment.length)
                          assignments[firstWell] = assignment;
                        show({
                          kind: "stage",
                          title: "Review acquisition assignment",
                          plate: { ...plate, assignments },
                          original: draftJSON,
                          issues: [],
                          lines: [
                            `${assignment.length} acquisitions will occupy ${firstWell}.`,
                            ...assignment.map(
                              (sid) =>
                                `${workspace.samples.find((s) => s.id === sid)?.name}: ${Object.entries(plate.assignments).find(([, ids]) => ids.includes(sid))?.[0] ?? "unassigned"} → ${firstWell}`,
                            ),
                          ],
                        });
                      }}
                    >
                      Preview assignment
                    </button>
                  </details>
                  <h4>Stage annotations for selected wells</h4>
                  <label className="field">
                    Keyword
                    <input
                      aria-label="Plate annotation keyword"
                      value={keyword}
                      maxLength={160}
                      onChange={(e) => setKeyword(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    Value
                    <textarea
                      aria-label="Plate annotation value"
                      value={value}
                      rows={2}
                      maxLength={2048}
                      disabled={clearValue}
                      onChange={(e) => setValue(e.target.value)}
                    />
                  </label>
                  <label className="checkbox">
                    <input
                      aria-label="Clear applied plate keyword"
                      type="checkbox"
                      checked={clearValue}
                      onChange={(e) => setClearValue(e.target.checked)}
                    />
                    Plan to clear this keyword
                  </label>
                  <div className="button-group">
                    <button
                      className="button"
                      disabled={!selected.length || !keyword.trim()}
                      onClick={stageKeyword}
                    >
                      Stage annotation
                    </button>
                    <button
                      className="button small"
                      disabled={!selected.length}
                      onClick={() => {
                        const annotations = { ...plate.annotations };
                        for (const well of selected) {
                          const values = { ...annotations[well] };
                          delete values[keyword];
                          if (Object.keys(values).length)
                            annotations[well] = values;
                          else delete annotations[well];
                        }
                        change({ ...plate, annotations });
                      }}
                    >
                      Remove planned keyword
                    </button>
                  </div>
                  <h4>Staged plan · {firstWell}</h4>
                  {Object.entries(plate.annotations[firstWell] ?? {}).length ? (
                    <dl className="plate-keyword-list">
                      {Object.entries(plate.annotations[firstWell]).map(
                        ([key, value]) => (
                          <div key={key}>
                            <dt>{key}</dt>
                            <dd>
                              {value === null
                                ? "Clear keyword"
                                : value === ""
                                  ? "Empty text"
                                  : value}
                            </dd>
                          </div>
                        ),
                      )}
                    </dl>
                  ) : (
                    <p className="muted small">
                      No annotations are staged for this well.
                    </p>
                  )}
                  <details>
                    <summary>Applied keywords and acquisition metadata</summary>
                    {(plate.assignments[firstWell] ?? []).map((sid) => {
                      const sample = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      return (
                        sample && (
                          <div key={sid}>
                            <h4>{sample.name} · applied annotations</h4>
                            <dl className="plate-keyword-list">
                              {Object.entries(sample.tags).map(([k, v]) => (
                                <div key={k}>
                                  <dt>{k}</dt>
                                  <dd>{v}</dd>
                                </div>
                              ))}
                            </dl>
                            <details>
                              <summary>Immutable acquisition metadata</summary>
                              <dl className="plate-keyword-list">
                                {Object.entries(sample.metadata).map(
                                  ([k, v]) => (
                                    <div key={k}>
                                      <dt>{k}</dt>
                                      <dd>{v}</dd>
                                    </div>
                                  ),
                                )}
                              </dl>
                            </details>
                          </div>
                        )
                      );
                    })}
                  </details>
                  <h4>Apply the reviewed plan</h4>
                  <label className="checkbox">
                    <input
                      aria-label="Apply all staged plate wells"
                      type="checkbox"
                      checked={applyAll}
                      onChange={(e) => setApplyAll(e.target.checked)}
                    />
                    All planned wells ({Object.keys(plate.annotations).length})
                  </label>
                  <label className="field">
                    Existing keywords
                    <select
                      aria-label="Plate annotation apply mode"
                      value={applyMode}
                      onChange={(e) => setApplyMode(e.target.value)}
                    >
                      <option value="replace">Replace matching keys</option>
                      <option value="fill_missing">
                        Fill missing / empty keys only
                      </option>
                    </select>
                  </label>
                  <label className="checkbox">
                    <input
                      aria-label="Apply plate identity keywords"
                      type="checkbox"
                      checked={identity}
                      onChange={(e) => setIdentity(e.target.checked)}
                    />
                    Include WELL ID and PLATE ID annotations
                  </label>
                  <button
                    className="button primary"
                    disabled={conflict || (!applyAll && !selected.length)}
                    onClick={() => void act(reviewAnnotations)}
                  >
                    Review annotation changes
                  </button>
                  <p className="form-note">
                    Stage and save preserve the plan. Applying writes only the
                    keys shown in the preview to assigned acquisitions.
                  </p>
                  <h4>Group selected wells</h4>
                  <label className="field">
                    Group name
                    <input
                      aria-label="Plate selection group name"
                      value={groupName}
                      maxLength={160}
                      onChange={(e) => setGroupName(e.target.value)}
                    />
                  </label>
                  <button
                    className="button"
                    disabled={
                      conflict ||
                      !selected.some((w) => plate.assignments[w]?.length) ||
                      !groupName.trim()
                    }
                    onClick={() =>
                      void act(async () => {
                        const document = await commit(
                          "/plates/group",
                          {
                            plate,
                            wells: selected,
                            name: groupName,
                            base_revision: session.baseRevision,
                          },
                          "Created group from selected wells",
                        );
                        load(
                          document.plates.find((p) => p.id === plate.id)!,
                          document,
                        );
                      })
                    }
                  >
                    Create group from selected wells
                  </button>
                </>
              )}
              {tool === "measurements" && (
                <PlateMeasurements
                  workspace={workspace}
                  plate={plate}
                  selected={column}
                  select={setColumn}
                  change={change}
                />
              )}
              {tool === "import" && (
                <>
                  <h3>Read plate / well keywords</h3>
                  <p className="form-note">
                    Preview matched acquisitions. Contradictory keywords,
                    invalid wells and duplicate mappings are reported.
                  </p>
                  <label className="field">
                    Sample group
                    <select
                      aria-label="Plate mapping sample group"
                      value={mapping.group_id}
                      onChange={(e) =>
                        setMapping({ ...mapping, group_id: e.target.value })
                      }
                    >
                      <option value="">All acquisitions</option>
                      {workspace.groups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="field-grid">
                    <label className="field">
                      Well keyword
                      <input
                        aria-label="Plate mapping well keyword"
                        value={mapping.well_key}
                        onChange={(e) =>
                          setMapping({ ...mapping, well_key: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Plate keyword
                      <input
                        aria-label="Plate mapping plate keyword"
                        value={mapping.plate_key}
                        onChange={(e) =>
                          setMapping({ ...mapping, plate_key: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <div className="field-grid">
                    <label className="field">
                      Source
                      <select
                        aria-label="Plate mapping keyword source"
                        value={mapping.source}
                        onChange={(e) =>
                          setMapping({ ...mapping, source: e.target.value })
                        }
                      >
                        <option value="auto">
                          Annotations, then acquisition metadata
                        </option>
                        <option value="tags">Applied annotations only</option>
                        <option value="metadata">
                          Acquisition metadata only
                        </option>
                      </select>
                    </label>
                    <label className="field">
                      Format
                      <select
                        aria-label="Plate mapping format"
                        value={mapping.format}
                        onChange={(e) =>
                          setMapping({ ...mapping, format: e.target.value })
                        }
                      >
                        <option value="">Infer 96, 384 or 1536 wells</option>
                        {[6, 12, 24, 48, 96, 384, 1536].map((f) => (
                          <option key={f} value={f}>
                            {f} wells
                          </option>
                        ))}
                        <option value="custom">Custom dimensions</option>
                      </select>
                    </label>
                    {mapping.format === "custom" && (
                      <>
                        <label className="field">
                          Mapping rows
                          <input
                            aria-label="Plate mapping rows"
                            type="number"
                            min={1}
                            max={96}
                            step={1}
                            value={mappingRows}
                            onChange={(e) =>
                              setMappingRows(Number(e.target.value))
                            }
                          />
                        </label>
                        <label className="field">
                          Mapping columns
                          <input
                            aria-label="Plate mapping columns"
                            type="number"
                            min={1}
                            max={96}
                            step={1}
                            value={mappingColumns}
                            onChange={(e) =>
                              setMappingColumns(Number(e.target.value))
                            }
                          />
                        </label>
                      </>
                    )}
                  </div>
                  <label className="checkbox">
                    <input
                      aria-label="Include plate mapping replicates"
                      type="checkbox"
                      checked={mapping.include_replicates}
                      onChange={(e) =>
                        setMapping({
                          ...mapping,
                          include_replicates: e.target.checked,
                        })
                      }
                    />
                    Include multiple acquisitions in the same well
                  </label>
                  <button
                    className="button"
                    onClick={() =>
                      void act(async () => {
                        const result = await post<
                          Omit<
                            Extract<Review, { kind: "discover" }>,
                            "kind" | "original"
                          >
                        >(endpoint("/discover"), {
                          ...mapping,
                          revision: workspace.revision,
                          group_id: mapping.group_id || null,
                          format: mapping.format
                            ? mapping.format === "custom"
                              ? "custom"
                              : Number(mapping.format)
                            : null,
                          ...(mapping.format === "custom"
                            ? {
                                geometry: {
                                  rows: mappingRows,
                                  columns: mappingColumns,
                                },
                              }
                            : {}),
                        });
                        show({
                          kind: "discover",
                          ...result,
                          original: draftJSON,
                        });
                      })
                    }
                  >
                    Preview keyword mapping
                  </button>
                  <h3>Import CSV annotation plan</h3>
                  <p className="form-note">
                    Quoted CSV supports empty wells and multiple plates. Blank
                    cells are skipped unless clearing is enabled.
                  </p>
                  <div className="field-grid">
                    <label className="field">
                      Well column
                      <input
                        aria-label="Plate CSV well column"
                        value={wellColumn}
                        onChange={(e) => setWellColumn(e.target.value)}
                      />
                    </label>
                    <label className="field">
                      Plate column (optional)
                      <input
                        aria-label="Plate CSV plate column"
                        value={plateColumn}
                        onChange={(e) => setPlateColumn(e.target.value)}
                      />
                    </label>
                  </div>
                  <label className="checkbox">
                    <input
                      aria-label="Clear blank plate CSV values"
                      type="checkbox"
                      checked={clearBlanks}
                      onChange={(e) => setClearBlanks(e.target.checked)}
                    />
                    Blank cells plan keyword removal
                  </label>
                  <label className="button plate-file">
                    <Upload size={15} />
                    Preview annotation CSV
                    <input
                      aria-label="Plate annotation CSV file"
                      type="file"
                      accept=".csv,text/csv"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file) void act(() => fileImport(file, false));
                      }}
                    />
                  </label>
                  <h3>Dilution / titration series</h3>
                  <div className="field-grid">
                    <label className="field">
                      Keyword
                      <input
                        aria-label="Plate series keyword"
                        value={series.keyword}
                        maxLength={160}
                        onChange={(e) =>
                          setSeries({ ...series, keyword: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Start well
                      <input
                        aria-label="Plate series start well"
                        value={series.start_well}
                        onChange={(e) =>
                          setSeries({ ...series, start_well: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Steps
                      <input
                        aria-label="Plate series steps"
                        type="number"
                        min={1}
                        max={48}
                        value={series.steps}
                        onChange={(e) =>
                          setSeries({
                            ...series,
                            steps: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="field">
                      Replicates
                      <input
                        aria-label="Plate series replicates"
                        type="number"
                        min={1}
                        max={48}
                        value={series.replicates}
                        onChange={(e) =>
                          setSeries({
                            ...series,
                            replicates: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="field">
                      Direction
                      <select
                        aria-label="Plate series direction"
                        value={series.direction}
                        onChange={(e) =>
                          setSeries({ ...series, direction: e.target.value })
                        }
                      >
                        <option value="columns">Across columns</option>
                        <option value="rows">Down rows</option>
                      </select>
                    </label>
                    <label className="field">
                      Operation
                      <select
                        aria-label="Plate series operation"
                        value={series.operation}
                        onChange={(e) =>
                          setSeries({ ...series, operation: e.target.value })
                        }
                      >
                        <option value="multiply">Multiply each step</option>
                        <option value="add">Add each step</option>
                      </select>
                    </label>
                    <label className="field">
                      Starting value
                      <input
                        aria-label="Plate series starting value"
                        type="number"
                        min={0}
                        step="any"
                        value={series.start}
                        onChange={(e) =>
                          setSeries({
                            ...series,
                            start: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="field">
                      {series.operation === "multiply" ? "Factor" : "Increment"}
                      <input
                        aria-label="Plate series factor or increment"
                        type="number"
                        step="any"
                        value={
                          series.operation === "multiply"
                            ? series.factor
                            : series.increment
                        }
                        onChange={(e) =>
                          setSeries({
                            ...series,
                            [series.operation === "multiply"
                              ? "factor"
                              : "increment"]: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="field">
                      Unit
                      <input
                        aria-label="Plate series unit"
                        value={series.unit}
                        maxLength={80}
                        onChange={(e) =>
                          setSeries({ ...series, unit: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Unit keyword
                      <input
                        aria-label="Plate series unit keyword"
                        value={series.unit_keyword}
                        maxLength={160}
                        onChange={(e) =>
                          setSeries({ ...series, unit_keyword: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <button
                    className="button"
                    onClick={() =>
                      void act(async () => {
                        const result = await post<{
                          plate: PlateDefinition;
                          records: Record<string, unknown>[];
                        }>(endpoint("/series"), { ...body(), ...series });
                        show({
                          kind: "stage",
                          title: "Review dilution series",
                          plate: result.plate,
                          original: draftJSON,
                          issues: [],
                          records: result.records,
                          lines: [
                            `${result.records.length} planned well values`,
                            "Concentrations are computed with decimal arithmetic; units use a separate annotation keyword.",
                          ],
                        });
                      })
                    }
                  >
                    Preview dilution series
                  </button>
                  <h3>Reusable plate template</h3>
                  <label className="button plate-file">
                    <Upload size={15} />
                    Preview JSON template
                    <input
                      aria-label="Plate template file"
                      type="file"
                      accept=".json,application/json"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file) void act(() => fileImport(file, true));
                      }}
                    />
                  </label>
                  <p className="form-note">
                    Templates preserve planned annotations, measurement
                    definitions and display settings. Sample and model bindings
                    require review in the destination workspace.
                  </p>
                </>
              )}
            </div>
          </aside>
        </div>
        {saved && (
          <details className="plate-maintenance">
            <summary>Saved plate management</summary>
            <button
              className="button small danger"
              disabled={conflict || dirty}
              onClick={() =>
                void act(async () => {
                  const document = await commit(
                    `/plates/${plate.id}`,
                    {},
                    "Removed plate definition",
                    "DELETE",
                  );
                  load(document.plates[0] ?? freshPlate(), document);
                })
              }
            >
              Remove saved plate definition
            </button>
            <p className="form-note">
              The workspace history can restore removed definitions. Sample data
              and applied annotations stay available. Save or discard an unsaved
              draft before removing its saved definition.
            </p>
          </details>
        )}
      </fieldset>
      {review && (
        <Modal
          title={
            review.kind === "switch"
              ? "Unsaved plate changes"
              : review.kind === "discover"
                ? "Review keyword mapping"
                : review.kind === "apply"
                  ? "Review annotation changes"
                  : review.title
          }
          onClose={() => {
            if (!disabled) setReview(null);
          }}
          wide
        >
          {error && <ErrorState error={new Error(error)} />}
          {review.kind === "switch" ? (
            <>
              <p className="form-note">
                Save or discard the current draft before opening{" "}
                {review.plate.name}.
              </p>
              <div className="button-group">
                <button
                  className="button primary"
                  disabled={disabled || conflict || !ready}
                  onClick={() =>
                    void act(async () => {
                      const document = await save();
                      load(review.plate, document);
                    })
                  }
                >
                  Save and open
                </button>
                <button
                  className="button"
                  disabled={disabled}
                  onClick={() => load(review.plate)}
                >
                  Discard draft and open
                </button>
                <button
                  className="button ghost"
                  disabled={disabled}
                  onClick={() => setReview(null)}
                >
                  Keep editing
                </button>
              </div>
            </>
          ) : (
            <>
              {!reviewCurrent && (
                <p className="plate-notice" role="alert">
                  The plate draft changed after this preview. Close it and
                  review again.
                </p>
              )}
              {review.kind === "stage" && (
                <>
                  <div className="form-note">
                    {review.lines.map((line, i) => (
                      <p key={i}>{line}</p>
                    ))}
                  </div>
                  {review.records?.length ? (
                    <div className="plate-review-table">
                      <table className="diagnostic-table">
                        <thead>
                          <tr>
                            <th>Well</th>
                            <th>Planned values</th>
                          </tr>
                        </thead>
                        <tbody>
                          {review.records.map((record, i) => (
                            <tr key={i}>
                              <td>{String(record.well)}</td>
                              <td>
                                {record.values
                                  ? Object.entries(
                                      record.values as Record<string, unknown>,
                                    )
                                      .map(
                                        ([k, v]) =>
                                          `${k}: ${v == null ? "clear" : v}`,
                                      )
                                      .join("; ")
                                  : `${record.value} ${record.unit ?? ""}`}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                </>
              )}
              {review.kind === "discover" && (
                <>
                  <p className="form-note">
                    {review.plates.length} plate definitions ·{" "}
                    {review.assigned_acquisitions} matched acquisitions. Review
                    unresolved mappings before saving.
                  </p>
                  <div className="plate-discovery-list">
                    {review.plates.map((p) => (
                      <div className="plate-discovery-item" key={p.id}>
                        <div>
                          <strong>{p.name}</strong>
                          <p className="muted small">
                            {plateGeometryLabel(p)} ·{" "}
                            {Object.keys(p.assignments).length} acquired wells ·{" "}
                            {Object.values(p.assignments).flat().length}{" "}
                            acquisitions
                          </p>
                        </div>
                        <button
                          className="button small"
                          disabled={
                            disabled ||
                            !reviewCurrent ||
                            (review.issues.length > 0 && !acknowledged)
                          }
                          onClick={() => {
                            const [mappedRows, mappedColumns] =
                              plateDimensions(p);
                            const mappedWells = new Set(
                              Array.from(
                                { length: mappedRows * mappedColumns },
                                (_, i) =>
                                  wellName(
                                    Math.floor(i / mappedColumns),
                                    i % mappedColumns,
                                  ),
                              ),
                            );
                            show({
                              kind: "stage",
                              title: "Review mapped assignments",
                              original: draftJSON,
                              plate: {
                                ...plate,
                                format: p.format,
                                geometry: p.geometry,
                                plate_key: p.plate_key,
                                assignments: p.assignments,
                                annotations: Object.fromEntries(
                                  Object.entries(plate.annotations).filter(
                                    ([w]) => mappedWells.has(w),
                                  ),
                                ),
                              },
                              issues: [],
                              lines: [
                                "Replace this draft's acquisition assignments with the selected mapping.",
                                "Plans outside the mapped plate format will be removed from this draft.",
                              ],
                            });
                          }}
                        >
                          Use mapping in current draft
                        </button>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {review.kind !== "apply" && review.issues.length > 0 && (
                <div className="plate-notice">
                  <strong>{review.issues.length} items require review</strong>
                  <ul>
                    {review.issues.map((issue, i) => (
                      <li key={i}>
                        {issue.plate ? `${issue.plate}: ` : ""}
                        {issue.well ? `${issue.well}: ` : ""}
                        {issue.line ? `CSV line ${issue.line}: ` : ""}
                        {issue.message}
                        {issue.sample_ids?.length
                          ? ` (${issue.sample_ids.length} acquisitions)`
                          : ""}
                      </li>
                    ))}
                  </ul>
                  <label className="checkbox">
                    <input
                      aria-label="I reviewed unresolved plate items"
                      disabled={disabled}
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(e) => setAcknowledged(e.target.checked)}
                    />
                    I reviewed the omitted mappings / rows and template binding
                    warnings.
                  </label>
                </div>
              )}
              {review.kind === "apply" && (
                <>
                  <p className="form-note">
                    {review.data.change_count} keyword changes across{" "}
                    {review.data.sample_count} acquisitions.{" "}
                    {review.data.unchanged} values stay as they are. Empty
                    planned wells:{" "}
                    {review.data.empty_wells.join(", ") || "none"}.
                  </p>
                  <div className="plate-review-table">
                    <table className="diagnostic-table">
                      <thead>
                        <tr>
                          <th>Well / acquisition</th>
                          <th>Keyword</th>
                          <th>Before</th>
                          <th>After</th>
                        </tr>
                      </thead>
                      <tbody>
                        {review.data.changes.map((c, i) => (
                          <tr key={i}>
                            <td>
                              {c.well} · {c.sample}
                            </td>
                            <td>{c.key}</td>
                            <td>{c.before ?? "Missing"}</td>
                            <td>
                              {c.after === null
                                ? "Clear keyword"
                                : c.after === ""
                                  ? "Empty text"
                                  : c.after}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="form-note">
                    Applying saves this plate plan and writes the displayed
                    sample annotation changes in one workspace transaction.
                  </p>
                </>
              )}
              <div className="button-group plate-review-actions">
                {review.kind === "stage" && (
                  <button
                    className="button primary"
                    disabled={
                      disabled ||
                      !reviewCurrent ||
                      (review.issues.length > 0 && !acknowledged)
                    }
                    onClick={() => {
                      change(review.plate);
                      setReview(null);
                    }}
                  >
                    Stage reviewed plan
                  </button>
                )}
                {review.kind === "discover" && (
                  <button
                    className="button primary"
                    disabled={
                      disabled ||
                      !reviewCurrent ||
                      !review.plates.length ||
                      (review.issues.length > 0 && !acknowledged)
                    }
                    onClick={() =>
                      void act(async () => {
                        const document = await commit(
                          "/plates/save-batch",
                          {
                            plates: review.plates,
                            base_revision: review.revision,
                          },
                          "Saved discovered plates",
                        );
                        if (
                          !saved &&
                          plate.name === "New plate" &&
                          !Object.keys(plate.assignments).length &&
                          !Object.keys(plate.annotations).length
                        ) {
                          load(
                            document.plates.find(
                              (p) => p.id === review.plates[0].id,
                            )!,
                            document,
                          );
                        } else {
                          setSession((current) => ({
                            ...current,
                            baseRevision: document.revision,
                          }));
                          setReview(null);
                        }
                      })
                    }
                  >
                    Save discovered plates
                  </button>
                )}
                {review.kind === "apply" && (
                  <button
                    className="button primary"
                    disabled={
                      disabled ||
                      conflict ||
                      !reviewCurrent ||
                      review.data.revision !== workspace.revision
                    }
                    onClick={() =>
                      void act(async () => {
                        const document = await commit(
                          "/plates/annotations/apply",
                          {
                            ...review.body,
                            base_revision: review.data.revision,
                            review_hash: review.data.review_hash,
                          },
                          "Applied reviewed plate annotations",
                        );
                        load(
                          document.plates.find((p) => p.id === plate.id)!,
                          document,
                        );
                      })
                    }
                  >
                    Apply reviewed annotations
                  </button>
                )}
                <button
                  className="button ghost"
                  disabled={disabled}
                  onClick={() => setReview(null)}
                >
                  Keep editing
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
