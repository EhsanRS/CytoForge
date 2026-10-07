import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { graphTextCSS } from "./graphTypography";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleHelp,
  Download,
  Dna,
  FilePlus2,
  FileText,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Grid2X2,
  Hand,
  Hexagon,
  Layers3,
  LoaderCircle,
  Maximize2,
  Magnet,
  MousePointer2,
  Network,
  PanelRightClose,
  PanelRightOpen,
  PenTool,
  Plus,
  Redo2,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  Table2,
  Undo2,
  Upload,
  ExternalLink,
  Edit3,
  X,
} from "lucide-react";
import { api, ApiError, download, params, post } from "./api";
import { GraphControls } from "./components/GraphControls";
import { AxisDefinitionDialog } from "./components/AxisDefinitionDialog";
import { EventExportDialog } from "./components/EventExportDialog";
import { PopulationSnapshotDialog } from "./components/PopulationSnapshotDialog";
import { ChannelAliasesDialog } from "./components/ChannelAliasesDialog";
import {
  PooledGateReview,
  type PooledGateProposal,
} from "./components/PooledGateReview";
import {
  channelForAxis,
  curlyAxisEligible,
  dimensionForAxis,
  virtualDimension,
} from "./coordinates";
import {
  ConcatenateDialog,
  ConcatenationOrigins,
} from "./components/ConcatenateDialog";
import { linkedPartition } from "./gatePartitions";
import type {
  Gate,
  AnalysisResult,
  GateCount,
  GateDrawingKind,
  GateDimension,
  History,
  PlotDefinition,
  Sample,
  Workspace,
  WorkspaceSummary,
  ImportResult,
  ImportSession,
  DesktopPlotWindow,
  DesktopPlotState,
  GraphOptions,
  ThreeDView,
  Transform,
  PlotNavigationAction,
  Channel,
  PooledScope,
  PooledGroup,
} from "./types";
import {
  channelLabel,
  formatNumber,
  id,
  makeGate,
  percentage,
  linear,
} from "./types";
import {
  Empty,
  ErrorState,
  Loading,
  Modal,
  Tag,
  Toast,
} from "./components/Common";
import {
  ApplyDialog,
  CompensationPanel,
  GateDialog,
  GroupDialog,
  SampleDialog,
  TransformDialog,
  type Commit,
} from "./components/Editors";
import {
  GateInspector,
  SamplesPanel,
  StatisticsPanel,
} from "./components/Panels";
import { LayoutPanel } from "./components/LayoutStudio";
import { Plot, type Tool } from "./components/Plot";
import {
  InlineGateEditor,
  canEditGateOnPlot,
} from "./components/InlineGateEditor";
import { DerivedDialog } from "./components/DerivedDialog";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { InterchangeDialog } from "./components/InterchangeDialog";
import { QualityPanel } from "./components/QualityPanel";
import { CellCyclePanel } from "./components/CellCyclePanel";
import { ProliferationPanel } from "./components/ProliferationPanel";
import { KineticsPanel } from "./components/KineticsPanel";
import { PopulationComparisonPanel } from "./components/PopulationComparisonPanel";
import { PlatePanel } from "./components/PlatePanel";
import { ImportProgress } from "./components/ImportProgress";

type Tab =
  | "analysis"
  | "discovery"
  | "quality"
  | "biology"
  | "proliferation"
  | "kinetics"
  | "comparison"
  | "samples"
  | "statistics"
  | "plates"
  | "compensation"
  | "layouts";
type ModalKind =
  | "new"
  | "group"
  | "apply"
  | "sample"
  | "remove-sample"
  | "remove-gate"
  | "capture-population"
  | "history"
  | "help"
  | "import-result"
  | "transform"
  | "derived"
  | "interchange"
  | "concatenate"
  | "origins"
  | "event-export"
  | "channel-aliases"
  | null;
const navItems = [
  { key: "analysis" as Tab, label: "Analysis", icon: Activity },
  { key: "discovery" as Tab, label: "Discovery", icon: Network },
  { key: "quality" as Tab, label: "Acquisition QC", icon: ShieldCheck },
  { key: "biology" as Tab, label: "Cell cycle", icon: Dna },
  { key: "proliferation" as Tab, label: "Proliferation", icon: GitBranch },
  { key: "kinetics" as Tab, label: "Kinetics", icon: Activity },
  { key: "comparison" as Tab, label: "Population comparison", icon: Layers3 },
  { key: "samples" as Tab, label: "Samples", icon: Layers3 },
  { key: "statistics" as Tab, label: "Statistics", icon: Table2 },
  { key: "plates" as Tab, label: "Plates", icon: Grid2X2 },
  { key: "compensation" as Tab, label: "Compensation", icon: Grid2X2 },
  { key: "layouts" as Tab, label: "Layout studio", icon: FileText },
];
const tools: {
  key: Tool;
  name: string;
  shortcut?: string;
  icon: typeof Square;
}[] = [
  { key: "inspect", name: "Select", shortcut: "V", icon: MousePointer2 },
  { key: "rectangle", name: "Rectangle gate", shortcut: "R", icon: Square },
  { key: "polygon", name: "Polygon gate", shortcut: "P", icon: Hexagon },
  { key: "freehand", name: "Freehand gate", shortcut: "F", icon: PenTool },
  {
    key: "autogate",
    name: "Automatic density gate",
    shortcut: "A",
    icon: Hexagon,
  },
  { key: "ellipse", name: "Ellipse gate", shortcut: "E", icon: Circle },
  { key: "range", name: "Range gate", shortcut: "G", icon: Braces },
  { key: "bisector", name: "Bisector gates", shortcut: "B", icon: Braces },
  { key: "quadrant", name: "Quadrant gates", shortcut: "Q", icon: Grid2X2 },
  { key: "spider", name: "Spider gates", shortcut: "S", icon: Network },
  { key: "curly", name: "Curly quadrants", shortcut: "C", icon: Grid2X2 },
  { key: "zoom", name: "Zoom into region", shortcut: "Z", icon: Maximize2 },
  { key: "pan", name: "Pan", shortcut: "H", icon: Hand },
];
function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true">
      <path
        d="M12 38 25 13h17L29 38Zm10 13 13-25h17L39 51Z"
        fill="currentColor"
      />
    </svg>
  );
}

type DrawingSource = {
  sample: Sample;
  channels: Channel[];
  coordinateGate?: Gate;
};

export default function App({
  plotWindow = null,
}: {
  plotWindow?: DesktopPlotWindow | null;
}) {
  const detached = !!plotWindow;
  const [pinnedView, setPinnedView] = useState(detached);
  const [plotDraft, setPlotDraft] = useState(false);
  const [drawingReset, setDrawingReset] = useState(0);
  const latestDrawingSource = useRef<DrawingSource | null>(null);
  const [drawingSource, setDrawingSource] = useState<DrawingSource | null>(
    null,
  );
  const plotDraftRef = useRef(false),
    gateDraftRef = useRef(false);
  const inlineEditRef = useRef(false);
  const [inlineGateEdit, setInlineGateEdit] = useState(false);
  const updatePlotDraft = useCallback((value: boolean) => {
    if (inlineEditRef.current && !value) return;
    if (value && !plotDraftRef.current) {
      setDrawingSource(
        latestDrawingSource.current
          ? structuredClone(latestDrawingSource.current)
          : null,
      );
      setPinnedView(true);
    } else if (!value) setDrawingSource(null);
    plotDraftRef.current = value;
    setPlotDraft(value);
    window.cytoforgeDesktop?.setGateDraftDirty(value || gateDraftRef.current);
  }, []);
  const localViewMemory = useRef(new Map<string, DesktopPlotState>());
  const cache = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(() => {
    if (plotWindow) return plotWindow.workspaceId;
    const saved = localStorage.getItem("cytoforge.workspace");
    return saved && /^[a-f0-9]{32}$/.test(saved) ? saved : null;
  });
  const [storageReady, setStorageReady] = useState(
    detached || !window.cytoforgeDesktop,
  );
  const [tab, setTabState] = useState<Tab>("analysis"),
    [groupId, setGroupId] = useState<string | null>(
      plotWindow?.groupId ?? null,
    );
  const [reportPrintRequest, setReportPrintRequest] = useState(0);
  const [pooledView, setPooledView] = useState(plotWindow?.pooled ?? false);
  const [pooledGateProposal, setPooledGateProposal] =
    useState<PooledGateProposal | null>(null);
  useEffect(() => {
    window.cytoforgeDesktop?.setGateDraftDirty(
      !!pooledGateProposal || gateDraftRef.current || plotDraftRef.current,
    );
  }, [pooledGateProposal]);
  const [pooledAnalysis, setPooledAnalysis] = useState<{
    inputs: AnalysisResult["request"]["inputs"];
    channels: string[];
    key: string;
  } | null>(null);
  const setTab = useCallback((value: Tab) => {
    if (plotDraftRef.current && value !== "analysis") return;
    setTabState(value);
  }, []);
  useEffect(() => {
    const exportReport = () => {
      if (plotDraftRef.current) return;
      setTab("layouts");
      setReportPrintRequest((value) => value + 1);
    };
    window.addEventListener("cytoforge:export-report", exportReport);
    return () =>
      window.removeEventListener("cytoforge:export-report", exportReport);
  }, []);
  const [sampleId, setSampleId] = useState<string | null>(
      plotWindow?.sampleId ?? null,
    ),
    [gateId, setGateId] = useState<string | null>(plotWindow?.gateId ?? null);
  const [coordinateGateId, setCoordinateGateId] = useState<string | null>(
    plotWindow?.coordinateGateId ?? null,
  );
  const [x, setX] = useState(plotWindow?.x ?? "PE-A"),
    [y, setY] = useState(plotWindow ? (plotWindow.y ?? "") : "APC-A"),
    [mode, setMode] = useState<string>(plotWindow?.mode ?? "density");
  const oneDimensional = ["histogram", "cdf"].includes(mode);
  const [xTransform, setXTransform] = useState<Transform | null>(
    plotWindow?.xTransform ?? null,
  );
  const [yTransform, setYTransform] = useState<Transform | null>(
    plotWindow?.yTransform ?? null,
  );
  const [xDimension, setXDimension] = useState<GateDimension | null>(
    plotWindow?.xDimension ?? null,
  );
  const [yDimension, setYDimension] = useState<GateDimension | null>(
    plotWindow?.yDimension ?? null,
  );
  const [axisEditor, setAxisEditor] = useState<number | null>(null);
  const [threeD, setThreeD] = useState<ThreeDView>(
    plotWindow?.threeD ?? { z: "" },
  );
  const [graphOptions, setGraphOptions] = useState<GraphOptions>(
    plotWindow?.graphOptions ?? {},
  );
  const [plotBins, setPlotBins] = useState(plotWindow?.bins ?? 160);
  const [viewBounds, setViewBounds] = useState<{
    signature: string;
    bounds: number[] | null;
  }>({ signature: "", bounds: plotWindow?.bounds ?? null });
  const [tool, setTool] = useState<Tool>("inspect"),
    [search, setSearch] = useState(plotWindow?.sampleFilter ?? ""),
    [selected, setSelected] = useState<string[]>([]);
  useEffect(() => {
    if (
      (mode === "3d" && !["inspect", "pan", "zoom"].includes(tool)) ||
      (tool === "bisector" && !oneDimensional) ||
      (oneDimensional &&
        [
          "rectangle",
          "ellipse",
          "polygon",
          "freehand",
          "autogate",
          "quadrant",
          "spider",
          "curly",
        ].includes(tool))
    )
      setTool("inspect");
  }, [mode, oneDimensional, tool]);
  const [inspector, setInspector] = useState(() => window.innerWidth > 1300),
    [backgate, setBackgate] = useState<string | null>(
      plotWindow?.backgateId ?? null,
    );
  const [modal, setModal] = useState<ModalKind>(null),
    [draftGate, setDraftGateState] = useState<Gate | null>(null),
    [editingGate, setEditingGate] = useState(false);
  const [editSample, setEditSample] = useState<Sample | null>(null),
    [transformChannel, setTransformChannel] = useState("");
  const [newName, setNewName] = useState("Untitled experiment"),
    [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(""),
    [toast, setToast] = useState<{ message: string; error?: boolean } | null>(
      null,
    );
  const importInput = useRef<HTMLInputElement>(null),
    projectInput = useRef<HTMLInputElement>(null),
    searchInput = useRef<HTMLInputElement>(null);
  const mainContent = useRef<HTMLElement>(null);
  const [gateRevision, setGateRevision] = useState<number | null>(null);
  const setDraftGate = (value: Gate | null) => {
    gateDraftRef.current = !!value;
    window.cytoforgeDesktop?.setGateDraftDirty(!!value || plotDraftRef.current);
    setGateRevision(value ? (workspace?.revision ?? null) : null);
    setDraftGateState(value);
  };
  useEffect(() => {
    if (mainContent.current) mainContent.current.scrollTop = 0;
  }, [tab, sampleId, gateId]);
  const operationLock = useRef(false);
  const workspaceList = useQuery({
    queryKey: ["workspaces"],
    queryFn: ({ signal }) => api<WorkspaceSummary[]>("/workspaces", { signal }),
  });
  const workspaceQuery = useQuery({
    queryKey: ["workspace", activeId],
    queryFn: ({ signal }) =>
      api<Workspace>(`/workspaces/${activeId}`, { signal }),
    enabled: !!activeId,
  });
  const workspace = workspaceQuery.data;
  const analysisStates = useQuery({
    queryKey: ["analyses", activeId, workspace?.revision],
    queryFn: ({ signal }) =>
      api<AnalysisResult[]>(`/workspaces/${activeId}/analyses`, { signal }),
    enabled: !!workspace?.analyses.length,
  });
  const cellCycleStates = useQuery({
    queryKey: ["cell-cycle-states", activeId, workspace?.revision],
    queryFn: ({ signal }) =>
      api<{ id: string; request: { name: string }; stale: boolean }[]>(
        `/workspaces/${activeId}/cell-cycle`,
        { signal },
      ),
    enabled: !!workspace?.cell_cycle_results?.length,
  });
  const proliferationStates = useQuery({
    queryKey: ["proliferation-states", activeId, workspace?.revision],
    queryFn: ({ signal }) =>
      api<{ id: string; request: { name: string }; stale: boolean }[]>(
        `/workspaces/${activeId}/proliferation`,
        { signal },
      ),
    enabled: !!workspace?.proliferation_results?.length,
  });
  const kineticsStates = useQuery({
    queryKey: ["kinetics-states", activeId, workspace?.revision],
    queryFn: ({ signal }) =>
      api<{ id: string; request: { name: string }; stale: boolean }[]>(
        `/workspaces/${activeId}/kinetics`,
        { signal },
      ),
    enabled: !!workspace?.kinetics_results?.length,
  });
  const actualSample = workspace?.samples.find((s) => s.id === sampleId);
  const sample =
    actualSample ??
    (plotDraft ? drawingSource?.sample : null) ??
    (!pinnedView ? workspace?.samples[0] : null) ??
    null;
  const gate =
    workspace?.gates.find(
      (g) => g.id === gateId && g.sample_id === sample?.id,
    ) ?? null;
  const gates =
    workspace?.gates.filter((g) => g.sample_id === sample?.id) ?? [];
  const coordinateGate =
    gates.find((g) => g.id === coordinateGateId) ??
    (plotDraft ? drawingSource?.coordinateGate : undefined);
  const pooledScope = useMemo<PooledScope | undefined>(
    () =>
      pooledView && sample
        ? {
            anchor_id: sample.id,
            group_id: groupId,
            sample_filter: search,
            gate_id: gateId,
          }
        : undefined,
    [pooledView, sample?.id, groupId, search, gateId],
  );
  const pooledQuery = useQuery({
    queryKey: [
      "virtual-group",
      activeId,
      workspace?.revision,
      pooledScope?.anchor_id,
      groupId,
      search,
      pooledView,
    ],
    enabled: !!pooledScope && !!workspace,
    queryFn: ({ signal }) =>
      api<PooledGroup>(`/workspaces/${activeId}/virtual-groups/inspect`, {
        method: "POST",
        signal,
        body: JSON.stringify({ ...pooledScope, gate_id: null }),
      }),
  });
  const pooledPopulation = useQuery({
    queryKey: [
      "virtual-population",
      activeId,
      workspace?.revision,
      pooledScope,
    ],
    enabled: !!pooledScope && !!gateId && !!workspace,
    queryFn: ({ signal }) =>
      api<PooledGroup>(`/workspaces/${activeId}/virtual-groups/inspect`, {
        method: "POST",
        signal,
        body: JSON.stringify(pooledScope),
      }),
  });
  const pooledInfo = gateId ? pooledPopulation.data : pooledQuery.data;
  const plotChannels = [...(sample?.channels ?? [])].filter(
    (c) => !pooledView || pooledQuery.data?.common_channels.includes(c.name),
  );
  for (const dim of [
    ...(coordinateGate?.dimensions ?? []),
    xDimension,
    yDimension,
    threeD.z_dimension,
    threeD.color_dimension,
    threeD.size_dimension,
  ]) {
    if (!dim) continue;
    const index = plotChannels.findIndex((c) => c.name === dim.channel);
    const value = {
      name: dim.channel,
      label:
        dim.ratio_channels?.join(" / ") ??
        plotChannels[index]?.label ??
        dim.channel,
      range: 262144,
      transform: dim.transform,
    };
    if (index < 0) plotChannels.push(value);
  }
  if (plotDraft)
    for (const channel of drawingSource?.channels ?? []) {
      if (!plotChannels.some((c) => c.name === channel.name))
        plotChannels.push(channel);
    }
  latestDrawingSource.current = sample
    ? { sample, channels: plotChannels, coordinateGate }
    : null;
  const rawXc = channelForAxis(
      coordinateGate,
      plotChannels.find((c) => c.name === x) ??
        (!pinnedView ? plotChannels[0] : undefined),
      0,
      xDimension,
    ),
    rawYc = channelForAxis(
      coordinateGate,
      plotChannels.find((c) => c.name === y) ??
        (!pinnedView ? plotChannels[1] : undefined),
      1,
      yDimension,
    );
  const xc = rawXc && xTransform ? { ...rawXc, transform: xTransform } : rawXc;
  const yc = rawYc && yTransform ? { ...rawYc, transform: yTransform } : rawYc;
  const curlyEnabled =
    !oneDimensional &&
    mode !== "3d" &&
    curlyAxisEligible(
      xc,
      xDimension ?? dimensionForAxis(coordinateGate, xc?.name, 0),
      sample,
    ) &&
    curlyAxisEligible(
      yc,
      yDimension ?? dimensionForAxis(coordinateGate, yc?.name, 1),
      sample,
    );
  useEffect(() => {
    if (tool === "curly" && !curlyEnabled) setTool("inspect");
  }, [tool, curlyEnabled]);
  const zc = channelForAxis(
    coordinateGate,
    plotChannels.find((c) => c.name === threeD.z) ??
      (!pinnedView ? (plotChannels[2] ?? plotChannels[0]) : undefined),
    2,
    threeD.z_dimension,
  );
  const threeDView = useMemo(
    () =>
      threeD.z === zc?.name || pinnedView
        ? threeD
        : {
            ...threeD,
            z: zc?.name ?? "",
            z_transform: null,
            z_dimension: null,
          },
    [threeD, zc?.name, pinnedView],
  );
  const missingPlotInput =
    pinnedView &&
    workspace &&
    (!actualSample && sampleId
      ? "This sample is unavailable. Undo its removal or select another sample."
      : gateId && !gate
        ? "This population is unavailable. Undo its removal or select another population."
        : coordinateGateId && !coordinateGate
          ? "The gate defining these plot coordinates is unavailable. Select another coordinate basis."
          : backgate && !gates.some((g) => g.id === backgate)
            ? "The backgate population is unavailable. Choose another backgate."
            : !xc || (!oneDimensional && !yc) || (mode === "3d" && !zc)
              ? "A plot parameter is unavailable. Select an available channel."
              : mode === "3d" &&
                  [threeD.color_by, threeD.size_by].some(
                    (name) =>
                      name && !plotChannels.some((c) => c.name === name),
                  )
                ? "A 3D color or size parameter is unavailable. Select an available channel."
                : null);
  const plotSignature = JSON.stringify([
    activeId,
    sample?.id,
    gateId,
    x,
    y,
    mode,
    coordinateGateId,
    xDimension,
    yDimension,
    xc?.transform,
    yc?.transform,
    mode === "3d"
      ? [
          threeDView.z,
          zc?.transform,
          threeD.z_transform,
          threeD.compensation,
          threeD.z_dimension,
        ]
      : null,
  ]);
  const plotBounds =
    !viewBounds.signature || viewBounds.signature === plotSignature
      ? viewBounds.bounds
      : null;
  const setPlotBounds = (bounds: number[] | null) =>
    setViewBounds({ signature: plotSignature, bounds });
  useEffect(() => {
    if (!viewBounds.signature && workspace && !missingPlotInput)
      setViewBounds((value) => ({ ...value, signature: plotSignature }));
  }, [plotSignature, workspace, missingPlotInput, viewBounds.signature]);
  const selectedAnalysisIds =
    sample?.computed_parameters
      .filter(
        (p) =>
          p.name === xc?.name ||
          (!oneDimensional && p.name === yc?.name) ||
          (mode === "3d" &&
            [zc?.name, threeD.color_by, threeD.size_by].includes(p.name)),
      )
      .map((p) => p.analysis_id) ?? [];
  const staleAnalysis = [
    ...(analysisStates.data ?? []),
    ...(cellCycleStates.data ?? []),
    ...(proliferationStates.data ?? []),
    ...(kineticsStates.data ?? []),
  ].find((a) => a.stale && selectedAnalysisIds.includes(a.id));
  const countsQuery = useQuery({
    queryKey: [
      "counts",
      activeId,
      workspace?.revision,
      sample?.id,
      pooledScope,
    ],
    queryFn: ({ signal }) =>
      api<GateCount[]>(
        `/workspaces/${activeId}/samples/${sample!.id}/counts?${params({ pooled: pooledScope ? true : null, group_id: pooledScope?.group_id, sample_filter: pooledScope?.sample_filter })}`,
        {
          signal,
        },
      ),
    enabled: !!sample && !!workspace,
  });
  const historyQuery = useQuery({
    queryKey: ["history", activeId, workspace?.revision],
    queryFn: ({ signal }) =>
      api<History>(`/workspaces/${activeId}/history`, { signal }),
    enabled: !!workspace,
  });
  const selectedGroup = workspace?.groups.find((g) => g.id === groupId);
  const populationPath: Gate[] = [];
  const pathSeen = new Set<string>();
  let pathGate = gate;
  while (pathGate && !pathSeen.has(pathGate.id)) {
    populationPath.unshift(pathGate);
    pathSeen.add(pathGate.id);
    pathGate = gates.find((g) => g.id === pathGate!.parent_id) ?? null;
  }
  const childPopulations = gates.filter((g) => g.parent_id === gateId);
  const siblingPopulations = gate
    ? gates.filter((g) => g.parent_id === gate.parent_id)
    : [];
  const visibleSamples =
    workspace?.samples.filter(
      (s) =>
        (!groupId || !!selectedGroup?.sample_ids.includes(s.id)) &&
        `${s.name} ${Object.values(s.tags).join(" ")}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    ) ?? [];
  const count = countsQuery.data?.find((c) => c.id === gate?.id);
  const totalEvents =
    workspace?.samples.reduce((sum, s) => sum + s.event_count, 0) ?? 0;
  const plotTotalEvents = pooledView
    ? pooledQuery.data?.total_events
    : sample?.event_count;
  const togglePooled = async () => {
    if (!workspace || plotDraft || draftGate || busy || modal) return;
    if (pooledView) {
      setPooledView(false);
      setViewBounds({ signature: "", bounds: null });
      return;
    }
    const anchor =
      visibleSamples.find((s) => s.id === sample?.id) ?? visibleSamples[0];
    if (!anchor) {
      notify("Choose a group containing samples in the current filter.", true);
      return;
    }
    try {
      const selected = anchor.id === sample?.id ? gateId : null;
      const info = await post<PooledGroup>(
        `/workspaces/${workspace.id}/virtual-groups/inspect`,
        {
          anchor_id: anchor.id,
          group_id: groupId,
          sample_filter: search,
          gate_id: selected,
        },
      );
      if (!info.common_channels.length)
        throw new Error("Harmonize the group's parameters before pooling.");
      const xAvailable = xDimension?.ratio_channels
        ? xDimension.ratio_channels.every((c) =>
            info.common_channels.includes(c),
          )
        : info.common_channels.includes(x);
      const yAvailable = yDimension?.ratio_channels
        ? yDimension.ratio_channels.every((c) =>
            info.common_channels.includes(c),
          )
        : info.common_channels.includes(y);
      setSampleId(anchor.id);
      setGateId(selected);
      if (!xAvailable) {
        setX(info.common_channels[0]);
        setXTransform(null);
        setXDimension(null);
      }
      if (!yAvailable) {
        setY(info.common_channels[1] ?? "");
        setYTransform(null);
        setYDimension(null);
      }
      if (!selected) {
        setCoordinateGateId(null);
        setBackgate(null);
      }
      if (info.common_channels.length < 2) setMode("histogram");
      setPooledView(true);
      setPinnedView(true);
      setViewBounds({ signature: "", bounds: null });
      notify(
        `Viewing ${info.group_name} as one population across ${info.sources.length} samples`,
      );
    } catch (error) {
      notify((error as Error).message, true);
    }
  };
  const analyzePooled = async () => {
    if (!workspace || !pooledScope || plotDraft || draftGate) return;
    try {
      const info = await post<PooledGroup>(
        `/workspaces/${workspace.id}/virtual-groups/inspect`,
        pooledScope,
      );
      const channels = info.common_channels.filter(
        (c) => !sample?.computed_parameters.some((p) => p.name === c),
      );
      setPooledAnalysis({
        inputs: info.analysis_inputs!,
        channels,
        key: `${workspace.id}:${workspace.revision}:${Date.now()}`,
      });
      setTab("discovery");
    } catch (error) {
      notify((error as Error).message, true);
    }
  };
  const closeModal = useCallback(() => setModal(null), []);
  const closeGate = useCallback(() => {
    inlineEditRef.current = false;
    setInlineGateEdit(false);
    updatePlotDraft(false);
    setDraftGate(null);
  }, [updatePlotDraft]);
  const startInlineEdit = (value: Gate) => {
    if (
      busy ||
      modal ||
      plotDraftRef.current ||
      gateDraftRef.current ||
      !canEditGateOnPlot(value)
    )
      return;
    setTool("inspect");
    setEditingGate(true);
    setDraftGate(value);
    inlineEditRef.current = true;
    setInlineGateEdit(true);
    updatePlotDraft(true);
  };
  const inlineDetails = (value: Gate) => {
    setDraftGateState(value);
    inlineEditRef.current = false;
    setInlineGateEdit(false);
    updatePlotDraft(false);
  };
  const notify = useCallback(
    (message: string, error = false) => setToast({ message, error }),
    [],
  );
  const desktopView = useMemo<DesktopPlotState | null>(
    () =>
      activeId && sample
        ? {
            workspaceId: activeId,
            sampleId: sample.id,
            gateId,
            x: xc?.name ?? x,
            y: (yc?.name ?? y) || null,
            mode: mode as DesktopPlotState["mode"],
            coordinateGateId,
            backgateId: backgate,
            bounds: plotBounds,
            graphOptions,
            bins: plotBins,
            threeD: threeDView.z ? threeDView : undefined,
            groupId,
            ...(pooledView ? { pooled: true } : {}),
            sampleFilter: search,
            xTransform,
            yTransform,
            ...(xDimension ? { xDimension } : {}),
            ...(yDimension ? { yDimension } : {}),
          }
        : pinnedView && activeId && sampleId
          ? {
              workspaceId: activeId,
              sampleId,
              gateId,
              x,
              y: y || null,
              mode: mode as DesktopPlotState["mode"],
              coordinateGateId,
              backgateId: backgate,
              bounds: plotBounds,
              graphOptions,
              bins: plotBins,
              threeD: threeDView.z ? threeDView : undefined,
              groupId,
              ...(pooledView ? { pooled: true } : {}),
              sampleFilter: search,
              xTransform,
              yTransform,
              ...(xDimension ? { xDimension } : {}),
              ...(yDimension ? { yDimension } : {}),
            }
          : null,
    [
      activeId,
      sample?.id,
      sampleId,
      gateId,
      xc?.name,
      yc?.name,
      x,
      y,
      mode,
      coordinateGateId,
      backgate,
      plotBounds,
      graphOptions,
      plotBins,
      threeDView,
      groupId,
      pooledView,
      search,
      xTransform,
      yTransform,
      xDimension,
      yDimension,
      pinnedView,
    ],
  );
  const applyPlotNavigation = useCallback((state: DesktopPlotState) => {
    setPinnedView(true);
    setSampleId(state.sampleId);
    setGateId(state.gateId);
    setX(state.x);
    setY(state.y ?? "");
    setMode(state.mode);
    setCoordinateGateId(state.coordinateGateId ?? null);
    setBackgate(state.backgateId ?? null);
    setXTransform(state.xTransform ?? null);
    setYTransform(state.yTransform ?? null);
    setXDimension(state.xDimension ?? null);
    setYDimension(state.yDimension ?? null);
    setThreeD(state.threeD ?? { z: "" });
    setGraphOptions(state.graphOptions ?? {});
    setPlotBins(state.bins ?? 160);
    setGroupId(state.groupId ?? null);
    setPooledView(state.pooled ?? false);
    setSearch(state.sampleFilter ?? "");
    setViewBounds({ signature: "", bounds: state.bounds ?? null });
    setTool("inspect");
  }, []);
  useEffect(
    () =>
      window.cytoforgeDesktop?.onPlotNavigation((state) => {
        if (state.workspaceId === activeId) applyPlotNavigation(state);
      }),
    [applyPlotNavigation, activeId],
  );
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), toast.error ? 12000 : 5000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!window.cytoforgeDesktop || detached) return;
    let cancelled = false;
    window.cytoforgeDesktop
      .getWorkspace()
      .then(async (value) => {
        const session = await window.cytoforgeDesktop?.getPlotSession();
        if (cancelled) return;
        setActiveId(value && /^[a-f0-9]{32}$/.test(value) ? value : null);
        if (session?.state && session.state.workspaceId === value)
          applyPlotNavigation(session.state);
        setStorageReady(true);
      })
      .catch(() => {
        if (!cancelled) setStorageReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [detached]);
  useEffect(() => {
    if (!storageReady || detached) return;
    if (activeId) localStorage.setItem("cytoforge.workspace", activeId);
    else localStorage.removeItem("cytoforge.workspace");
    window.cytoforgeDesktop?.saveWorkspace(activeId);
  }, [activeId, storageReady, detached]);
  useEffect(
    () =>
      window.cytoforgeDesktop?.onWorkspaceChanged((value) => {
        const current = cache.getQueryData<Workspace>([
          "workspace",
          value.workspaceId,
        ]);
        if (current && current.revision >= value.revision) return;
        void cache.invalidateQueries({
          predicate: (query) =>
            query.queryKey.includes(value.workspaceId) ||
            query.queryKey[0] === "workspaces",
        });
      }),
    [cache],
  );
  useEffect(() => {
    if (!desktopView || (!detached && !storageReady)) return;
    const key =
      `${desktopView.workspaceId}/${desktopView.sampleId}/${desktopView.gateId ?? "all"}` +
      (desktopView.pooled
        ? `/pooled/${desktopView.groupId ?? "all"}/${desktopView.sampleFilter ?? ""}`
        : "");
    localViewMemory.current.delete(key);
    localViewMemory.current.set(key, desktopView);
    while (localViewMemory.current.size > 192)
      localViewMemory.current.delete(
        localViewMemory.current.keys().next().value!,
      );
    void window.cytoforgeDesktop
      ?.updatePlotWindow(
        desktopView,
        gateDraftRef.current || plotDraftRef.current,
        tab === "analysis",
      )
      .catch((error) => notify(error.message, true));
    if (detached)
      document.title = `CytoForge · ${pooledView ? (pooledQuery.data?.group_name ?? "Pooled group") : (sample?.name ?? "Unavailable sample")} · ${gate?.name ?? (gateId ? "Unavailable population" : "All events")}`;
  }, [
    detached,
    activeId,
    sampleId,
    gateId,
    x,
    y,
    mode,
    coordinateGateId,
    backgate,
    plotBounds,
    graphOptions,
    plotBins,
    threeDView,
    draftGate,
    sample?.name,
    gate?.name,
    desktopView,
    tab,
    storageReady,
    plotDraft,
  ]);
  useEffect(() => {
    if (!workspace) return;
    if (
      !pinnedView &&
      gateId &&
      !workspace.gates.some(
        (g) => g.id === gateId && g.sample_id === sample?.id,
      )
    )
      setGateId(null);
    if (
      !pinnedView &&
      backgate &&
      !workspace.gates.some(
        (g) => g.id === backgate && g.sample_id === sample?.id,
      )
    )
      setBackgate(null);
    setSelected((ids) =>
      ids.filter((id) => workspace.samples.some((s) => s.id === id)),
    );
  }, [workspace, gateId, groupId, sample?.id, backgate, pinnedView]);
  const openWorkspace = async (doc: Workspace) => {
    if (plotDraftRef.current || gateDraftRef.current) {
      notify(
        "Finish or cancel the gate drawing before changing experiments",
        true,
      );
      return;
    }
    const session = await window.cytoforgeDesktop?.getPlotSession(doc.id);
    const remembered =
      session?.state ??
      [...localViewMemory.current.values()]
        .reverse()
        .find((v) => v.workspaceId === doc.id);
    setPinnedView(false);
    cache.setQueryData(["workspace", doc.id], doc);
    setActiveId(doc.id);
    setGroupId(null);
    setPooledView(false);
    setPooledAnalysis(null);
    setSelected([]);
    setSearch("");
    setTab("analysis");
    const first = doc.samples[0];
    setSampleId(first?.id ?? null);
    const demo = first?.source === "Synthetic demo";
    setGateId(
      demo
        ? (doc.gates.find(
            (g) => g.sample_id === first.id && g.name === "CD3+ T cells",
          )?.id ?? null)
        : null,
    );
    setX(demo ? "PE-A" : (first?.channels[0]?.name ?? ""));
    setY(demo ? "APC-A" : (first?.channels[1]?.name ?? ""));
    setMode("density");
    setTool("inspect");
    setBackgate(null);
    setXTransform(null);
    setYTransform(null);
    setCoordinateGateId(null);
    setXDimension(null);
    setYDimension(null);
    setViewBounds({ signature: "", bounds: null });
    setThreeD({
      z: first?.channels[2]?.name ?? first?.channels[0]?.name ?? "",
    });
    setGraphOptions({});
    setPlotBins(160);
    if (remembered?.workspaceId === doc.id) applyPlotNavigation(remembered);
    cache.invalidateQueries({ queryKey: ["workspaces"] });
  };
  const perform = async <T,>(
    label: string,
    fn: () => Promise<T>,
  ): Promise<T> => {
    if (operationLock.current)
      throw new Error("Wait for the current operation to finish");
    operationLock.current = true;
    setBusy(label);
    try {
      return await fn();
    } catch (err) {
      notify((err as Error).message, true);
      if (err instanceof ApiError && err.status === 409)
        workspaceQuery.refetch();
      throw err;
    } finally {
      operationLock.current = false;
      setBusy("");
    }
  };
  const commit: Commit = async (path, body, message, method = "POST") => {
    if (!workspace) throw new Error("Open a workspace first");
    return perform(message, async () => {
      const response = await post<Workspace>(
        `/workspaces/${workspace.id}${path}`,
        { ...(body as object), revision: workspace.revision },
        method,
      );
      cache.setQueryData(["workspace", workspace.id], response);
      cache.invalidateQueries({ queryKey: ["workspaces"] });
      notify(message);
      return response;
    });
  };
  const moveHistory = (direction: "undo" | "redo") =>
    commit(
      `/${direction}`,
      {},
      direction === "undo" ? "Change undone" : "Change restored",
    ).catch(() => {});
  const navigatePlot = (
    direction: PlotNavigationAction["direction"],
    sync = false,
    targetSampleId?: string,
    targetGateId?: string | null,
  ) => {
    if (!workspace || !desktopView || busy || draftGate || plotDraft || modal)
      return;
    const populationAction = [
      "parent",
      "population",
      "child",
      "sibling_previous",
      "sibling_next",
      "reset_population",
    ].includes(direction);
    void perform(
      populationAction ? "Opening population" : "Moving plot sample",
      async () => {
        if (window.cytoforgeDesktop) {
          await window.cytoforgeDesktop.updatePlotWindow(
            desktopView,
            false,
            true,
          );
          const result = await window.cytoforgeDesktop.navigatePlot({
            direction,
            sync,
            ...(targetSampleId ? { targetSampleId } : {}),
            ...(direction === "population"
              ? { targetGateId: targetGateId ?? null }
              : {}),
          });
          notify(
            `${result.moved === 1 ? "Plot moved" : `${result.moved} plots moved`}${result.skipped.length ? ` · skipped ${result.skipped.length} incompatible sample(s)` : ""}`,
          );
        } else {
          if (sync)
            throw new Error(
              "Synchronized navigation requires desktop plot windows",
            );
          const plan = await post<{
            available: boolean;
            reason: string;
            revision: number;
            views: { id: string; state: DesktopPlotState }[];
          }>(`/workspaces/${workspace.id}/plot-navigation/plan`, {
            revision: workspace.revision,
            initiator: "main",
            direction,
            ...(targetSampleId ? { targetSampleId } : {}),
            ...(direction === "population"
              ? { targetGateId: targetGateId ?? null }
              : {}),
            ...(populationAction
              ? {
                  rememberedViews: [...localViewMemory.current.values()].filter(
                    (v) =>
                      v.workspaceId === desktopView.workspaceId &&
                      v.sampleId === desktopView.sampleId,
                  ),
                }
              : {}),
            views: [{ id: "main", state: desktopView }],
          });
          if (!plan.available) throw new Error(plan.reason);
          applyPlotNavigation(plan.views[0].state);
        }
      },
    ).catch(() => {});
  };
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInput.current?.focus();
        return;
      }
      if (
        modal ||
        draftGate ||
        plotDraft ||
        target.matches("input, textarea, select") ||
        target.isContentEditable
      )
        return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (
          !busy &&
          (event.shiftKey
            ? historyQuery.data?.can_redo
            : historyQuery.data?.can_undo)
        )
          moveHistory(event.shiftKey ? "redo" : "undo");
        return;
      }
      if (
        (event.metaKey || event.ctrlKey) &&
        tab === "analysis" &&
        ["pagedown", "pageup", "u", "o"].includes(event.key.toLowerCase())
      ) {
        event.preventDefault();
        navigatePlot(
          event.key.toLowerCase() === "u"
            ? "parent"
            : event.key.toLowerCase() === "o"
              ? "child"
              : event.key === "PageDown"
                ? "previous"
                : "next",
          ["PageDown", "PageUp"].includes(event.key) && event.shiftKey,
        );
        return;
      }
      if (!event.metaKey && !event.ctrlKey && tab === "analysis") {
        const chosen = tools.find(
          (t) => t.shortcut?.toLowerCase() === event.key.toLowerCase(),
        );
        if (
          chosen &&
          (chosen.key !== "bisector" || oneDimensional) &&
          (chosen.key !== "curly" || curlyEnabled) &&
          (!oneDimensional ||
            ![
              "rectangle",
              "ellipse",
              "polygon",
              "freehand",
              "autogate",
              "quadrant",
              "spider",
              "curly",
            ].includes(chosen.key)) &&
          (mode !== "3d" || ["inspect", "pan", "zoom"].includes(chosen.key))
        ) {
          setTool(chosen.key);
          event.preventDefault();
        }
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  });
  const selectSample = (s: Sample, pop: string | null = null) => {
    if (plotDraftRef.current || gateDraftRef.current) {
      notify("Finish or cancel the gate drawing before changing samples", true);
      return;
    }
    setPinnedView(true);
    if (!s.channels.some((c) => c.name === x)) setX(s.channels[0]?.name ?? "");
    if (!s.channels.some((c) => c.name === y)) setY(s.channels[1]?.name ?? "");
    setSampleId(s.id);
    setGateId(pop);
    setCoordinateGateId(null);
    setXDimension(null);
    setYDimension(null);
    setBackgate(null);
    setTool("inspect");
    setTab("analysis");
    setXTransform(null);
    setYTransform(null);
    setThreeD((v) => ({
      ...v,
      z_transform: null,
      color_transform: null,
      size_transform: null,
      z_dimension: null,
      color_dimension: null,
      size_dimension: null,
    }));
  };
  const selectGate = (g: Gate | null) => {
    if (plotDraftRef.current || gateDraftRef.current) {
      notify(
        "Finish or cancel the gate drawing before changing populations",
        true,
      );
      return;
    }
    setTab("analysis");
    navigatePlot("population", false, undefined, g?.id ?? null);
  };
  const drawGate = (kind: GateDrawingKind, geometry: Partial<Gate>) => {
    if (!sample || !xc || busy) return;
    if (kind === "curly" && !curlyEnabled) return;
    const oneD = kind === "range" || kind === "bisector";
    const draft = {
      ...makeGate(
        sample.id,
        gate?.id ?? null,
        kind === "bisector" ? "range" : kind,
        xc,
        oneD ? null : (yc ?? null),
      ),
      ...geometry,
    };
    draft.name = geometry.provenance?.automatic_gate
      ? "Automatic population"
      : kind === "curly"
        ? "Curly"
        : kind === "spider"
          ? "Spider"
          : kind === "quadrant"
            ? "Quadrant"
            : kind === "bisector"
              ? "Bisector"
              : `Population ${gates.length + 1}`;
    if (
      kind === "quadrant" ||
      kind === "bisector" ||
      kind === "spider" ||
      kind === "curly" ||
      ((xDimension || yDimension || coordinateGate?.dimensions?.length) &&
        !geometry.dimensions?.length)
    ) {
      const dims = [xc, ...(!oneD && yc ? [yc] : [])].map((channel, axis) => {
        const imported =
          (axis === 0 ? xDimension : yDimension) ??
          dimensionForAxis(coordinateGate, channel.name, axis);
        return imported
          ? {
              ...imported,
              transform: channel.transform,
              minimum: null,
              maximum: null,
            }
          : ({
              channel: channel.name,
              transform: channel.transform ?? linear,
              compensation_ref: "sample",
              minimum: null,
              maximum: null,
              ratio_channels: null,
              ratio_a: 1,
              ratio_b: 0,
              ratio_c: 0,
            } as GateDimension);
      });
      if (kind === "quadrant" || kind === "bisector")
        Object.assign(draft, linkedPartition(draft, kind, dims, draft.bounds));
      else if (kind === "spider" || kind === "curly") {
        draft.dimensions = dims;
        draft.partition = { id: id(), kind, member: 1 };
      } else {
        draft.dimensions = dims;
        if (kind === "range" || kind === "rectangle") {
          draft.kind = "hyperrectangle";
          draft.dimensions = dims.map((d, i) => ({
            ...d,
            minimum: draft.bounds[i * 2],
            maximum: draft.bounds[i * 2 + 1],
          }));
          draft.bounds = [];
        } else if (kind === "ellipse") {
          draft.kind = "ellipsoid";
          draft.coordinates = draft.center!;
          const [rx, ry] = draft.radii!,
            c = Math.cos(draft.angle),
            s = Math.sin(draft.angle);
          draft.covariance = [
            [c * c * rx * rx + s * s * ry * ry, c * s * (rx * rx - ry * ry)],
            [c * s * (rx * rx - ry * ry), s * s * rx * rx + c * c * ry * ry],
          ];
          draft.distance_square = 1;
        }
      }
    }
    setEditingGate(false);
    setDraftGate(draft);
  };
  const saveGate = async (value: Gate) => {
    if (workspace?.revision !== gateRevision)
      throw new Error(
        "Workspace changed while this gate was being edited. Review the changes before saving the draft.",
      );
    if (pooledScope) {
      let proposed = [value];
      if (!editingGate && !value.partition && value.kind === "quadrant") {
        proposed = [1, 2, 3, 4].map((q) => {
          const dims = value.provenance?.coordinate_dimensions as
            GateDimension[] | undefined;
          const result = {
            ...value,
            id: id(),
            name: `${value.name} Q${q}`,
            quadrant: q as Gate["quadrant"],
          };
          return dims
            ? {
                ...result,
                kind: "hyperrectangle",
                bounds: [],
                dimensions: dims.map((d, i) => ({
                  ...d,
                  minimum: (i === 0 ? [2, 3].includes(q) : [1, 2].includes(q))
                    ? value.bounds[i]
                    : null,
                  maximum: (i === 0 ? [2, 3].includes(q) : [1, 2].includes(q))
                    ? null
                    : value.bounds[i],
                })),
              }
            : result;
        });
      }
      await new Promise<void>((resolve, reject) =>
        setPooledGateProposal({
          scope: structuredClone(pooledScope),
          revision: workspace!.revision,
          action: editingGate ? "edit" : "create",
          gates: proposed,
          resolve,
          reject,
        }),
      );
      setTool("inspect");
      if (editingGate && value.id === coordinateGateId) {
        setXTransform(null);
        setYTransform(null);
        setThreeD((v) => ({
          ...v,
          z_transform: null,
          color_transform: null,
          size_transform: null,
        }));
      }
      return;
    }
    if (!editingGate && value.partition) {
      await commit(
        "/gates",
        { gate: value },
        `Linked ${value.partition.kind} populations created`,
      );
    } else if (!editingGate && value.kind === "quadrant") {
      const quartet = [1, 2, 3, 4].map((q) => {
        const result = {
          ...value,
          id: id(),
          name: `${value.name} Q${q}`,
          quadrant: q,
        };
        const dims = value.provenance?.coordinate_dimensions as
          GateDimension[] | undefined;
        return dims
          ? {
              ...result,
              kind: "hyperrectangle",
              bounds: [],
              dimensions: dims.map((d, i) => ({
                ...d,
                minimum: (i === 0 ? [2, 3].includes(q) : [1, 2].includes(q))
                  ? value.bounds[i]
                  : null,
                maximum: (i === 0 ? [2, 3].includes(q) : [1, 2].includes(q))
                  ? null
                  : value.bounds[i],
              })),
            }
          : result;
      });
      await commit(
        "/gates/batch",
        { gates: quartet },
        "Quadrant populations created",
      );
    } else
      await commit(
        editingGate ? `/gates/${value.id}` : "/gates",
        { gate: value },
        editingGate ? "Population updated" : "Population created",
        editingGate ? "PUT" : "POST",
      );
    setTool("inspect");
    if (editingGate && value.id === coordinateGateId) {
      setXTransform(null);
      setYTransform(null);
      setThreeD((v) => ({
        ...v,
        z_transform: null,
        color_transform: null,
        size_transform: null,
      }));
    }
  };
  const [importProgress, setImportProgress] = useState<ImportSession | null>(
    null,
  );
  const importSelection = useRef<File[]>([]);
  const cancelImport = async () => {
    if (!workspace || !importProgress || importProgress.cancel_requested)
      return;
    try {
      const result = await post<ImportSession>(
        "/workspaces/" +
          workspace.id +
          "/imports/" +
          importProgress.id +
          "/cancel",
        {},
      );
      setImportProgress((current) =>
        current?.id === result.id ? result : current,
      );
    } catch (error) {
      notify((error as Error).message, true);
    }
  };
  const importSamples = async (
    files: FileList | File[] | null,
    allowDuplicates = false,
  ) => {
    if (!files?.length || !workspace) return;
    const selection = Array.from(files);
    importSelection.current = selection;
    const form = new FormData();
    selection.forEach((file) => form.append("files", file));
    await perform("Importing samples", async () => {
      setModal(null);
      const endpoint = "/workspaces/" + workspace.id;
      const session = await post<ImportSession>(endpoint + "/imports", {
        revision: workspace.revision,
      });
      const decorate = (value: ImportSession) => ({
        ...value,
        file_count: value.file_count || selection.length,
        bytes_total:
          value.bytes_total ||
          selection.reduce((total, file) => total + file.size, 0),
      });
      setImportProgress(decorate(session));
      let polling = false;
      const timer = window.setInterval(async () => {
        if (polling) return;
        polling = true;
        try {
          const value = await api<ImportSession>(
            endpoint + "/imports/" + session.id,
          );
          setImportProgress((current) =>
            current?.id === value.id ? decorate(value) : current,
          );
        } catch {
          // The import response reports terminal errors; a transient poll can retry.
        } finally {
          polling = false;
        }
      }, 350);
      try {
        const result = await api<ImportResult>(
          endpoint +
            "/import?" +
            params({
              revision: workspace.revision,
              import_id: session.id,
              allow_duplicates: allowDuplicates,
            }),
          { method: "POST", body: form },
        );
        cache.setQueryData(["workspace", workspace.id], result.workspace);
        cache.invalidateQueries({ queryKey: ["workspaces"] });
        setImportResult(result);
        setModal("import-result");
        if (!sample && result.workspace.samples[0])
          selectSample(result.workspace.samples[0]);
      } catch (error) {
        await post(endpoint + "/imports/" + session.id + "/cancel", {}).catch(
          () => {},
        );
        throw error;
      } finally {
        window.clearInterval(timer);
        setImportProgress(null);
      }
    }).catch(() => {});
    if (importInput.current) importInput.current.value = "";
  };
  const importProject = async (files: FileList | null) => {
    if (!files?.[0]) return;
    const form = new FormData();
    form.append("file", files[0]);
    await perform("Opening project archive", async () => {
      const doc = await api<Workspace>("/import/project", {
        method: "POST",
        body: form,
      });
      await openWorkspace(doc);
      notify("Project restored");
    }).catch(() => {});
    if (projectInput.current) projectInput.current.value = "";
  };
  const renderGateTree = (parent: string | null, depth = 0): React.ReactNode =>
    gates
      .filter((g) => g.parent_id === parent)
      .map((g) => {
        const value = countsQuery.data?.find((c) => c.id === g.id);
        return (
          <div className="gate-node" key={g.id}>
            <button
              className={`gate-row ${gate?.id === g.id ? "active" : ""}`}
              style={{ paddingLeft: 16 + depth * 13 }}
              onClick={() => selectGate(g)}
              onDoubleClick={() => void openPlotWindow(g, sample, false)}
              title={`${g.name} · ${formatNumber(value?.count, 0)} events`}
            >
              {g.magnetic ? (
                <Magnet
                  size={14}
                  style={{ color: g.color }}
                  aria-label="Magnetic gate"
                />
              ) : (
                <span
                  className="gate-dot"
                  style={{ backgroundColor: g.color }}
                />
              )}
              <span className="gate-name">{g.name}</span>
              <span className="gate-percent">
                {percentage(value?.percent_parent)}
              </span>
            </button>
            {renderGateTree(g.id, depth + 1)}
          </div>
        );
      });
  const currentPlot: Omit<PlotDefinition, "id" | "title"> | null =
    sample && xc
      ? {
          sample_id: sample.id,
          ...(pooledView
            ? { pooled: true, group_id: groupId, sample_filter: search }
            : {}),
          gate_id: gate?.id ?? null,
          coordinate_gate_id: coordinateGate?.id ?? null,
          ...(backgate ? { backgate_id: backgate } : {}),
          x: xc.name,
          y: oneDimensional ? null : (yc?.name ?? null),
          mode,
          bounds: plotBounds,
          bins: plotBins,
          graph_options: graphOptions,
          three_d: mode === "3d" ? threeDView : null,
          x_transform: xTransform,
          y_transform: oneDimensional ? null : yTransform,
          x_dimension: xDimension,
          y_dimension: oneDimensional ? null : yDimension,
        }
      : null;

  const openPlotWindow = async (
    population: Gate | null = gate,
    targetSample: Sample | null = sample,
    preserveView = true,
  ) => {
    if (!workspace || !targetSample || !window.cytoforgeDesktop) return;
    try {
      const reference = preserveView
        ? coordinateGate
        : population?.dimensions?.length
          ? population
          : null;
      const px = preserveView
        ? xc?.name
        : (reference?.dimensions?.[0]?.channel ??
          population?.x ??
          targetSample.channels[0]?.name);
      const py = preserveView
        ? yc?.name
        : (reference?.dimensions?.[1]?.channel ??
          population?.y ??
          targetSample.channels[1]?.name);
      const histogram = preserveView
        ? oneDimensional
        : population?.kind === "range" || reference?.dimensions?.length === 1;
      if (!px || (!histogram && !py))
        throw new Error("Select available plot channels first");
      await window.cytoforgeDesktop.openPlotWindow({
        workspaceId: workspace.id,
        sampleId: targetSample.id,
        gateId: population?.id ?? null,
        x: px,
        y: py ?? null,
        mode: preserveView
          ? (mode as DesktopPlotState["mode"])
          : histogram
            ? "histogram"
            : reference?.dimensions?.[2]
              ? "3d"
              : "density",
        coordinateGateId: reference?.id ?? null,
        backgateId: preserveView ? backgate : null,
        bounds: preserveView ? plotBounds : null,
        graphOptions: preserveView ? graphOptions : {},
        bins: preserveView ? plotBins : 160,
        groupId,
        sampleFilter: search,
        ...(pooledView ? { pooled: true } : {}),
        xTransform: preserveView ? xTransform : null,
        yTransform: preserveView ? yTransform : null,
        ...(preserveView && xDimension ? { xDimension } : {}),
        ...(preserveView && yDimension ? { yDimension } : {}),
        threeD:
          preserveView && threeDView.z
            ? threeDView
            : reference?.dimensions?.[2]
              ? { z: reference.dimensions[2].channel }
              : undefined,
      });
    } catch (error) {
      notify((error as Error).message, true);
    }
  };

  return (
    <div
      className={`app-shell ${detached ? "plot-window" : ""}`}
      data-plot-window={detached ? plotWindow!.id : undefined}
    >
      <input
        ref={importInput}
        className="hidden"
        type="file"
        multiple
        accept=".fcs,.csv"
        aria-label="Import FCS or CSV files"
        onChange={(e) => importSamples(e.target.files)}
      />
      <input
        ref={projectInput}
        className="hidden"
        type="file"
        accept=".cytoforge"
        aria-label="Open CytoForge project archive"
        onChange={(e) => importProject(e.target.files)}
      />
      <header className="topbar">
        <button
          className="brand"
          aria-label="CytoForge home"
          disabled={detached}
          onClick={() => {
            setActiveId(null);
            setModal(null);
          }}
        >
          <Logo />
          <span>CytoForge</span>
          <span className="brand-version">LOCAL</span>
        </button>
        <div className="workspace-select">
          <FolderOpen size={14} />
          <select
            aria-label="Active workspace"
            disabled={detached || !!busy || plotDraft || !!draftGate}
            value={activeId ?? ""}
            onChange={(e) => {
              const target = e.target.value;
              if (!target) {
                setActiveId(null);
                setSampleId(null);
                setGateId(null);
                setPinnedView(false);
                return;
              }
              void perform("Opening experiment", async () => {
                const doc = await api<Workspace>(`/workspaces/${target}`);
                await openWorkspace(doc);
              }).catch(() => {});
            }}
          >
            <option value="">Choose workspace</option>
            {workspaceList.data?.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
        <div className="topbar-spacer" />
        <label className="global-search">
          <Search size={15} />
          <input
            ref={searchInput}
            aria-label="Search workspace"
            disabled={plotDraft}
            placeholder="Search samples…"
            value={search}
            maxLength={256}
            onChange={(e) => setSearch(e.target.value)}
          />
          <kbd>⌘ K</kbd>
        </label>
        <span className="local-indicator">
          <span className="status-dot" />
          Local engine
        </span>
        <button
          className="icon-button"
          aria-label="Help and keyboard shortcuts"
          onClick={() => setModal("help")}
        >
          <CircleHelp size={19} />
        </button>
        {detached && (
          <button
            className="icon-button"
            aria-label="Close plot window"
            onClick={() => void window.cytoforgeDesktop?.closePlotWindow()}
          >
            <X size={18} />
          </button>
        )}
      </header>
      {!activeId ? (
        <main className="home">
          <div className="home-glow" />
          <div className="home-copy">
            <span className="eyebrow">
              <span className="status-dot" />
              YOUR DATA. YOUR MACHINE.
            </span>
            <h1>
              See more.
              <br />
              <span>Discover what matters.</span>
            </h1>
            <p>
              A precise workspace for complex cytometry.
              <br />
              From your first gate to your final figure, all in one place.
            </p>
            <div className="home-actions">
              <button
                className="button primary large"
                disabled={!!busy || plotDraft}
                onClick={() => {
                  setNewName("Untitled experiment");
                  setModal("new");
                }}
              >
                <Plus size={18} />
                New experiment
              </button>
              <button
                className="button large"
                disabled={!!busy || plotDraft}
                onClick={() => projectInput.current?.click()}
              >
                <FolderOpen size={18} />
                Open project
              </button>
            </div>
            <button
              className="demo-link"
              disabled={!!busy || plotDraft}
              onClick={() =>
                perform("Building demonstration workspace", async () => {
                  const doc = await post<Workspace>("/demo", {});
                  await openWorkspace(doc);
                }).catch(() => {})
              }
            >
              Explore the PBMC demo <ArrowRight size={16} />
              <span>12 samples · synthetic data</span>
            </button>
            <div className="home-features">
              <span>
                <ShieldCheck size={16} />
                Private by design
              </span>
              <span>
                <Activity size={16} />
                Full-data analysis
              </span>
              <span>
                <GitBranch size={16} />
                Reproducible workflows
              </span>
            </div>
          </div>
          <div className="home-visual" aria-hidden="true">
            <div className="visual-orbit orbit-one" />
            <div className="visual-orbit orbit-two" />
            <div className="visual-orbit orbit-three" />
            {Array.from({ length: 90 }, (_, i) => (
              <i
                className="visual-cell"
                key={i}
                style={{
                  left: `${22 + ((Math.sin(i * 5.73) + 1) / 2) * 57}%`,
                  top: `${24 + ((Math.cos(i * 3.61) + 1) / 2) * 52}%`,
                  opacity: 0.25 + (i % 6) / 8,
                  width: 2 + (i % 4),
                  height: 2 + (i % 4),
                  background: i % 3 ? "#38d9ba" : "#7ba8f8",
                }}
              />
            ))}
            <div className="visual-caption">
              <span>HIGH RESOLUTION. ZERO COMPROMISE.</span>
              <small>Every event has a story.</small>
            </div>
          </div>
          {workspaceList.data && workspaceList.data.length > 0 && (
            <div className="recent-workspaces">
              <div className="section-label">RECENT EXPERIMENTS</div>
              {workspaceList.data.map((w) => (
                <button
                  key={w.id}
                  onClick={() => {
                    setActiveId(w.id);
                    setSampleId(null);
                    setGateId(null);
                  }}
                >
                  <FolderOpen size={17} />
                  <span>
                    {w.name}
                    <small>
                      {w.sample_count} samples ·{" "}
                      {new Date(w.updated_at).toLocaleDateString()}
                    </small>
                  </span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </div>
          )}
          {workspaceList.error && (
            <ErrorState
              error={workspaceList.error}
              onRetry={() => workspaceList.refetch()}
            />
          )}
        </main>
      ) : workspaceQuery.isPending ? (
        <Loading label="Opening workspace" />
      ) : workspaceQuery.error ? (
        <ErrorState
          error={workspaceQuery.error}
          onRetry={() => workspaceQuery.refetch()}
        />
      ) : (
        workspace && (
          <>
            <div className="workbench">
              {!detached && (
                <nav className="nav-rail" aria-label="Workbench views">
                  {navItems.map((item) => (
                    <button
                      key={item.key}
                      className={tab === item.key ? "active" : ""}
                      aria-label={item.label}
                      disabled={plotDraft && item.key !== "analysis"}
                      aria-current={tab === item.key ? "page" : undefined}
                      title={item.label}
                      onClick={() => setTab(item.key)}
                    >
                      <item.icon size={21} />
                      <span>
                        {item.label === "Layout studio"
                          ? "Layouts"
                          : item.label}
                      </span>
                    </button>
                  ))}
                  <div className="rail-spacer" />
                  <button
                    aria-label="New workspace"
                    title="New workspace"
                    onClick={() => {
                      setNewName("Untitled experiment");
                      setModal("new");
                    }}
                  >
                    <FilePlus2 size={20} />
                    <span>New</span>
                  </button>
                </nav>
              )}
              <aside className="sidebar">
                <div className="sidebar-title">
                  <span>EXPERIMENT</span>
                  <button
                    className="icon-button"
                    aria-label="Create sample group"
                    onClick={() => setModal("group")}
                  >
                    <Plus size={16} />
                  </button>
                </div>
                <button
                  className={`group-row ${!groupId ? "active" : ""}`}
                  onClick={() => setGroupId(null)}
                >
                  <Layers3 size={15} />
                  <span>All samples</span>
                  <small>{workspace.samples.length}</small>
                </button>
                {workspace.groups.map((g) => (
                  <button
                    key={g.id}
                    className={`group-row ${groupId === g.id ? "active" : ""}`}
                    onClick={() => setGroupId(g.id)}
                  >
                    <span
                      className="group-square"
                      style={{ backgroundColor: g.color }}
                    />
                    <span>{g.name}</span>
                    <small>{g.sample_ids.length}</small>
                  </button>
                ))}
                <div className="sidebar-divider" />
                <div className="sidebar-title">
                  <span>SAMPLES</span>
                  <button
                    className="icon-button"
                    aria-label="Import samples"
                    disabled={!!busy || plotDraft}
                    onClick={() => importInput.current?.click()}
                  >
                    <Upload size={15} />
                  </button>
                </div>
                <div className="sidebar-samples">
                  <button
                    className={`pooled-toggle ${pooledView ? "active" : ""}`}
                    aria-label="Pool sample group"
                    aria-pressed={pooledView}
                    disabled={!!busy || plotDraft || !!draftGate || !!modal}
                    onClick={() => void togglePooled()}
                  >
                    <Layers3 size={15} />{" "}
                    {pooledView ? "Pooled group" : "Pool group"}
                  </button>
                  {visibleSamples.map((s) => (
                    <button
                      key={s.id}
                      className={`sidebar-sample ${sample?.id === s.id ? "active" : ""}`}
                      onClick={() => selectSample(s)}
                      onDoubleClick={() => void openPlotWindow(null, s, false)}
                    >
                      <FlaskConical size={13} />
                      <span>{s.name}</span>
                      <small
                        title={`${formatNumber(s.event_count, 0)} ${s.event_count === 1 ? "event" : "events"}`}
                      >
                        {s.event_count < 1000
                          ? formatNumber(s.event_count, 0)
                          : `${formatNumber(s.event_count / 1000, 1)}k`}
                      </small>
                    </button>
                  ))}
                  {!visibleSamples.length && (
                    <p className="sidebar-empty">
                      {search
                        ? "No matching samples"
                        : "No samples in this group"}
                    </p>
                  )}
                </div>
                <div className="sidebar-divider" />
                <div className="sidebar-title">
                  <span>POPULATIONS</span>
                  <GitBranch size={14} />
                </div>
                {sample ? (
                  <div className="gate-tree">
                    <button
                      className={`gate-row root-gate ${!gateId ? "active" : ""}`}
                      onClick={() => selectGate(null)}
                      onDoubleClick={() =>
                        void openPlotWindow(null, sample, false)
                      }
                    >
                      <ChevronDown size={13} />
                      <span className="gate-name">All events</span>
                      <span className="gate-percent">100%</span>
                    </button>
                    {renderGateTree(null)}
                    {!gates.length && (
                      <p className="sidebar-empty">
                        Draw a gate to define a population.
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="sidebar-empty">Select a sample to begin.</p>
                )}
                <div className="sidebar-bottom">
                  <span>{workspace.samples.length} samples</span>
                  <span>{formatNumber(totalEvents / 1e6, 2)}M events</span>
                </div>
              </aside>
              <main className={`main-content view-${tab}`} ref={mainContent}>
                <div className="workspace-toolbar">
                  <div className="workspace-breadcrumb">
                    <FolderOpen size={14} />
                    <span>{workspace.name}</span>
                    <ChevronRight size={13} />
                    <strong>
                      {detached
                        ? "Plot window"
                        : navItems.find((n) => n.key === tab)?.label}
                    </strong>
                  </div>
                  <div className="workspace-actions">
                    <button
                      className="icon-button"
                      aria-label="Undo"
                      title="Undo · Ctrl/⌘ Z"
                      disabled={!!busy || !historyQuery.data?.can_undo}
                      onClick={() => moveHistory("undo")}
                    >
                      <Undo2 size={17} />
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Redo"
                      title="Redo · Ctrl/⌘ Shift Z"
                      disabled={!!busy || !historyQuery.data?.can_redo}
                      onClick={() => moveHistory("redo")}
                    >
                      <Redo2 size={17} />
                    </button>
                    <span className="toolbar-separator" />
                    <button
                      className="button small"
                      disabled={!!busy || plotDraft}
                      onClick={() => importInput.current?.click()}
                    >
                      <Plus size={14} />
                      Import FCS
                    </button>
                    <button
                      className="button small"
                      disabled={!!busy || plotDraft}
                      onClick={() => setModal("interchange")}
                    >
                      <FileText size={14} /> Import gates
                    </button>
                    <button
                      className="button small"
                      disabled={!!busy || plotDraft}
                      onClick={() =>
                        perform("Exporting portable project", () =>
                          download(
                            `/workspaces/${workspace.id}/export/project`,
                            `${workspace.name}.cytoforge`,
                          ),
                        )
                          .then(() => notify("Portable project exported"))
                          .catch(() => {})
                      }
                    >
                      <Download size={14} />
                      Save project
                    </button>
                  </div>
                </div>
                {missingPlotInput && (
                  <div className="plot-input-unavailable" role="status">
                    <strong>{missingPlotInput}</strong>
                    <span>
                      Original references are retained. Restore the source or
                      choose a replacement to resume this plot.
                    </span>
                    <button
                      className="button small"
                      onClick={() => {
                        setGateId(null);
                        setCoordinateGateId(null);
                        setBackgate(null);
                      }}
                    >
                      Use all events and sample coordinates
                    </button>
                    {sample &&
                      (!xc ||
                        (!oneDimensional && !yc) ||
                        (mode === "3d" && !zc)) && (
                        <div className="field-row">
                          {[
                            "X",
                            ...(!oneDimensional ? ["Y"] : []),
                            ...(mode === "3d" ? ["Z"] : []),
                          ].map((axis) => (
                            <label className="field" key={axis}>
                              {axis} replacement channel
                              <select
                                aria-label={`${axis} replacement channel`}
                                value={
                                  axis === "X" ? x : axis === "Y" ? y : threeD.z
                                }
                                onChange={(event) => {
                                  if (axis === "X") {
                                    setX(event.target.value);
                                    setXDimension(null);
                                    setXTransform(null);
                                  } else if (axis === "Y") {
                                    setY(event.target.value);
                                    setYDimension(null);
                                    setYTransform(null);
                                  } else
                                    setThreeD((v) => ({
                                      ...v,
                                      z: event.target.value,
                                      z_transform: null,
                                      z_dimension: null,
                                    }));
                                }}
                              >
                                <option
                                  value={
                                    axis === "X"
                                      ? x
                                      : axis === "Y"
                                        ? y
                                        : threeD.z
                                  }
                                >
                                  Choose an available channel
                                </option>
                                {plotChannels.map((channel) => (
                                  <option
                                    key={channel.name}
                                    value={channel.name}
                                  >
                                    {channelLabel(channel)}
                                  </option>
                                ))}
                              </select>
                            </label>
                          ))}
                        </div>
                      )}
                    {sample && mode === "3d" && (
                      <div className="field-row">
                        {(["color_by", "size_by"] as const).map((field) => (
                          <label className="field" key={field}>
                            {field === "color_by" ? "Color" : "Size"}{" "}
                            replacement parameter
                            <select
                              aria-label={`3D ${field === "color_by" ? "color" : "size"} replacement parameter`}
                              value={threeD[field] ?? ""}
                              onChange={(e) =>
                                setThreeD((v) => ({
                                  ...v,
                                  [field]: e.target.value || null,
                                  ...(field === "color_by"
                                    ? {
                                        color_transform: null,
                                        color_bounds: null,
                                        color_dimension: null,
                                      }
                                    : {
                                        size_transform: null,
                                        size_bounds: null,
                                        size_dimension: null,
                                      }),
                                }))
                              }
                            >
                              <option value="">Uniform</option>
                              {threeD[field] &&
                                !plotChannels.some(
                                  (c) => c.name === threeD[field],
                                ) && (
                                  <option value={threeD[field]!}>
                                    {threeD[field]} · unavailable
                                  </option>
                                )}
                              {plotChannels.map((c) => (
                                <option key={c.name} value={c.name}>
                                  {channelLabel(c)}
                                </option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {tab === "analysis" &&
                  (!sample || !xc ? (
                    <Empty
                      icon={<FlaskConical size={40} />}
                      title={
                        pinnedView
                          ? "Plot source unavailable"
                          : "Bring your experiment to life"
                      }
                      text={
                        pinnedView
                          ? "Select a sample and its channels, or undo the source removal."
                          : "Import acquisition files to explore distributions, draw gates, and quantify populations."
                      }
                    >
                      <button
                        className="button primary"
                        onClick={() => importInput.current?.click()}
                      >
                        <Upload size={16} />
                        Import FCS or CSV
                      </button>
                    </Empty>
                  ) : (
                    <div
                      className={`analysis-layout ${inspector && !missingPlotInput && !inlineGateEdit ? "" : "inspector-hidden"}`}
                    >
                      <div className="analysis-main">
                        <div className="analysis-heading">
                          <div>
                            <div className="analysis-kicker">
                              <span className="eyebrow">
                                POPULATION ANALYSIS
                              </span>
                              {sample.source === "Synthetic demo" && (
                                <Tag color="#f0b96a">SYNTHETIC DEMO</Tag>
                              )}
                            </div>
                            <h1 style={graphTextCSS(graphOptions, "title")}>
                              {missingPlotInput
                                ? "Plot source unavailable"
                                : (gate?.name ?? "All events")}
                            </h1>
                            <p>
                              {sample.name} <span> / </span>
                              {missingPlotInput
                                ? "Unavailable source"
                                : gate
                                  ? "Gated population"
                                  : "Ungated sample"}
                            </p>
                          </div>
                          <div className="analysis-heading-actions">
                            {window.cytoforgeDesktop && (
                              <button
                                className="button small open-plot-window"
                                aria-label="Open plot in new window"
                                disabled={!!busy || !!missingPlotInput}
                                onClick={() => void openPlotWindow()}
                              >
                                <ExternalLink size={14} />
                                <span>New plot window</span>
                              </button>
                            )}
                            <button
                              className="button small"
                              onClick={() => setModal("derived")}
                            >
                              <Braces size={14} />
                              Derived parameter
                            </button>
                            <button
                              className="button small"
                              onClick={() => setModal("apply")}
                            >
                              <GitBranch size={14} />
                              Apply gates
                            </button>
                            <button
                              className="icon-button"
                              aria-label={
                                inspector ? "Hide inspector" : "Show inspector"
                              }
                              onClick={() => setInspector(!inspector)}
                            >
                              {inspector ? (
                                <PanelRightClose size={18} />
                              ) : (
                                <PanelRightOpen size={18} />
                              )}
                            </button>
                          </div>
                        </div>
                        {staleAnalysis && (
                          <div className="analysis-stale-notice" role="status">
                            <span>
                              {staleAnalysis.request.name} retains results from
                              earlier scientific inputs. Rerun the analysis to
                              refresh these parameters.
                            </span>
                            {!detached && (
                              <button
                                className="text-button"
                                onClick={() => setTab("discovery")}
                              >
                                Open Discovery
                              </button>
                            )}
                          </div>
                        )}
                        <div className="metric-row">
                          <div>
                            <span>POPULATION EVENTS</span>
                            <strong>
                              {formatNumber(
                                missingPlotInput
                                  ? null
                                  : gate
                                    ? count?.count
                                    : plotTotalEvents,
                                0,
                              )}
                            </strong>
                            <small>
                              of {formatNumber(plotTotalEvents, 0)} acquired
                              {pooledView &&
                                ` · ${pooledQuery.data?.sources.length ?? "…"} samples`}
                            </small>
                          </div>
                          <div>
                            <span>FREQUENCY OF PARENT</span>
                            <strong className="accent">
                              {percentage(
                                missingPlotInput
                                  ? null
                                  : gate
                                    ? count?.percent_parent
                                    : plotTotalEvents
                                      ? 100
                                      : null,
                              )}
                            </strong>
                            <small>
                              {gate?.parent_id
                                ? gates.find((g) => g.id === gate.parent_id)
                                    ?.name
                                : "All events"}
                            </small>
                          </div>
                          <div>
                            <span>PANEL</span>
                            <strong>
                              {pooledView
                                ? pooledQuery.data?.common_channels.length
                                : sample.channels.length}
                              <em>parameters</em>
                            </strong>
                            <small>
                              {pooledView
                                ? "Each sample's correction"
                                : sample.compensation_id
                                  ? "Compensation applied"
                                  : "Uncompensated data"}
                            </small>
                          </div>
                        </div>
                        <section className="panel primary-plot">
                          {inlineGateEdit && draftGate ? (
                            <InlineGateEditor
                              key={draftGate.id}
                              gate={draftGate}
                              workspace={workspace}
                              baseRevision={gateRevision}
                              pooledScope={pooledScope}
                              busy={!!busy}
                              onSave={saveGate}
                              onClose={closeGate}
                              onRebase={() =>
                                setGateRevision(workspace.revision)
                              }
                              onDetails={inlineDetails}
                            />
                          ) : (
                            <>
                              <div className="plot-card-heading">
                                <div className="plot-mode-switch">
                                  {[
                                    "density",
                                    "scatter",
                                    "histogram",
                                    "cdf",
                                    "contour",
                                    "zebra",
                                    "pseudocolor",
                                    "3d",
                                  ].map((m) => (
                                    <button
                                      key={m}
                                      aria-pressed={mode === m}
                                      className={mode === m ? "active" : ""}
                                      disabled={
                                        plotDraft || !!draftGate || !!busy
                                      }
                                      onClick={() => {
                                        if (
                                          !["histogram", "cdf"].includes(m) &&
                                          !plotChannels.some(
                                            (c) => c.name === y,
                                          )
                                        )
                                          setY(
                                            plotChannels[1]?.name ??
                                              plotChannels[0].name,
                                          );
                                        if (m === "3d" && !threeD.z)
                                          setThreeD((v) => ({
                                            ...v,
                                            z:
                                              plotChannels[2]?.name ??
                                              plotChannels[0].name,
                                          }));
                                        setMode(m);
                                      }}
                                    >
                                      {m === "3d"
                                        ? "3D"
                                        : m === "cdf"
                                          ? "CDF"
                                          : m[0].toUpperCase() + m.slice(1)}
                                    </button>
                                  ))}
                                </div>
                                <div
                                  className="gate-tools"
                                  role="toolbar"
                                  aria-label="Gating tools"
                                >
                                  {canEditGateOnPlot(gate) && (
                                    <button
                                      className="icon-button"
                                      title="Edit gate on plot"
                                      aria-label="Edit gate on plot"
                                      disabled={
                                        !!busy ||
                                        !!draftGate ||
                                        plotDraft ||
                                        !!modal
                                      }
                                      onClick={() => startInlineEdit(gate)}
                                    >
                                      <Edit3 size={16} />
                                    </button>
                                  )}
                                  {tools.map((t) => (
                                    <button
                                      key={t.key}
                                      className={`icon-button ${tool === t.key ? "active" : ""}`}
                                      title={`${t.name}${t.shortcut ? ` · ${t.shortcut}` : ""}`}
                                      aria-label={t.name}
                                      aria-pressed={tool === t.key}
                                      disabled={
                                        !!busy ||
                                        plotDraft ||
                                        (t.key === "curly" && !curlyEnabled) ||
                                        (t.key === "bisector" &&
                                          !oneDimensional) ||
                                        (mode === "3d" &&
                                          !["inspect", "pan", "zoom"].includes(
                                            t.key,
                                          )) ||
                                        (oneDimensional &&
                                          [
                                            "rectangle",
                                            "ellipse",
                                            "polygon",
                                            "freehand",
                                            "autogate",
                                            "quadrant",
                                            "spider",
                                            "curly",
                                          ].includes(t.key))
                                      }
                                      onClick={() => setTool(t.key)}
                                    >
                                      <t.icon size={16} />
                                    </button>
                                  ))}
                                </div>
                              </div>
                              <nav
                                className="population-breadcrumbs"
                                aria-label="Population breadcrumbs"
                              >
                                <button
                                  className="breadcrumb-population"
                                  aria-label="View all events"
                                  aria-current={!gateId ? "page" : undefined}
                                  disabled={
                                    !!busy ||
                                    !!draftGate ||
                                    plotDraft ||
                                    !!modal
                                  }
                                  onClick={() => selectGate(null)}
                                >
                                  All events
                                </button>
                                {populationPath.length > 6 && (
                                  <select
                                    aria-label="Earlier population ancestors"
                                    value=""
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal
                                    }
                                    onChange={(e) =>
                                      selectGate(
                                        gates.find(
                                          (g) => g.id === e.target.value,
                                        ) ?? null,
                                      )
                                    }
                                  >
                                    <option value="">
                                      … {populationPath.length - 6} ancestors
                                    </option>
                                    {populationPath.slice(0, -6).map((g) => (
                                      <option key={g.id} value={g.id}>
                                        {g.name}
                                      </option>
                                    ))}
                                  </select>
                                )}
                                {populationPath.slice(-6).map((g) => (
                                  <span key={g.id}>
                                    <ChevronRight
                                      size={13}
                                      aria-hidden="true"
                                    />
                                    <button
                                      className="breadcrumb-population"
                                      aria-label={`View population ${g.name}`}
                                      aria-current={
                                        g.id === gateId ? "page" : undefined
                                      }
                                      disabled={
                                        !!busy ||
                                        !!draftGate ||
                                        plotDraft ||
                                        !!modal
                                      }
                                      onClick={() => selectGate(g)}
                                    >
                                      {g.name}
                                    </button>
                                  </span>
                                ))}
                                <div className="population-branches">
                                  <select
                                    aria-label="Open child population"
                                    value=""
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal ||
                                      !childPopulations.length
                                    }
                                    onChange={(e) =>
                                      selectGate(
                                        childPopulations.find(
                                          (g) => g.id === e.target.value,
                                        ) ?? null,
                                      )
                                    }
                                  >
                                    <option value="">
                                      Children ({childPopulations.length})
                                    </option>
                                    {childPopulations.map((g) => (
                                      <option key={g.id} value={g.id}>
                                        {g.name}
                                      </option>
                                    ))}
                                  </select>
                                  <button
                                    className="icon-button"
                                    aria-label="Previous sibling population"
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal ||
                                      !gate ||
                                      siblingPopulations.findIndex(
                                        (g) => g.id === gate.id,
                                      ) <= 0
                                    }
                                    onClick={() =>
                                      navigatePlot("sibling_previous")
                                    }
                                  >
                                    <ArrowLeft size={14} />
                                  </button>
                                  <button
                                    className="icon-button"
                                    aria-label="Next sibling population"
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal ||
                                      !gate ||
                                      siblingPopulations.findIndex(
                                        (g) => g.id === gate.id,
                                      ) >=
                                        siblingPopulations.length - 1
                                    }
                                    onClick={() => navigatePlot("sibling_next")}
                                  >
                                    <ArrowRight size={14} />
                                  </button>
                                  <button
                                    className="text-button"
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal
                                    }
                                    title="Use this population’s defining axes and reset zoom"
                                    onClick={() =>
                                      navigatePlot("reset_population")
                                    }
                                  >
                                    Reset population view
                                  </button>
                                </div>
                              </nav>
                              <div
                                className="plot-navigation"
                                role="toolbar"
                                aria-label="Sample navigation"
                              >
                                <button
                                  className="icon-button"
                                  aria-label="Previous matching sample"
                                  title="Previous sample · Ctrl/Cmd+PageDown · Shift moves matching plots together"
                                  disabled={
                                    !!busy ||
                                    !!draftGate ||
                                    plotDraft ||
                                    !!modal ||
                                    !!missingPlotInput ||
                                    visibleSamples.findIndex(
                                      (s) => s.id === sample.id,
                                    ) <= 0
                                  }
                                  onClick={(event) =>
                                    navigatePlot("previous", event.shiftKey)
                                  }
                                >
                                  <ArrowLeft size={16} />
                                </button>
                                <label className="navigation-sample">
                                  <span>
                                    {pooledView ? "Representative" : "Sample"}
                                  </span>
                                  <select
                                    aria-label="Plot sample"
                                    value={sample.id}
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal ||
                                      !!missingPlotInput ||
                                      !visibleSamples.some(
                                        (s) => s.id === sample.id,
                                      )
                                    }
                                    onChange={(event) =>
                                      navigatePlot(
                                        "select",
                                        false,
                                        event.target.value,
                                      )
                                    }
                                  >
                                    {!visibleSamples.some(
                                      (s) => s.id === sample.id,
                                    ) && (
                                      <option value={sample.id}>
                                        {sample.name} · outside selection
                                      </option>
                                    )}
                                    {visibleSamples.map((s) => (
                                      <option key={s.id} value={s.id}>
                                        {s.name}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                                <button
                                  className="icon-button"
                                  aria-label="Next matching sample"
                                  title="Next sample · Ctrl/Cmd+PageUp · Shift moves matching plots together"
                                  disabled={
                                    !!busy ||
                                    !!draftGate ||
                                    plotDraft ||
                                    !!modal ||
                                    !!missingPlotInput ||
                                    !visibleSamples.some(
                                      (s) => s.id === sample.id,
                                    ) ||
                                    visibleSamples.findIndex(
                                      (s) => s.id === sample.id,
                                    ) ===
                                      visibleSamples.length - 1
                                  }
                                  onClick={(event) =>
                                    navigatePlot("next", event.shiftKey)
                                  }
                                >
                                  <ArrowRight size={16} />
                                </button>
                                <button
                                  className="icon-button"
                                  aria-label="Parent population"
                                  title="Defining population · Ctrl/Cmd+U"
                                  disabled={
                                    !!busy ||
                                    !!draftGate ||
                                    plotDraft ||
                                    !!modal ||
                                    !!missingPlotInput ||
                                    !gate
                                  }
                                  onClick={() => navigatePlot("parent")}
                                >
                                  <ArrowUp size={16} />
                                </button>
                                <label>
                                  <span>Group</span>
                                  <select
                                    aria-label="Plot sample group"
                                    value={groupId ?? ""}
                                    disabled={
                                      !!busy ||
                                      !!draftGate ||
                                      plotDraft ||
                                      !!modal
                                    }
                                    onChange={(event) =>
                                      setGroupId(event.target.value || null)
                                    }
                                  >
                                    <option value="">All samples</option>
                                    {groupId && !selectedGroup && (
                                      <option value={groupId}>
                                        Unavailable group
                                      </option>
                                    )}
                                    {workspace.groups.map((g) => (
                                      <option key={g.id} value={g.id}>
                                        {g.name}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                                <button
                                  className={`text-button pooled-toggle ${pooledView ? "active" : ""}`}
                                  aria-label="Pool plot group"
                                  aria-pressed={pooledView}
                                  disabled={
                                    !!busy ||
                                    plotDraft ||
                                    !!draftGate ||
                                    !!modal
                                  }
                                  onClick={() => void togglePooled()}
                                >
                                  <Layers3 size={16} />
                                  {pooledView ? "Pooled" : "Pool group"}
                                </button>
                                <span className="navigation-hint">
                                  {groupId && !selectedGroup
                                    ? "Restore or choose a group"
                                    : !visibleSamples.some(
                                          (s) => s.id === sample.id,
                                        )
                                      ? "Current sample is outside the group or search"
                                      : "Shift-click an arrow to move matching plots together"}
                                </span>
                              </div>
                              {(xTransform ||
                                yTransform ||
                                threeD.z_transform ||
                                threeD.color_transform ||
                                threeD.size_transform) && (
                                <div className="plot-scale-notice">
                                  <span>Retained view scales</span>
                                  <button
                                    className="button small"
                                    disabled={plotDraft}
                                    onClick={() => {
                                      setXTransform(null);
                                      setYTransform(null);
                                      setThreeD((v) => ({
                                        ...v,
                                        z_transform: null,
                                        color_transform: null,
                                        size_transform: null,
                                      }));
                                      setPlotBounds(null);
                                    }}
                                  >
                                    Use current sample scales
                                  </button>
                                </div>
                              )}
                              <div className="axis-toolbar">
                                <label>
                                  <span>X</span>
                                  <select
                                    aria-label="X axis channel"
                                    disabled={plotDraft}
                                    value={xc.name}
                                    onChange={(e) => {
                                      setX(e.target.value);
                                      setXTransform(null);
                                      setXDimension(
                                        virtualDimension(
                                          sample,
                                          e.target.value,
                                          [
                                            xDimension,
                                            yDimension,
                                            threeD.z_dimension,
                                            threeD.color_dimension,
                                            threeD.size_dimension,
                                          ],
                                        ),
                                      );
                                    }}
                                  >
                                    {plotChannels.map((c) => (
                                      <option key={c.name} value={c.name}>
                                        {channelLabel(c)}
                                      </option>
                                    ))}
                                  </select>
                                  <button
                                    className="axis-transform"
                                    disabled={plotDraft}
                                    onClick={() => {
                                      if (xDimension) {
                                        setAxisEditor(0);
                                        return;
                                      }
                                      setTransformChannel(xc.name);
                                      if (
                                        coordinateGate?.dimensions?.some(
                                          (d) => d.channel === xc.name,
                                        )
                                      ) {
                                        setDraftGate(coordinateGate);
                                        setEditingGate(true);
                                      } else setModal("transform");
                                    }}
                                    title="Edit X channel transform"
                                  >
                                    {xc.transform.kind}
                                    <Settings2 size={12} />
                                  </button>
                                  <button
                                    className="axis-transform"
                                    disabled={plotDraft || !!busy}
                                    aria-label="X coordinate definition"
                                    onClick={() => setAxisEditor(0)}
                                  >
                                    Coordinates
                                  </button>
                                </label>
                                {!oneDimensional && yc && (
                                  <label>
                                    <span>Y</span>
                                    <select
                                      aria-label="Y axis channel"
                                      disabled={plotDraft}
                                      value={yc.name}
                                      onChange={(e) => {
                                        setY(e.target.value);
                                        setYTransform(null);
                                        setYDimension(
                                          virtualDimension(
                                            sample,
                                            e.target.value,
                                            [
                                              xDimension,
                                              yDimension,
                                              threeD.z_dimension,
                                              threeD.color_dimension,
                                              threeD.size_dimension,
                                            ],
                                          ),
                                        );
                                      }}
                                    >
                                      {plotChannels.map((c) => (
                                        <option key={c.name} value={c.name}>
                                          {channelLabel(c)}
                                        </option>
                                      ))}
                                    </select>
                                    <button
                                      className="axis-transform"
                                      disabled={plotDraft}
                                      onClick={() => {
                                        if (yDimension) {
                                          setAxisEditor(1);
                                          return;
                                        }
                                        setTransformChannel(yc.name);
                                        if (
                                          coordinateGate?.dimensions?.some(
                                            (d) => d.channel === yc.name,
                                          )
                                        ) {
                                          setDraftGate(coordinateGate);
                                          setEditingGate(true);
                                        } else setModal("transform");
                                      }}
                                      title="Edit Y channel transform"
                                    >
                                      {yc.transform.kind}
                                      <Settings2 size={12} />
                                    </button>
                                    <button
                                      className="axis-transform"
                                      disabled={plotDraft || !!busy}
                                      aria-label="Y coordinate definition"
                                      onClick={() => setAxisEditor(1)}
                                    >
                                      Coordinates
                                    </button>
                                  </label>
                                )}
                              </div>
                              {mode === "3d" && zc && (
                                <div className="axis-toolbar three-d-axis">
                                  <label>
                                    <span>Z</span>
                                    <select
                                      aria-label="Z axis channel"
                                      disabled={plotDraft}
                                      value={zc.name}
                                      onChange={(e) =>
                                        setThreeD((v) => ({
                                          ...v,
                                          z: e.target.value,
                                          z_transform: null,
                                          z_dimension: virtualDimension(
                                            sample,
                                            e.target.value,
                                            [
                                              xDimension,
                                              yDimension,
                                              v.z_dimension,
                                              v.color_dimension,
                                              v.size_dimension,
                                            ],
                                          ),
                                        }))
                                      }
                                    >
                                      {plotChannels.map((c) => (
                                        <option key={c.name} value={c.name}>
                                          {channelLabel(c)}
                                        </option>
                                      ))}
                                    </select>
                                    <button
                                      className="axis-transform"
                                      disabled={plotDraft}
                                      title="Edit Z channel transform"
                                      onClick={() => {
                                        if (threeD.z_dimension) {
                                          setAxisEditor(2);
                                          return;
                                        }
                                        setTransformChannel(zc.name);
                                        if (
                                          coordinateGate?.dimensions?.some(
                                            (d) => d.channel === zc.name,
                                          )
                                        ) {
                                          setDraftGate(coordinateGate);
                                          setEditingGate(true);
                                        } else setModal("transform");
                                      }}
                                    >
                                      {zc.transform.kind}
                                      <Settings2 size={12} />
                                    </button>
                                    <button
                                      className="axis-transform"
                                      disabled={plotDraft || !!busy}
                                      aria-label="Z coordinate definition"
                                      onClick={() => setAxisEditor(2)}
                                    >
                                      Coordinates
                                    </button>
                                  </label>
                                  {(["Color", "Size"] as const).map(
                                    (name, i) => (
                                      <button
                                        className="button small"
                                        key={name}
                                        disabled={plotDraft || !!busy}
                                        aria-label={`${name} coordinate definition`}
                                        onClick={() => setAxisEditor(i + 3)}
                                      >
                                        {name} coordinates
                                      </button>
                                    ),
                                  )}
                                </div>
                              )}
                              {coordinateGate && (
                                <div className="coordinate-context">
                                  <span>
                                    Gate coordinates · {coordinateGate.name}
                                  </span>
                                  <button
                                    className="text-button"
                                    disabled={plotDraft}
                                    onClick={() => {
                                      setCoordinateGateId(null);
                                      if (
                                        !xDimension &&
                                        sample.channels.every(
                                          (c) => c.name !== x,
                                        )
                                      )
                                        setX(sample.channels[0].name);
                                      if (
                                        !yDimension &&
                                        sample.channels.every(
                                          (c) => c.name !== y,
                                        )
                                      )
                                        setY(sample.channels[1]?.name ?? "");
                                      if (
                                        mode === "3d" &&
                                        !threeD.z_dimension &&
                                        sample.channels.every(
                                          (c) => c.name !== threeDView.z,
                                        )
                                      )
                                        setThreeD((v) => ({
                                          ...v,
                                          z:
                                            sample.channels[2]?.name ??
                                            sample.channels[0].name,
                                          z_transform: null,
                                          z_dimension: null,
                                        }));
                                    }}
                                  >
                                    Use sample display settings
                                  </button>
                                </div>
                              )}
                              <GraphControls
                                disabled={plotDraft}
                                mode={mode}
                                options={graphOptions}
                                bins={plotBins}
                                onBinsChange={setPlotBins}
                                onChange={(next) => {
                                  if (
                                    next.axis_extent !==
                                    graphOptions.axis_extent
                                  )
                                    setPlotBounds(null);
                                  setGraphOptions(next);
                                }}
                              />
                              {(!missingPlotInput || plotDraft) && (
                                <Plot
                                  key={`${sample.id}-${gateId}-${xc.name}-${yc?.name}-${mode}`}
                                  workspaceId={workspace.id}
                                  revision={workspace.revision}
                                  sample={sample}
                                  pooledScope={pooledScope}
                                  gateId={gateId}
                                  x={xc.name}
                                  y={oneDimensional ? null : (yc?.name ?? null)}
                                  mode={mode}
                                  graphOptions={graphOptions}
                                  bins={plotBins}
                                  tool={tool}
                                  onDraw={drawGate}
                                  onEditGate={(identifier) => {
                                    const value = gates.find(
                                      (g) => g.id === identifier,
                                    );
                                    if (value && canEditGateOnPlot(value))
                                      startInlineEdit(value);
                                    else
                                      notify(
                                        "Use numeric settings for this gate.",
                                      );
                                  }}
                                  backgateId={backgate}
                                  coordinateGateId={coordinateGateId}
                                  initialBounds={plotBounds}
                                  onBoundsChange={setPlotBounds}
                                  threeD={threeDView}
                                  onThreeDChange={setThreeD}
                                  onDraftChange={updatePlotDraft}
                                  drawingReset={drawingReset}
                                  plotParameters={plotChannels}
                                  xTransform={xTransform}
                                  yTransform={yTransform}
                                  xDimension={xDimension}
                                  yDimension={
                                    oneDimensional ? null : yDimension
                                  }
                                />
                              )}
                              {plotDraft && (
                                <div
                                  className="plot-draft-notice"
                                  role="status"
                                >
                                  <span>
                                    Finish or cancel this gate drawing before
                                    changing views.
                                  </span>
                                  <button
                                    className="text-button"
                                    onClick={() =>
                                      setDrawingReset((v) => v + 1)
                                    }
                                  >
                                    Cancel unfinished drawing
                                  </button>
                                </div>
                              )}
                            </>
                          )}
                        </section>
                        {pooledView && (
                          <details
                            className="pooled-source-summary"
                            aria-label="Pooled source samples"
                            open={
                              !!pooledQuery.error || !!pooledPopulation.error
                            }
                          >
                            <summary>
                              {pooledInfo?.group_name ??
                                selectedGroup?.name ??
                                "All samples"}{" "}
                              · {pooledInfo?.sources.length ?? "…"} original
                              samples ·{" "}
                              {formatNumber(pooledInfo?.population_events, 0)}{" "}
                              population events
                            </summary>
                            {pooledInfo?.sources.map((source) => (
                              <p key={source.sample_id}>
                                {source.sample_name}:{" "}
                                {formatNumber(source.count, 0)} of{" "}
                                {formatNumber(source.event_count, 0)} events
                              </p>
                            ))}
                            {(pooledQuery.error || pooledPopulation.error) && (
                              <p className="form-error" role="alert">
                                {
                                  (pooledQuery.error ?? pooledPopulation.error)
                                    ?.message
                                }
                              </p>
                            )}
                          </details>
                        )}
                        <div className="analysis-bottom">
                          <span>
                            <MousePointer2 size={13} />
                            {mode === "3d"
                              ? "Drag to rotate · Shift-drag to pan · Scroll to zoom · Use 3D bounds to create a volume gate."
                              : tool === "inspect"
                                ? "Double-click a visible gate to edit its shape, or draw gates with the tools above."
                                : tool === "polygon"
                                  ? "Click to add vertices. Double-click or press Enter to finish."
                                  : tool === "freehand"
                                    ? "Click and trace to the start ring, or drag and release. Click or press Enter to finish; Escape cancels."
                                    : tool === "autogate"
                                      ? "Click inside a density region. Adjust coverage and smoothing, review the count, then use the outline. Escape cancels."
                                      : tool === "quadrant"
                                        ? "Click to place the shared quadrant thresholds."
                                        : tool === "spider"
                                          ? "Click to place the shared spider center; adjust the four arms in the gate editor."
                                          : tool === "curly"
                                            ? "Click to place the shared curly center; review noise coefficients and all four counts in the gate editor."
                                            : tool === "bisector"
                                              ? "Click to split the histogram into linked negative and positive populations."
                                              : `Drag on the plot to ${tool === "zoom" ? "zoom into a region" : tool === "pan" ? "pan the view" : `draw a ${tool} gate`}.`}
                          </span>
                          <div>
                            <select
                              aria-label="Backgate population"
                              disabled={plotDraft}
                              value={backgate ?? ""}
                              onChange={(e) =>
                                setBackgate(e.target.value || null)
                              }
                            >
                              <option value="">Backgate…</option>
                              {gates.map((g) => (
                                <option key={g.id} value={g.id}>
                                  {g.name}
                                </option>
                              ))}
                            </select>
                            <button
                              className="text-button"
                              onClick={() => setModal("event-export")}
                            >
                              <ArrowDownToLine size={14} />
                              Export events
                            </button>
                            {pooledView && (
                              <button
                                className="text-button"
                                onClick={() => void analyzePooled()}
                              >
                                <Network size={14} />
                                Analyze pooled group
                              </button>
                            )}
                            <button
                              className="text-button"
                              title={
                                gates.some((value) => value.magnetic)
                                  ? "Export resolved positions as static gates. Portable projects preserve automatic following."
                                  : "Export this sample's gate strategy"
                              }
                              onClick={() =>
                                download(
                                  `/workspaces/${workspace.id}/samples/${sample.id}/export/gatingml`,
                                  `${sample.name}.gates.xml`,
                                ).catch((err) => notify(err.message, true))
                              }
                            >
                              <FileText size={14} />
                              {gates.some((value) => value.magnetic)
                                ? "Export GatingML snapshot"
                                : "Export GatingML"}
                            </button>
                          </div>
                        </div>
                        <section className="panel population-summary">
                          <div className="panel-heading">
                            <h3>Child populations</h3>
                            <span className="muted">
                              Frequency of selected parent
                            </span>
                          </div>
                          {gates.filter(
                            (g) => g.parent_id === (gate?.id ?? null),
                          ).length ? (
                            <div className="child-populations">
                              {gates
                                .filter(
                                  (g) => g.parent_id === (gate?.id ?? null),
                                )
                                .map((g) => {
                                  const stats = countsQuery.data?.find(
                                    (c) => c.id === g.id,
                                  );
                                  return (
                                    <button
                                      key={g.id}
                                      onClick={() => selectGate(g)}
                                      onDoubleClick={() =>
                                        void openPlotWindow(g, sample, false)
                                      }
                                    >
                                      <span
                                        className="gate-dot"
                                        style={{ backgroundColor: g.color }}
                                      />
                                      <div>
                                        <strong>{g.name}</strong>
                                        <span>
                                          {formatNumber(stats?.count, 0)} events
                                        </span>
                                      </div>
                                      <div className="child-frequency">
                                        <strong>
                                          {percentage(stats?.percent_parent)}
                                        </strong>
                                        <div className="frequency-track">
                                          <i
                                            style={{
                                              width: `${stats?.percent_parent ?? 0}%`,
                                              backgroundColor: g.color,
                                            }}
                                          />
                                        </div>
                                      </div>
                                      <ChevronRight size={15} />
                                    </button>
                                  );
                                })}
                            </div>
                          ) : (
                            <div className="no-children">
                              <GitBranch size={19} />
                              <span>
                                Draw a gate to define a child population.
                              </span>
                            </div>
                          )}
                        </section>
                      </div>
                      {inspector && !missingPlotInput && !inlineGateEdit && (
                        <GateInspector
                          gate={gate}
                          count={count}
                          sample={sample}
                          workspace={workspace}
                          pooled={pooledView}
                          pooledInfo={pooledQuery.data}
                          onEdit={() => {
                            if (gate) {
                              setEditingGate(true);
                              setDraftGate(gate);
                            }
                          }}
                          onDelete={() => setModal("remove-gate")}
                          onBoolean={() => {
                            const draft = makeGate(
                              sample.id,
                              gate?.id ?? null,
                              "boolean",
                              xc,
                              null,
                            );
                            draft.name = "Combined population";
                            setEditingGate(false);
                            setDraftGate(draft);
                          }}
                          onApply={() => setModal("apply")}
                          onDerived={() => setModal("derived")}
                          onCapture={
                            !pooledView && !busy && !plotDraft && !modal
                              ? () => setModal("capture-population")
                              : undefined
                          }
                        />
                      )}
                    </div>
                  ))}
                {tab === "discovery" && (
                  <AnalysisPanel
                    key={pooledAnalysis?.key ?? workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    initialInputs={pooledAnalysis?.inputs}
                    initialParameters={pooledAnalysis?.channels}
                    commit={commit}
                    busy={!!busy}
                    onExplore={(result, sid) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (!target) return;
                      const sourceParent =
                        result.request.inputs.find((i) => i.sample_id === sid)
                          ?.gate_id ?? null;
                      const viewParent =
                        result.request.compensated === false
                          ? (workspace.gates.find(
                              (g) =>
                                g.sample_id === sid &&
                                g.provenance?.analysis_id === result.id &&
                                g.provenance?.source_gate_id === sourceParent &&
                                g.provenance?.coordinate_basis === "acquired",
                            )?.id ?? null)
                          : sourceParent;
                      selectSample(target, viewParent);
                      setX(
                        result.request.algorithm === "flowsom"
                          ? result.columns[1]
                          : result.columns[0],
                      );
                      setY(result.columns[1] ?? result.columns[0]);
                      setMode(
                        result.request.algorithm === "flowsom" ||
                          result.request.algorithm === "phenograph"
                          ? "histogram"
                          : "density",
                      );
                    }}
                  />
                )}
                {tab === "quality" && (
                  <QualityPanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    commit={commit}
                    busy={!!busy}
                    onExplore={(sid, gid) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (target) {
                        selectSample(target, gid);
                        setTab("analysis");
                      }
                    }}
                  />
                )}
                {tab === "biology" && (
                  <CellCyclePanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    commit={commit}
                    busy={!!busy}
                    onExplore={(sid, gid) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (target) {
                        selectSample(target, gid);
                        setTab("analysis");
                      }
                    }}
                  />
                )}
                {tab === "proliferation" && (
                  <ProliferationPanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    commit={commit}
                    busy={!!busy}
                    onExplore={(sid, gid) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (target) {
                        selectSample(target, gid);
                        setTab("analysis");
                      }
                    }}
                  />
                )}
                {tab === "kinetics" && (
                  <KineticsPanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    commit={commit}
                    busy={!!busy}
                    onExplore={(sid, gid, time, signal) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (target) {
                        selectSample(target, gid);
                        setX(time);
                        setY(signal);
                        setMode("density");
                        setTab("analysis");
                      }
                    }}
                  />
                )}
                {tab === "comparison" && (
                  <PopulationComparisonPanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    gate={gate}
                    commit={commit}
                    busy={!!busy}
                  />
                )}
                {tab === "samples" && (
                  <SamplesPanel
                    workspace={workspace}
                    samples={visibleSamples}
                    selected={selected}
                    setSelected={setSelected}
                    onAnalyze={(s) => selectSample(s)}
                    onEdit={(s) => {
                      setEditSample(s);
                      setModal("sample");
                    }}
                    onRemove={(s) => {
                      setEditSample(s);
                      setModal("remove-sample");
                    }}
                    onGroup={() => setModal("group")}
                    onConcatenate={() => setModal("concatenate")}
                    onHarmonize={() => setModal("channel-aliases")}
                    onOrigins={(s) => {
                      setEditSample(s);
                      setModal("origins");
                    }}
                    onImport={() => importInput.current?.click()}
                  />
                )}
                {tab === "statistics" && (
                  <StatisticsPanel
                    key={workspace.id}
                    workspace={workspace}
                    groupId={groupId}
                    commit={commit}
                    busy={!!busy}
                    onOpen={(sid, gid) => {
                      const s = workspace.samples.find((s) => s.id === sid);
                      if (s) selectSample(s, gid);
                    }}
                  />
                )}
                {tab === "compensation" && (
                  <CompensationPanel
                    key={workspace.id}
                    workspace={workspace}
                    sample={sample}
                    commit={commit}
                    busy={!!busy}
                  />
                )}
                {tab === "plates" && (
                  <PlatePanel
                    key={workspace.id}
                    workspace={workspace}
                    groupId={groupId}
                    commit={commit}
                    busy={!!busy}
                    onOpen={(sid) => {
                      const target = workspace.samples.find(
                        (s) => s.id === sid,
                      );
                      if (target) {
                        selectSample(target);
                        setTab("analysis");
                      }
                    }}
                  />
                )}
                {tab === "layouts" && (
                  <LayoutPanel
                    key={workspace.id}
                    workspace={workspace}
                    current={currentPlot}
                    printRequest={reportPrintRequest}
                    commit={commit}
                    busy={!!busy}
                  />
                )}
              </main>
            </div>
            <footer className="statusbar">
              <span>
                <Check size={12} />
                {busy || "All changes saved locally"}
              </span>
              <button onClick={() => setModal("history")}>
                Revision {workspace.revision}
                <span>·</span>History
              </button>
              <div className="statusbar-spacer" />
              <span>
                {sample?.source === "Synthetic demo"
                  ? "SYNTHETIC DATA"
                  : "OFFLINE READY"}
              </span>
              <span className="statusbar-version">CytoForge 0.1</span>
            </footer>
          </>
        )
      )}
      {busy && (
        <div className="busy-indicator" role="status">
          <LoaderCircle size={16} className="spin" />
          {busy}…
        </div>
      )}
      {toast && (
        <Toast
          message={toast.message}
          error={toast.error}
          close={() => setToast(null)}
        />
      )}
      {modal === "capture-population" && workspace && sample && (
        <PopulationSnapshotDialog
          sample={sample}
          gate={gate}
          commit={commit}
          onClose={closeModal}
          onSaved={setGateId}
        />
      )}
      {draftGate && workspace && !inlineGateEdit && (
        <GateDialog
          key={draftGate.id}
          gate={draftGate}
          workspace={workspace}
          existing={editingGate}
          busy={!!busy}
          onClose={closeGate}
          onSave={saveGate}
          baseRevision={gateRevision}
          pooledScope={pooledScope}
          onRebase={() => setGateRevision(workspace.revision)}
        />
      )}
      {modal === "interchange" && workspace && (
        <InterchangeDialog
          workspace={workspace}
          onClose={closeModal}
          onApply={(doc) => {
            cache.setQueryData(["workspace", doc.id], doc);
            cache.invalidateQueries({ queryKey: ["workspaces"] });
            notify(
              `${doc.interchanges.at(-1)?.gate_ids.length ?? 0} gates imported`,
            );
          }}
        />
      )}
      {modal === "new" && (
        <Modal
          title="New experiment"
          subtitle="A local workspace for your samples, populations, and reports."
          onClose={closeModal}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              perform("Creating workspace", async () => {
                const doc = await post<Workspace>("/workspaces", {
                  name: newName.trim(),
                });
                await openWorkspace(doc);
                closeModal();
              }).catch(() => {});
            }}
          >
            <label className="field">
              Experiment name
              <input
                required
                maxLength={160}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </label>
            <div className="modal-footer">
              <button className="button" type="button" onClick={closeModal}>
                Cancel
              </button>
              <button
                className="button primary"
                disabled={!!busy || !newName.trim()}
              >
                <Plus size={16} />
                Create workspace
              </button>
            </div>
          </form>
        </Modal>
      )}
      {modal === "group" && workspace && (
        <GroupDialog
          workspace={workspace}
          selected={selected}
          busy={!!busy}
          commit={commit}
          onClose={closeModal}
        />
      )}
      {modal === "sample" && editSample && (
        <SampleDialog
          sample={editSample}
          busy={!!busy}
          commit={commit}
          onClose={closeModal}
        />
      )}
      {modal === "apply" && workspace && sample && (
        <ApplyDialog
          workspace={workspace}
          source={sample}
          targets={selected.length ? selected : visibleSamples.map((s) => s.id)}
          busy={!!busy}
          commit={commit}
          onClose={closeModal}
        />
      )}
      {axisEditor !== null && workspace && sample && (
        <AxisDefinitionDialog
          key={axisEditor}
          axis={["X", "Y", "Z", "Color", "Size"][axisEditor]}
          workspace={workspace}
          sample={sample}
          pooledScope={pooledScope}
          value={(() => {
            const names = [
              xc?.name,
              yc?.name,
              zc?.name,
              threeD.color_by,
              threeD.size_by,
            ];
            const name = names[axisEditor] ?? sample.channels[0].name;
            const explicit = [
              xDimension,
              yDimension,
              threeD.z_dimension,
              threeD.color_dimension,
              threeD.size_dimension,
            ][axisEditor];
            const base =
              explicit ?? dimensionForAxis(coordinateGate, name, axisEditor);
            const transform = [
              xTransform,
              yTransform,
              threeD.z_transform,
              threeD.color_transform,
              threeD.size_transform,
            ][axisEditor];
            return {
              channel: name,
              compensation_ref:
                mode === "3d" &&
                threeD.compensation === "uncompensated" &&
                !explicit
                  ? "uncompensated"
                  : "sample",
              minimum: null,
              maximum: null,
              ratio_channels: null,
              ratio_a: 1,
              ratio_b: 0,
              ratio_c: 0,
              transform:
                sample.channels.find((c) => c.name === name)?.transform ??
                linear,
              ...base,
              ...(mode === "3d" &&
              threeD.compensation === "uncompensated" &&
              !explicit
                ? { compensation_ref: "uncompensated" }
                : {}),
              ...(transform ? { transform } : {}),
            };
          })()}
          onClose={() => setAxisEditor(null)}
          onApply={(dimension) => {
            if (axisEditor === 0) {
              setX(dimension.channel);
              setXDimension(dimension);
              setXTransform(null);
            } else if (axisEditor === 1) {
              setY(dimension.channel);
              setYDimension(dimension);
              setYTransform(null);
            } else {
              const names = ["z", "color_by", "size_by"] as const;
              const definitions = [
                "z_dimension",
                "color_dimension",
                "size_dimension",
              ] as const;
              const transforms = [
                "z_transform",
                "color_transform",
                "size_transform",
              ] as const;
              setThreeD((v) => ({
                ...v,
                [names[axisEditor - 2]]: dimension.channel,
                [definitions[axisEditor - 2]]: dimension,
                [transforms[axisEditor - 2]]: null,
                ...(axisEditor === 3
                  ? { color_bounds: null }
                  : axisEditor === 4
                    ? { size_bounds: null }
                    : {}),
              }));
            }
            setPlotBounds(null);
          }}
        />
      )}
      {modal === "concatenate" && workspace && (
        <ConcatenateDialog
          workspace={workspace}
          selected={selected}
          onClose={closeModal}
          onCreated={(doc) => {
            cache.setQueryData(["workspace", doc.id], doc);
            cache.invalidateQueries({ queryKey: ["workspaces"] });
            setSelected(
              doc.samples
                .filter(
                  (s) =>
                    !workspace.samples.some((previous) => previous.id === s.id),
                )
                .map((s) => s.id),
            );
            notify("Merged samples created");
          }}
        />
      )}
      {modal === "channel-aliases" && workspace && (
        <ChannelAliasesDialog
          workspace={workspace}
          selected={selected}
          onClose={closeModal}
          onApplied={(doc) => {
            cache.setQueryData(["workspace", doc.id], doc);
            cache.invalidateQueries({ queryKey: ["workspaces"] });
            notify("Panel channel aliases saved");
          }}
        />
      )}
      {modal === "event-export" && workspace && sample && (
        <EventExportDialog
          workspace={workspace}
          sample={sample}
          gateId={gate?.id ?? null}
          pooledScope={pooledScope}
          onClose={closeModal}
          onSaved={() => notify("Event file saved")}
        />
      )}
      {modal === "origins" && workspace && editSample && (
        <ConcatenationOrigins
          workspace={workspace}
          sample={editSample}
          onClose={closeModal}
        />
      )}
      {modal === "derived" && workspace && sample && (
        <DerivedDialog
          workspace={workspace}
          sample={sample}
          commit={commit}
          busy={!!busy}
          onClose={closeModal}
        />
      )}
      {modal === "transform" && workspace && sample && (
        <TransformDialog
          workspace={workspace}
          sample={sample}
          channelName={transformChannel}
          busy={!!busy}
          commit={async (...args) => {
            const doc = await commit(...args);
            if (transformChannel === xc?.name) setXTransform(null);
            if (transformChannel === yc?.name) setYTransform(null);
            setThreeD((v) => ({
              ...v,
              z_transform: transformChannel === v.z ? null : v.z_transform,
              color_transform:
                transformChannel === v.color_by ? null : v.color_transform,
              size_transform:
                transformChannel === v.size_by ? null : v.size_transform,
            }));
            return doc;
          }}
          onClose={closeModal}
        />
      )}
      {modal === "remove-gate" && workspace && gate && (
        <Modal
          title={
            gate.partition
              ? `Delete linked ${gate.partition.kind} populations?`
              : `Delete ${gate.name}?`
          }
          subtitle={
            gate.partition
              ? "This removes every population in the linked family, their descendants, and dependent boolean gates. You can undo this change."
              : "This removes the population, its descendants, and boolean gates that depend on it. You can undo this change."
          }
          onClose={closeModal}
        >
          <div className="modal-footer">
            <button className="button" onClick={closeModal}>
              Cancel
            </button>
            <button
              className="button danger-button"
              disabled={!!busy || plotDraft}
              onClick={() =>
                perform("Deleting population", async () => {
                  if (pooledScope) {
                    await new Promise<void>((resolve, reject) =>
                      setPooledGateProposal({
                        scope: structuredClone(pooledScope),
                        revision: workspace.revision,
                        action: "delete",
                        gates: [gate],
                        resolve,
                        reject,
                      }),
                    );
                    setGateId(gate.parent_id);
                    closeModal();
                    notify("Group populations deleted");
                    return;
                  }
                  const doc = await api<Workspace>(
                    `/workspaces/${workspace.id}/gates/${gate.id}?revision=${workspace.revision}`,
                    { method: "DELETE" },
                  );
                  cache.setQueryData(["workspace", doc.id], doc);
                  setGateId(gate.parent_id);
                  closeModal();
                  notify("Population deleted");
                }).catch(() => {})
              }
            >
              {gate.partition
                ? "Delete linked populations"
                : "Delete population"}
            </button>
          </div>
        </Modal>
      )}
      {modal === "remove-sample" && workspace && editSample && (
        <Modal
          title={`Remove ${editSample.name}?`}
          subtitle="The sample and its populations will leave the workspace. Event data is retained so you can undo this change."
          onClose={closeModal}
        >
          <div className="modal-footer">
            <button className="button" onClick={closeModal}>
              Cancel
            </button>
            <button
              className="button danger-button"
              disabled={!!busy || plotDraft}
              onClick={() =>
                perform("Removing sample", async () => {
                  const doc = await api<Workspace>(
                    `/workspaces/${workspace.id}/samples/${editSample.id}?revision=${workspace.revision}`,
                    { method: "DELETE" },
                  );
                  cache.setQueryData(["workspace", doc.id], doc);
                  cache.invalidateQueries({ queryKey: ["workspaces"] });
                  closeModal();
                  notify("Sample removed");
                }).catch(() => {})
              }
            >
              Remove sample
            </button>
          </div>
        </Modal>
      )}
      {modal === "history" && (
        <Modal
          title="Workspace history"
          subtitle="Every persisted edit has a snapshot. Undo and redo survive restarts."
          onClose={closeModal}
        >
          <div className="history-list">
            {historyQuery.data?.entries
              .slice()
              .reverse()
              .map((entry) => (
                <div
                  key={entry.seq}
                  className={
                    entry.seq === historyQuery.data?.cursor ? "current" : ""
                  }
                >
                  <span className="history-dot" />
                  <div>
                    <strong>{entry.label}</strong>
                    <small>{new Date(entry.at).toLocaleString()}</small>
                  </div>
                  {entry.seq === historyQuery.data?.cursor && (
                    <Tag>Current</Tag>
                  )}
                </div>
              ))}
          </div>
          <div className="modal-footer">
            <button
              className="button"
              disabled={!!busy || !historyQuery.data?.can_undo}
              onClick={() => moveHistory("undo")}
            >
              <ArrowLeft size={15} />
              Undo
            </button>
            <button
              className="button"
              disabled={!!busy || !historyQuery.data?.can_redo}
              onClick={() => moveHistory("redo")}
            >
              Redo
              <ArrowRight size={15} />
            </button>
          </div>
        </Modal>
      )}
      {importProgress && (
        <ImportProgress session={importProgress} cancel={cancelImport} />
      )}
      {modal === "import-result" && importResult && (
        <Modal
          title={
            importResult.cancelled
              ? "Import cancelled"
              : `${importResult.imported} ${importResult.imported === 1 ? "sample" : "samples"} imported`
          }
          subtitle={
            importResult.cancelled
              ? "No samples were added from this batch."
              : "Each FCS dataset becomes a sample. Successful files are retained when another file fails."
          }
          onClose={closeModal}
        >
          {!importResult.cancelled &&
            !importResult.errors.length &&
            !importResult.warnings.length && (
              <div className="import-success">
                <Check size={28} />
                <p>Your data is ready for analysis.</p>
              </div>
            )}
          {importResult.datasets.filter((dataset) => !dataset.skipped).length >
            0 && (
            <div className="sample-import-results">
              {importResult.datasets
                .filter((dataset) => !dataset.skipped)
                .map((dataset) => (
                  <div key={dataset.sample_id}>
                    <strong>{dataset.name}</strong>
                    <span>
                      {formatNumber(dataset.event_count, 0)} events ·{" "}
                      {dataset.channel_count} parameters
                    </span>
                  </div>
                ))}
            </div>
          )}
          {importResult.errors.map((e, i) => (
            <div className="import-message error" key={`e${i}`}>
              <strong>{e.file}</strong>
              <p>{e.message}</p>
            </div>
          ))}
          {importResult.warnings.map((e, i) => (
            <div className="import-message" key={`w${i}`}>
              <strong>{e.file}</strong>
              <p>{e.message}</p>
            </div>
          ))}
          <div className="modal-footer">
            {importResult.datasets.some((dataset) => dataset.skipped) && (
              <button
                className="button"
                disabled={!!busy || plotDraft}
                title="Create new samples for every dataset in the selected files, including identical datasets"
                onClick={() => importSamples(importSelection.current, true)}
              >
                Import selection again
              </button>
            )}
            <button className="button primary" onClick={closeModal}>
              Continue
            </button>
          </div>
        </Modal>
      )}
      {modal === "help" && (
        <Modal
          title="A precise, local workspace"
          subtitle="CytoForge · cytometry workbench"
          onClose={closeModal}
        >
          <p className="form-note">
            Import FCS/CSV data, select a sample, and draw gates. Every
            population count uses the complete event matrix. Display transforms
            and compensation update live.
          </p>
          <div className="shortcut-list">
            {tools.map((t) => (
              <div key={t.key}>
                <span>{t.name}</span>
                <kbd>{t.shortcut}</kbd>
              </div>
            ))}
            <div>
              <span>Search samples</span>
              <kbd>Ctrl / ⌘ K</kbd>
            </div>
            <div>
              <span>Undo / Redo</span>
              <kbd>Ctrl / ⌘ [Shift] Z</kbd>
            </div>
          </div>
          <p className="form-note">
            This is an actively developed 0.1 workbench. Specialized biological
            models, advanced embeddings, FlowJo workspace interoperability, and
            verified installers on every platform are still in development.
          </p>
          <div className="modal-footer">
            <button className="button primary" onClick={closeModal}>
              Got it
            </button>
          </div>
        </Modal>
      )}
      {pooledGateProposal && workspace && (
        <PooledGateReview
          workspace={workspace}
          proposal={pooledGateProposal}
          onClose={() => setPooledGateProposal(null)}
          onApplied={(doc) => {
            cache.setQueryData(["workspace", doc.id], doc);
            void cache.invalidateQueries({ queryKey: ["workspaces"] });
            notify("Group populations applied; Undo restores the whole change");
          }}
        />
      )}
    </div>
  );
}
