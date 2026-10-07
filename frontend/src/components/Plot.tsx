import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent,
  type MouseEvent,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Download, RotateCcw, X } from "lucide-react";
import { api, params } from "../api";
import { backgateSummary, drawBackgatePoints } from "../backgates";
import {
  canvasGraphText,
  graphTextCSS,
  nativePlotPadding,
  scientificGraphOptions,
} from "../graphTypography";
import { strokeAndFillGate } from "../graphGateStyle";
import { useGraphFonts } from "../useGraphFonts";
import type {
  Gate,
  GateDimension,
  GateDrawingKind,
  GraphOptions,
  PlotData,
  Sample,
  ThreeDView,
  Channel,
  Transform,
} from "../types";
import { axisFraction, axisValue, densityColor } from "../graph";
import { channelLabel, formatNumber } from "../types";
import { ErrorState, Loading } from "./Common";
import { GateShapeOverlay } from "./GateShapeOverlay";
import {
  AutomaticGateDraft,
  type AutomaticGateSource,
} from "./AutomaticGateDraft";
import {
  FreehandTrace,
  FREEHAND_LIMIT,
  FREEHAND_SPACING,
  type TracePoint,
} from "../freehand";

export type Tool =
  | "inspect"
  | "rectangle"
  | "ellipse"
  | "polygon"
  | "freehand"
  | "autogate"
  | "range"
  | "bisector"
  | "quadrant"
  | "spider"
  | "curly"
  | "zoom"
  | "pan";
export interface PlotProps {
  workspaceId: string;
  revision: number;
  sample: Sample;
  gateId: string | null;
  pooledScope?: import("../types").PooledScope;
  x: string;
  y: string | null;
  mode?: string;
  graphOptions?: GraphOptions;
  bins?: number;
  tool?: Tool;
  backgateId?: string | null;
  coordinateGateId?: string | null;
  onDraw?: (kind: GateDrawingKind, geometry: Partial<Gate>) => void;
  onEditGate?: (gateId: string) => void;
  onReady?: (ready: boolean) => void;
  light?: boolean;
  initialBounds?: number[] | null;
  onBoundsChange?: (bounds: number[] | null) => void;
  threeD?: ThreeDView;
  onThreeDChange?: (view: ThreeDView) => void;
  plotParameters?: Channel[];
  xTransform?: Transform | null;
  yTransform?: Transform | null;
  xDimension?: GateDimension | null;
  yDimension?: GateDimension | null;
  onDraftChange?: (dirty: boolean) => void;
  drawingReset?: number;
  previewPayload?: PlotData;
  shapeEditor?: {
    gate: Gate;
    disabled: boolean;
    onChange: (gate: Gate) => void;
    onActiveChange?: (active: boolean) => void;
  };
}
const Plot3D = lazy(() => import("./Plot3D"));

export function Plot(props: PlotProps) {
  return props.mode === "3d" ? (
    <Suspense fallback={<Loading label="Loading 3D viewer" />}>
      <Plot3D {...props} />
    </Suspense>
  ) : (
    <PlanarPlot {...props} />
  );
}

function PlanarPlot({
  workspaceId,
  revision,
  sample,
  gateId,
  pooledScope,
  x,
  y,
  mode = "density",
  graphOptions = {},
  bins = 160,
  tool = "inspect",
  backgateId,
  coordinateGateId,
  onDraw,
  onEditGate,
  onReady,
  light = false,
  initialBounds = null,
  onBoundsChange,
  xTransform,
  yTransform,
  xDimension,
  yDimension,
  onDraftChange,
  drawingReset = 0,
  previewPayload,
  shapeEditor,
}: PlotProps) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 600, height: 370 });
  const [localBounds, setLocalBounds] = useState<number[] | null>(
    initialBounds,
  );
  const bounds = onBoundsChange ? initialBounds : localBounds;
  const setBounds = (value: number[] | null) => {
    if (
      value &&
      (value.length !== (y ? 4 : 2) ||
        !value.every(Number.isFinite) ||
        value[0] >= value[1] ||
        (y && value[2] >= value[3]))
    )
      return;
    onBoundsChange ? onBoundsChange(value) : setLocalBounds(value);
  };
  const [points, setPoints] = useState<[number, number][]>([]);
  const freehand = useRef<{
    trace: FreehandTrace;
    width: number;
    height: number;
    pointer: number | null;
  } | null>(null);
  const [traceError, setTraceError] = useState("");
  const [automatic, setAutomatic] = useState<AutomaticGateSource | null>(null);
  const [traceResized, setTraceResized] = useState(false);
  const [drag, setDrag] = useState<{
    start: [number, number];
    end: [number, number];
  } | null>(null);
  const query = useQuery({
    enabled: !previewPayload,
    queryKey: [
      "plot",
      workspaceId,
      revision,
      sample.id,
      pooledScope,
      gateId,
      x,
      y,
      mode,
      scientificGraphOptions(graphOptions),
      bins,
      bounds,
      backgateId,
      coordinateGateId,
      xTransform,
      yTransform,
      xDimension,
      yDimension,
    ],
    queryFn: ({ signal }) =>
      api<PlotData>(
        `/workspaces/${workspaceId}/samples/${sample.id}/plot?${params({ x, y, pooled: pooledScope ? true : null, group_id: pooledScope?.group_id, sample_filter: pooledScope?.sample_filter, gate_id: gateId, mode, bins, graph_options: JSON.stringify(scientificGraphOptions(graphOptions)), bounds: bounds ? JSON.stringify(bounds) : null, backgate_id: backgateId, coordinate_gate_id: coordinateGateId, x_transform: xTransform ? JSON.stringify(xTransform) : null, y_transform: yTransform ? JSON.stringify(yTransform) : null, x_dimension: xDimension ? JSON.stringify(xDimension) : null, y_dimension: yDimension ? JSON.stringify(yDimension) : null })}`,
        { signal },
      ),
  });
  const data = previewPayload ?? query.data;
  const fontRevision = useGraphFonts(graphOptions);
  const typography = JSON.stringify(graphOptions.typography ?? {});
  const gateStyle = JSON.stringify(graphOptions.gate_style ?? {});
  const padding = useMemo(() => {
    if (
      !data ||
      (!graphOptions.typography?.axis_labels &&
        !graphOptions.typography?.tick_labels)
    )
      return nativePlotPadding(graphOptions);
    const context = document.createElement("canvas").getContext("2d");
    if (!context) return nativePlotPadding(graphOptions);
    context.font = canvasGraphText(graphOptions, "tick_labels", 11, "").font;
    const labels = y
      ? data.ticks_y
      : [0, 0.25, 0.5, 0.75, 1].map((value) => ({
          label:
            formatNumber(
              value *
                (data.mode === "cdf" ? 100 : Math.max(data.max_count || 1, 1)),
              0,
            ) + (data.mode === "cdf" ? "%" : ""),
        }));
    const width = (values: { label: string }[]) =>
      Math.max(
        0,
        ...values.map((tick) => context.measureText(tick.label).width),
      );
    return nativePlotPadding(
      graphOptions,
      width(labels),
      width(
        [data.ticks_x[0], data.ticks_x.at(-1)].filter(
          (value): value is { value: number; label: string } => !!value,
        ),
      ),
    );
  }, [data, typography, fontRevision, y]);
  const w = size.width - padding.left - padding.right,
    h = size.height - padding.top - padding.bottom;
  const fontsFit = w >= 40 && h >= 40;
  const draftCallback = useRef(onDraftChange);
  draftCallback.current = onDraftChange;
  const signature = JSON.stringify([
    workspaceId,
    sample.id,
    pooledScope,
    gateId,
    x,
    y,
    mode,
    coordinateGateId,
    backgateId,
    xTransform,
    yTransform,
    xDimension,
    yDimension,
    bounds,
    graphOptions,
    bins,
    padding,
  ]);
  type Drawing = { revision: number; signature: string; data: PlotData };
  const drawing = useRef<Drawing | null>(null);
  const [draftSnapshot, setDraftSnapshot] = useState<Drawing | null>(null);
  const draftStale =
    !!draftSnapshot &&
    (draftSnapshot.revision !== revision ||
      draftSnapshot.signature !== signature);
  const validDrawing = () =>
    fontsFit &&
    !draftStale &&
    !traceResized &&
    !!data &&
    data.revision === revision &&
    (!!previewPayload || (!query.isFetching && !query.error));
  const beginDrawing = () => {
    if (drawing.current || !data) return;
    drawing.current = { revision, signature, data };
    setDraftSnapshot(drawing.current);
    draftCallback.current?.(true);
  };
  const cancelDrawing = () => {
    drawing.current = null;
    freehand.current = null;
    setTraceError("");
    setTraceResized(false);
    setAutomatic(null);
    setDraftSnapshot(null);
    setPoints([]);
    setDrag(null);
    draftCallback.current?.(false);
  };
  const readyCallback = useRef(onReady);
  readyCallback.current = onReady;
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      if (rect.width > 0 && rect.height > 0)
        setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    cancelDrawing();
  }, [tool, drawingReset]);
  useEffect(() => () => draftCallback.current?.(false), []);
  useEffect(() => {
    const trace = freehand.current;
    if (
      trace &&
      (Math.abs(trace.width - size.width) > 0.01 ||
        Math.abs(trace.height - size.height) > 0.01)
    ) {
      setTraceResized(true);
    }
  }, [size]);
  useEffect(() => {
    if (!data || !canvas.current) return;
    const element = canvas.current,
      dpr = window.devicePixelRatio || 1;
    element.width = size.width * dpr;
    element.height = size.height * dpr;
    const context = element.getContext("2d")!;
    context.scale(dpr, dpr);
    context.fillStyle = light ? "#ffffff" : "#11171f";
    context.fillRect(0, 0, size.width, size.height);
    if (!fontsFit) return;
    const [xmin, xmax, ymin, ymax] = data.bounds;
    const px = (v: number) => padding.left + axisFraction(v, xmin, xmax) * w;
    const py = (v: number) => padding.top + h - axisFraction(v, ymin, ymax) * h;
    const cdf = data.mode === "cdf";
    const histMax = cdf ? 100 : Math.max(data.max_count || 1, 1);
    context.strokeStyle = light ? "#e7eaf0" : "#242c37";
    context.lineWidth = 0.65;
    const gridY = y
      ? data.ticks_y
      : Array.from({ length: 5 }, (_, i) => ({
          value: (histMax * i) / 4,
          label: `${formatNumber((histMax * i) / 4, 0)}${cdf ? "%" : ""}`,
        }));
    const hy = (v: number) =>
      padding.top + h - (v / histMax) * h * (cdf ? 1 : 0.9);
    data.ticks_x.forEach((tick) => {
      context.beginPath();
      context.moveTo(px(tick.value), padding.top);
      context.lineTo(px(tick.value), padding.top + h);
      context.stroke();
    });
    gridY.forEach((tick) => {
      const line = y ? py(tick.value) : hy(tick.value);
      context.beginPath();
      context.moveTo(padding.left, line);
      context.lineTo(padding.left + w, line);
      context.stroke();
    });
    context.save();
    context.beginPath();
    context.rect(padding.left, padding.top, w, h);
    context.clip();
    if (data.counts && y && data.mode !== "contour") {
      const offscreen = document.createElement("canvas");
      offscreen.width = offscreen.height = data.bins;
      const ctx = offscreen.getContext("2d")!,
        image = ctx.createImageData(data.bins, data.bins);
      const zebra = data.mode === "zebra";
      const field =
        (zebra ? data.zebra_bands : data.density_field) ?? data.counts;
      const max = zebra
        ? data.zebra_max_band || 1
        : Math.log1p(data.density_max ?? data.max_count ?? 1);
      field.forEach((count, index) => {
        if (!count) return;
        const row = Math.floor(index / data.bins),
          col = index % data.bins;
        const offset = ((data.bins - row - 1) * data.bins + col) * 4;
        const rgb = densityColor(
          (zebra ? count : Math.log1p(count)) / max,
          data.graph_options?.palette,
        );
        image.data[offset] = rgb[0];
        image.data[offset + 1] = rgb[1];
        image.data[offset + 2] = rgb[2];
        image.data[offset + 3] = 255;
      });
      ctx.putImageData(image, 0, 0);
      context.imageSmoothingEnabled = false;
      const domain = data.density_bounds ?? data.bounds;
      const crop = [
        Math.max(domain[0], xmin),
        Math.min(domain[1], xmax),
        Math.max(domain[2], ymin),
        Math.min(domain[3], ymax),
      ];
      if (crop[0] < crop[1] && crop[2] < crop[3]) {
        const sx = axisFraction(crop[0], domain[0], domain[1]) * data.bins;
        const sy =
          (1 - axisFraction(crop[3], domain[2], domain[3])) * data.bins;
        const sw =
          (axisFraction(crop[1], domain[0], domain[1]) -
            axisFraction(crop[0], domain[0], domain[1])) *
          data.bins;
        const sh =
          (axisFraction(crop[3], domain[2], domain[3]) -
            axisFraction(crop[2], domain[2], domain[3])) *
          data.bins;
        context.drawImage(
          offscreen,
          sx,
          sy,
          sw,
          sh,
          px(crop[0]),
          py(crop[3]),
          px(crop[1]) - px(crop[0]),
          py(crop[2]) - py(crop[3]),
        );
      }
    } else if (cdf && data.cdf_percent && data.edges) {
      context.beginPath();
      data.cdf_percent.forEach((value, i) => {
        if (value === null) return;
        if (i === 0) context.moveTo(px(data.edges![i]), hy(value));
        else context.lineTo(px(data.edges![i]), hy(value));
      });
      context.strokeStyle = "#38d9ba";
      context.lineWidth = 1.8;
      context.stroke();
    } else if (data.counts && !y) {
      const gradient = context.createLinearGradient(
        0,
        padding.top,
        0,
        padding.top + h,
      );
      gradient.addColorStop(0, "#38d9ba88");
      gradient.addColorStop(1, "#38d9ba10");
      context.beginPath();
      context.moveTo(padding.left, padding.top + h);
      data.counts.forEach((count, i) =>
        context.lineTo(padding.left + ((i + 0.5) / data.bins) * w, hy(count)),
      );
      context.lineTo(padding.left + w, padding.top + h);
      context.closePath();
      context.fillStyle = gradient;
      context.fill();
      context.beginPath();
      data.counts.forEach((count, i) => {
        const a = padding.left + ((i + 0.5) / data.bins) * w;
        if (i === 0) context.moveTo(a, hy(count));
        else context.lineTo(a, hy(count));
      });
      context.strokeStyle = "#38d9ba";
      context.lineWidth = 1.8;
      context.stroke();
    } else if (data.points) {
      context.fillStyle = light ? "#159985aa" : "#56cfcc99";
      data.points.forEach(([a, b]) => context.fillRect(px(a), py(b), 1.6, 1.6));
    }
    if (data.contours && data.density_bounds && y) {
      const domain = data.density_bounds;
      context.strokeStyle =
        data.mode === "zebra"
          ? light
            ? "#23344788"
            : "#eef5ff88"
          : light
            ? "#087e8b"
            : "#76e0ce";
      context.lineWidth = 0.85;
      for (const contour of data.contours) {
        context.beginPath();
        for (const path of contour.paths)
          path.forEach(([a, b], i) => {
            const x = px(axisValue(domain[0], domain[1], a)),
              y = py(axisValue(domain[2], domain[3], b));
            if (i === 0) context.moveTo(x, y);
            else context.lineTo(x, y);
          });
        context.stroke();
      }
    }
    if (data.outlier_points && y) {
      context.fillStyle = light ? "#23344799" : "#b4c8dc99";
      for (const [a, b] of data.outlier_points)
        context.fillRect(px(a), py(b), 1.5, 1.5);
    }
    if (data.backgate_points)
      drawBackgatePoints(
        context,
        data.backgate_points,
        px,
        y ? py : null,
        padding.top + h,
      );
    data.overlays.forEach((overlay) => {
      context.strokeStyle = overlay.color;
      context.lineWidth = 1.5;
      let labelX = padding.left + 8,
        labelY = padding.top + 12;
      context.beginPath();
      if (overlay.kind === "range" && overlay.bounds) {
        if (overlay.axis === "x") {
          const a = px(overlay.bounds[0]),
            b = px(overlay.bounds[1]);
          context.rect(a, padding.top, b - a, h);
          labelX = Math.min(
            Math.max(a + 6, padding.left + 8),
            padding.left + w - 130,
          );
        } else {
          const a = py(overlay.bounds[1]),
            b = py(overlay.bounds[0]);
          context.rect(padding.left, a, w, b - a);
          labelY = Math.min(
            Math.max(a + 15, padding.top + 12),
            padding.top + h - 8,
          );
        }
      } else if (["spider", "curly"].includes(overlay.kind) && y) {
        overlay.segments?.forEach((segment) =>
          segment.forEach(([a, b], i) =>
            i ? context.lineTo(px(a), py(b)) : context.moveTo(px(a), py(b)),
          ),
        );
        if (overlay.center) {
          const offset = [
            [-100, -12],
            [8, -12],
            [8, 18],
            [-100, 18],
          ][
            data.overlays
              .filter((v) => v.kind === overlay.kind)
              .findIndex((v) => v.id === overlay.id) % 4
          ];
          labelX = px(overlay.center[0]) + offset[0];
          labelY = py(overlay.center[1]) + offset[1];
        }
      } else if (overlay.vertices && y) {
        [overlay.vertices, ...(overlay.holes ?? [])].forEach((ring) => {
          ring.forEach(([a, b], i) =>
            i ? context.lineTo(px(a), py(b)) : context.moveTo(px(a), py(b)),
          );
          context.closePath();
        });
        const visible = overlay.vertices
          .map(([a, b]) => [px(a), py(b)])
          .filter(
            ([a, b]) =>
              a >= padding.left &&
              a <= padding.left + w &&
              b >= padding.top &&
              b <= padding.top + h,
          );
        if (visible.length) {
          labelX = Math.max(
            padding.left + 8,
            Math.min(...visible.map((v) => v[0])) + 6,
          );
          labelY = Math.max(
            padding.top + 14,
            Math.min(...visible.map((v) => v[1])) - 8,
          );
        }
      }
      strokeAndFillGate(
        context,
        graphOptions,
        overlay.color,
        overlay.kind === "range" && overlay.bounds
          ? {
              outer:
                overlay.axis === "x"
                  ? [
                      [px(overlay.bounds[0]), padding.top],
                      [px(overlay.bounds[1]), padding.top],
                      [px(overlay.bounds[1]), padding.top + h],
                      [px(overlay.bounds[0]), padding.top + h],
                    ]
                  : [
                      [padding.left, py(overlay.bounds[0])],
                      [padding.left + w, py(overlay.bounds[0])],
                      [padding.left + w, py(overlay.bounds[1])],
                      [padding.left, py(overlay.bounds[1])],
                    ],
              holes: [],
              bounds: [padding.left, padding.top, w, h],
            }
          : overlay.vertices?.length && y
            ? {
                outer: overlay.vertices.map(([a, b]) => [px(a), py(b)]),
                holes: (overlay.holes ?? []).map((ring) =>
                  ring.map(([a, b]) => [px(a), py(b)]),
                ),
                bounds: [padding.left, padding.top, w, h],
              }
            : undefined,
      );
      const arrow = overlay.magnetic?.arrow;
      if (arrow && overlay.magnetic && overlay.magnetic.distance > 0) {
        const point = (values: number[]) => {
          const result = [padding.left + w / 2, padding.top + h / 2];
          arrow.axes.forEach((axis, index) => {
            result[axis] = axis === 0 ? px(values[index]) : py(values[index]);
          });
          return result;
        };
        const start = point(arrow.from),
          end = point(arrow.to);
        const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
        context.beginPath();
        context.moveTo(start[0], start[1]);
        context.lineTo(end[0], end[1]);
        context.lineTo(
          end[0] - 7 * Math.cos(angle - Math.PI / 6),
          end[1] - 7 * Math.sin(angle - Math.PI / 6),
        );
        context.moveTo(end[0], end[1]);
        context.lineTo(
          end[0] - 7 * Math.cos(angle + Math.PI / 6),
          end[1] - 7 * Math.sin(angle + Math.PI / 6),
        );
        context.stroke();
      }
      if (graphOptions.gate_style?.show_labels === false) return;
      const gateText = canvasGraphText(
        graphOptions,
        "gate_labels",
        11,
        overlay.color,
        "500",
      );
      context.font = gateText.font;
      context.textAlign = "left";
      const populationLabel = overlay.pooled_source_name
        ? `${overlay.name} · ${overlay.pooled_source_name}`
        : overlay.name;
      const label = overlay.magnetic
        ? `${populationLabel} · magnetic`
        : populationLabel;
      const labelWidth = context.measureText(label).width;
      labelX = Math.max(
        padding.left + 5,
        Math.min(labelX, padding.left + w - labelWidth - 8),
      );
      context.fillStyle = light ? "#ffffffdd" : "#11171fdd";
      labelY = Math.max(padding.top + gateText.pixels, labelY);
      context.fillRect(
        labelX - 4,
        labelY - gateText.pixels - 1,
        labelWidth + 8,
        gateText.pixels * 1.4 + 2,
      );
      context.fillStyle = gateText.color;
      context.fillText(label, labelX, labelY);
    });
    context.restore();
    const tickText = canvasGraphText(
      graphOptions,
      "tick_labels",
      11,
      light ? "#687386" : "#7e8da0",
    );
    context.font = tickText.font;
    context.fillStyle = tickText.color;
    context.textAlign = "center";
    data.ticks_x.forEach((t) =>
      context.fillText(
        t.label,
        px(t.value),
        padding.top + h + Math.max(21, tickText.pixels * 1.4),
      ),
    );
    context.textAlign = "right";
    gridY.forEach((t) =>
      context.fillText(
        t.label,
        padding.left - 12,
        (y ? py(t.value) : hy(t.value)) + tickText.pixels * 0.36,
      ),
    );
    context.strokeStyle = light ? "#c6ceda" : "#3a4654";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(padding.left, padding.top);
    context.lineTo(padding.left, padding.top + h);
    context.lineTo(padding.left + w, padding.top + h);
    context.stroke();
    const axisText = canvasGraphText(
      graphOptions,
      "axis_labels",
      12,
      light ? "#233447" : "#b8c6d5",
    );
    context.fillStyle = axisText.color;
    context.font = axisText.font;
    context.textAlign = "center";
    const xc = sample.channels.find((c) => c.name === x),
      yc = sample.channels.find((c) => c.name === y);
    context.fillText(
      xc ? channelLabel(xc) : x,
      padding.left + w / 2,
      size.height - Math.max(10, axisText.pixels * 0.4),
    );
    context.save();
    context.translate(Math.max(17, axisText.pixels * 1.1), padding.top + h / 2);
    context.rotate(-Math.PI / 2);
    context.fillText(
      yc
        ? channelLabel(yc)
        : (y ?? (cdf ? "Cumulative frequency (%)" : "Event count")),
      0,
      0,
    );
    if (data.cdf_undefined_reason || data.finite_count === 0) {
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.fillStyle = light ? "#52677e" : "#9bacc0";
      context.font = "12px Inter, system-ui";
      context.fillText(
        data.cdf_undefined_reason ?? "No finite events in this population",
        padding.left + w / 2,
        padding.top + h / 2,
      );
    }
    context.restore();
  }, [
    data,
    size,
    w,
    h,
    x,
    y,
    sample,
    light,
    typography,
    gateStyle,
    fontRevision,
  ]);

  useEffect(() => {
    readyCallback.current?.(
      fontsFit &&
        !!data &&
        data.revision === revision &&
        (!!previewPayload || (!query.isFetching && !query.error)),
    );
  }, [data, query.isFetching, query.error, revision, previewPayload, fontsFit]);

  const dataPoint = (event: PointerEvent<HTMLDivElement>): [number, number] => {
    const rect = event.currentTarget.getBoundingClientRect(),
      b = data!.bounds;
    const u = Math.max(
      0,
      Math.min(1, (event.clientX - rect.left - padding.left) / w),
    );
    const v = Math.max(
      0,
      Math.min(1, (event.clientY - rect.top - padding.top) / h),
    );
    return [axisValue(b[0], b[1], u), y ? axisValue(b[2], b[3], 1 - v) : v];
  };
  const pixel = ([a, b]: [number, number]) => {
    const limits = (draftSnapshot?.data ?? data)!.bounds;
    return [
      padding.left + axisFraction(a, limits[0], limits[1]) * w,
      y
        ? padding.top + h - axisFraction(b, limits[2], limits[3]) * h
        : padding.top + b * h,
    ];
  };
  const finishPolygon = () => {
    if (points.length >= 3 && validDrawing()) {
      onDraw?.("polygon", { vertices: points });
      cancelDrawing();
    }
  };
  const finishFreehand = () => {
    const stroke = freehand.current?.trace;
    if (!stroke || !validDrawing()) return;
    if (stroke.exceeded) {
      setTraceError(
        `The outline exceeds ${FREEHAND_LIMIT.toLocaleString()} vertices. Cancel and redraw at a wider view. No partial outline will be saved.`,
      );
      return;
    }
    if (!stroke.hasArea()) {
      setTraceError("Trace an outline with an area before finishing the gate.");
      return;
    }
    onDraw?.("polygon", {
      vertices: stroke.vertices.map((point) => [...point] as TracePoint),
      provenance: {
        drawing_tool: "freehand",
        vertex_spacing_css_pixels: FREEHAND_SPACING,
      },
    });
    cancelDrawing();
  };
  const tracePoint = (
    clientX: number,
    clientY: number,
    rect: DOMRect,
  ): TracePoint => [
    Math.max(
      padding.left,
      Math.min(rect.width - padding.right, clientX - rect.left),
    ),
    Math.max(
      padding.top,
      Math.min(rect.height - padding.bottom, clientY - rect.top),
    ),
  ];
  const traceMove = (event: PointerEvent<HTMLDivElement>) => {
    const stroke = freehand.current;
    if (!stroke || !validDrawing()) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (
      Math.abs(rect.width - stroke.width) > 0.01 ||
      Math.abs(rect.height - stroke.height) > 0.01
    ) {
      setTraceResized(true);
      return;
    }
    const coalesced = event.nativeEvent.getCoalescedEvents?.();
    for (const point of coalesced?.length ? coalesced : [event.nativeEvent]) {
      const pixel = tracePoint(point.clientX, point.clientY, rect);
      if (stroke.trace.nearStart(pixel)) {
        finishFreehand();
        return;
      }
      stroke.trace.append(pixel);
    }
    setPoints([...stroke.trace.vertices]);
    if (stroke.trace.exceeded)
      setTraceError(
        `The outline exceeds ${FREEHAND_LIMIT.toLocaleString()} vertices. Cancel and redraw at a wider view. No partial outline will be saved.`,
      );
    else setTraceError("");
  };
  const editVisibleGate = (event: MouseEvent<HTMLDivElement>) => {
    if (
      !onEditGate ||
      !fontsFit ||
      tool !== "inspect" ||
      !data ||
      data.revision !== revision ||
      query.isFetching ||
      query.error
    )
      return;
    const context = canvas.current?.getContext("2d");
    if (!context) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const a = event.clientX - rect.left,
      b = event.clientY - rect.top;
    if (
      a < padding.left ||
      a > padding.left + w ||
      b < padding.top ||
      b > padding.top + h
    )
      return;
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.lineWidth = 10;
    try {
      let bodyHit: string | undefined;
      for (const overlay of [...data.overlays].reverse()) {
        const path = new Path2D();
        const holePaths: Path2D[] = [];
        if (overlay.kind === "range" && overlay.bounds) {
          if (overlay.axis === "x") {
            const lo = pixel([overlay.bounds[0], 0])[0],
              hi = pixel([overlay.bounds[1], 0])[0];
            path.rect(lo, padding.top, hi - lo, h);
          } else {
            const lo = pixel([0, overlay.bounds[1]])[1],
              hi = pixel([0, overlay.bounds[0]])[1];
            path.rect(padding.left, lo, w, hi - lo);
          }
        } else if (["spider", "curly"].includes(overlay.kind) && y) {
          for (const segment of overlay.segments ?? [])
            segment.forEach((point, i) => {
              const p = pixel(point);
              if (i) path.lineTo(p[0], p[1]);
              else path.moveTo(p[0], p[1]);
            });
        } else if (overlay.vertices && y) {
          overlay.vertices.forEach((point, i) => {
            const p = pixel(point);
            if (i) path.lineTo(p[0], p[1]);
            else path.moveTo(p[0], p[1]);
          });
          path.closePath();
          for (const ring of overlay.holes ?? []) {
            const hole = new Path2D();
            ring.forEach((point, i) => {
              const p = pixel(point);
              if (i) hole.lineTo(p[0], p[1]);
              else hole.moveTo(p[0], p[1]);
            });
            hole.closePath();
            holePaths.push(hole);
          }
        } else continue;
        if (
          context.isPointInStroke(path, a, b) ||
          holePaths.some((hole) => context.isPointInStroke(hole, a, b))
        ) {
          event.preventDefault();
          event.stopPropagation();
          onEditGate(overlay.id);
          return;
        }
        if (
          !bodyHit &&
          overlay.kind !== "spider" &&
          overlay.kind !== "curly" &&
          context.isPointInPath(path, a, b, "evenodd") &&
          !holePaths.some((hole) =>
            context.isPointInPath(hole, a, b, "evenodd"),
          )
        ) {
          bodyHit = overlay.id;
        }
      }
      if (bodyHit) {
        event.preventDefault();
        event.stopPropagation();
        onEditGate(bodyHit);
      }
    } finally {
      context.restore();
    }
  };
  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!validDrawing() || tool === "inspect" || event.button !== 0) return;
    if ((tool === "freehand" || tool === "autogate") && !y) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (
      event.clientX - rect.left < padding.left ||
      event.clientX - rect.left > padding.left + w ||
      event.clientY - rect.top < padding.top ||
      event.clientY - rect.top > padding.top + h
    )
      return;
    event.currentTarget.focus();
    if (tool === "autogate") {
      if (automatic) return;
      beginDrawing();
      setAutomatic({
        revision,
        sample_id: sample.id,
        ...(pooledScope ? { scope: pooledScope } : {}),
        parent_id: gateId,
        coordinate_gate_id: coordinateGateId ?? null,
        x,
        y: y!,
        x_transform: data!.x_transform!,
        y_transform: data!.y_transform!,
        x_dimension: data!.axes?.[0],
        y_dimension: data!.axes?.[1],
        seed: dataPoint(event),
      });
      return;
    }
    if (tool === "freehand") {
      if (freehand.current) {
        finishFreehand();
        return;
      }
      beginDrawing();
      const limits = [...data!.bounds],
        width = rect.width,
        height = rect.height;
      const trace = new FreehandTrace(
        tracePoint(event.clientX, event.clientY, rect),
        ([a, b]) => [
          axisValue(
            limits[0],
            limits[1],
            (a - padding.left) / (width - padding.left - padding.right),
          ),
          axisValue(
            limits[2],
            limits[3],
            1 - (b - padding.top) / (height - padding.top - padding.bottom),
          ),
        ],
      );
      freehand.current = { trace, width, height, pointer: event.pointerId };
      event.currentTarget.setPointerCapture(event.pointerId);
      setPoints([...trace.vertices]);
      setTraceError("");
      return;
    }
    const point = dataPoint(event);
    if (tool === "polygon") {
      beginDrawing();
      if (event.detail < 2) setPoints([...points, point]);
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    if (!["zoom", "pan"].includes(tool)) beginDrawing();
    setDrag({ start: point, end: point });
  };
  const pointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (tool === "freehand" && freehand.current) {
      const stroke = freehand.current;
      const held = stroke.pointer === event.pointerId;
      stroke.pointer = null;
      if (held && stroke.trace.length >= 24) finishFreehand();
      return;
    }
    if (!drag || !data || !validDrawing()) return;
    const end = dataPoint(event),
      start = drag.start;
    try {
      const b = [
        Math.min(start[0], end[0]),
        Math.max(start[0], end[0]),
        Math.min(start[1], end[1]),
        Math.max(start[1], end[1]),
      ];
      if (tool === "quadrant" && y) {
        onDraw?.("quadrant", { bounds: end });
        return;
      }
      if (tool === "curly" && y) {
        onDraw?.("curly", {
          curly: {
            center: end,
            coefficients: [1, 1],
            convention: "sqrt-positive-intensity-v1",
          },
        });
        return;
      }
      if (tool === "spider" && y) {
        const limits = (draftSnapshot?.data ?? data).bounds;
        const scale = [0, 1].map(
          (i) => limits[2 * i + 1] / 2 - limits[2 * i] / 2,
        );
        if (!scale.every((v) => Number.isFinite(v) && v > 0)) {
          setTraceError(
            "Zoom to a finite coordinate range before creating spider gates.",
          );
          return;
        }
        onDraw?.("spider", {
          spider: {
            center: end,
            scale: scale as [number, number],
            angles: [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2],
          },
        });
        return;
      }
      if (tool === "bisector" && !y) {
        onDraw?.("bisector", { bounds: [end[0]] });
        return;
      }
      if (Math.abs(pixel(start)[0] - pixel(end)[0]) < 4 && tool !== "pan")
        return;
      if (tool === "zoom") {
        if (y && Math.abs(pixel(start)[1] - pixel(end)[1]) < 4) return;
        setBounds(y ? b : b.slice(0, 2));
        return;
      }
      if (tool === "pan") {
        const limits = data.bounds;
        const dx =
          axisFraction(start[0], limits[0], limits[1]) -
          axisFraction(end[0], limits[0], limits[1]);
        const dy = y
          ? axisFraction(start[1], limits[2], limits[3]) -
            axisFraction(end[1], limits[2], limits[3])
          : 0;
        setBounds(
          limits.map((_, i) =>
            axisValue(
              limits[i < 2 ? 0 : 2],
              limits[i < 2 ? 1 : 3],
              (i % 2) + (i < 2 ? dx : dy),
            ),
          ),
        );
        return;
      }
      if (tool === "range") {
        onDraw?.("range", { bounds: b.slice(0, 2) });
        return;
      }
      if (!y || Math.abs(pixel(start)[1] - pixel(end)[1]) < 4) return;
      if (tool === "rectangle") onDraw?.("rectangle", { bounds: b });
      if (tool === "ellipse")
        onDraw?.("ellipse", {
          center: [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2],
          radii: [(b[1] - b[0]) / 2, (b[3] - b[2]) / 2],
        });
    } finally {
      cancelDrawing();
    }
  };
  const startPx = drag && (data || draftSnapshot) ? pixel(drag.start) : null,
    endPx = drag && (data || draftSnapshot) ? pixel(drag.end) : null;
  const box =
    startPx && endPx
      ? {
          x: Math.min(startPx[0], endPx[0]),
          y: tool === "range" ? padding.top : Math.min(startPx[1], endPx[1]),
          width: Math.abs(startPx[0] - endPx[0]),
          height: tool === "range" ? h : Math.abs(startPx[1] - endPx[1]),
        }
      : null;
  return (
    <div className={`plot-component ${light ? "light" : ""}`}>
      <div
        className={`plot-stage tool-${tool}`}
        ref={container}
        tabIndex={0}
        role="img"
        aria-label={`${mode} plot of ${x}${y ? ` versus ${y}` : ""}, ${data ? `${formatNumber(data.count, 0)} events` : "loading"}`}
        onPointerDown={pointerDown}
        onPointerMove={(e) => {
          if (tool === "freehand") traceMove(e);
          else if (drag && data && validDrawing())
            setDrag({ ...drag, end: dataPoint(e) });
        }}
        onPointerUp={pointerUp}
        onPointerCancel={cancelDrawing}
        onDoubleClick={(event) => {
          if (tool === "polygon") finishPolygon();
          else if (tool === "freehand") finishFreehand();
          else editVisibleGate(event);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget && automatic) {
            e.preventDefault();
            container.current
              ?.querySelector<HTMLButtonElement>("[data-accept-automatic-gate]")
              ?.click();
          }
          if (
            e.key === "Enter" &&
            e.target === e.currentTarget &&
            tool === "polygon"
          )
            finishPolygon();
          if (
            e.key === "Enter" &&
            e.target === e.currentTarget &&
            tool === "freehand"
          ) {
            e.preventDefault();
            finishFreehand();
          }
          if (e.key === "Escape") {
            cancelDrawing();
          }
        }}
      >
        <canvas
          ref={canvas}
          data-ready={
            !!data &&
            data.revision === revision &&
            (!!previewPayload || (!query.isFetching && !query.error))
          }
        />
        {(data || draftSnapshot) && (
          <svg
            className="draw-overlay"
            width={size.width}
            height={size.height}
            aria-hidden="true"
          >
            {box &&
              tool !== "pan" &&
              tool !== "quadrant" &&
              tool !== "spider" &&
              tool !== "curly" &&
              tool !== "bisector" &&
              (tool === "ellipse" ? (
                <ellipse
                  cx={box.x + box.width / 2}
                  cy={box.y + box.height / 2}
                  rx={box.width / 2}
                  ry={box.height / 2}
                />
              ) : (
                <rect {...box} />
              ))}
            {tool === "quadrant" && endPx && (
              <path
                d={`M ${padding.left} ${endPx[1]} H ${padding.left + w} M ${endPx[0]} ${padding.top} V ${padding.top + h}`}
              />
            )}
            {tool === "bisector" && endPx && (
              <path d={`M ${endPx[0]} ${padding.top} V ${padding.top + h}`} />
            )}
            {points.length > 0 && (
              <>
                <polyline
                  points={points.map((p) => pixel(p).join(",")).join(" ")}
                />
                {tool === "freehand" ? (
                  <circle
                    className="freehand-start"
                    cx={pixel(points[0])[0]}
                    cy={pixel(points[0])[1]}
                    r={8}
                  />
                ) : (
                  points.map((p, i) => {
                    const [a, b] = pixel(p);
                    return <circle key={i} cx={a} cy={b} r={3} />;
                  })
                )}
              </>
            )}
          </svg>
        )}
        {shapeEditor && data && (
          <GateShapeOverlay
            {...shapeEditor}
            bounds={data.bounds}
            segments={
              data.overlays.find((o) => o.id === shapeEditor.gate.id)
                ?.shared_segments
            }
            width={size.width}
            height={size.height}
          />
        )}
        {automatic && draftSnapshot && (
          <AutomaticGateDraft
            workspaceId={workspaceId}
            source={automatic}
            bounds={draftSnapshot.data.bounds}
            width={size.width}
            height={size.height}
            stale={draftStale}
            onCancel={cancelDrawing}
            onUse={(gate) => {
              if (!draftStale) {
                onDraw?.("polygon", gate);
                cancelDrawing();
              }
            }}
          />
        )}
        {query.isPending && !previewPayload && (
          <Loading label="Calculating plot" />
        )}
        {query.error && !previewPayload && (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        )}
        {query.isFetching && data && !previewPayload && (
          <span className="plot-updating">Updating…</span>
        )}
        {(draftStale || traceResized) && (
          <div className="plot-draft-notice" role="alert">
            {traceResized
              ? "The plot was resized."
              : "Workspace or plot coordinates changed."}{" "}
            This drawing is retained; cancel it and redraw against the current
            view.
          </div>
        )}
        {points.length > 0 && (
          <div
            className="polygon-actions"
            onPointerDown={(event) => event.stopPropagation()}
            onPointerMove={(event) => event.stopPropagation()}
          >
            {tool === "freehand" && (
              <span className="freehand-count">
                {points.length.toLocaleString()} /{" "}
                {FREEHAND_LIMIT.toLocaleString()} vertices
              </span>
            )}
            <button
              className="button small primary"
              disabled={
                points.length < 3 ||
                !validDrawing() ||
                !!freehand.current?.trace.exceeded
              }
              onClick={tool === "freehand" ? finishFreehand : finishPolygon}
            >
              <Check size={14} />
              {tool === "freehand" ? "Finish freehand gate" : "Finish polygon"}
            </button>
            <button
              className="icon-button"
              aria-label={
                tool === "freehand" ? "Cancel freehand gate" : "Cancel polygon"
              }
              onClick={cancelDrawing}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {tool === "freehand" && traceError && (
          <div className="freehand-error" role="alert">
            {traceError}
          </div>
        )}
      </div>
      {!fontsFit && (
        <div role="alert">
          Increase the plot window size or reduce the axis fonts to fit this
          plot.
        </div>
      )}
      <div
        className="plot-footer"
        style={graphTextCSS(graphOptions, "statistics")}
      >
        <span>
          <span className="status-dot" />
          {formatNumber(data?.count, 0)} events
          {data &&
            data.count > data.finite_count &&
            ` · ${formatNumber(data.count - data.finite_count, 0)} nonfinite excluded`}
          {data?.displayed_count != null
            ? ` · ${formatNumber(data.displayed_count, 0)} displayed`
            : data?.mode === "cdf"
              ? ` · CDF of ${formatNumber(data.cdf_denominator, 0)} finite events`
              : " · full-event bins"}
          {data?.point_sampling && " · sampled markers"}
          {backgateSummary(data, (value) => formatNumber(value, 0))}
          {data?.outlier_count != null &&
            ` · ${formatNumber(data.outlier_count, 0)} outliers; ${formatNumber(data.outlier_displayed_count, 0)} displayed`}
          {data?.outlier_sampling && " · sampled outlier markers"}
          {data?.contours_truncated && " · contour drawing limit reached"}
          {data && data.visible_count < data.finite_count && (
            <span className="dim">
              {" "}
              · {formatNumber(data.finite_count - data.visible_count, 0)}{" "}
              outside view
            </span>
          )}
        </span>
        <div>
          {data?.overlays.some((overlay) => overlay.magnetic) && (
            <button
              className="text-button"
              disabled={
                !!draftSnapshot ||
                query.isFetching ||
                !!query.error ||
                data.revision !== revision
              }
              onClick={() => {
                const limits = [...data.bounds];
                const include = (axis: number, value: number) => {
                  limits[2 * axis] = Math.min(limits[2 * axis], value);
                  limits[2 * axis + 1] = Math.max(limits[2 * axis + 1], value);
                };
                for (const overlay of data.overlays.filter(
                  (value) => value.magnetic,
                )) {
                  const arrow = overlay.magnetic?.arrow;
                  if (arrow)
                    arrow.axes.forEach((axis, i) => {
                      include(axis, arrow.from[i]);
                      include(axis, arrow.to[i]);
                    });
                  overlay.vertices?.forEach(([a, b]) => {
                    include(0, a);
                    include(1, b);
                  });
                  if (overlay.bounds)
                    for (const value of overlay.bounds)
                      include(overlay.axis === "y" ? 1 : 0, value);
                }
                setBounds(
                  limits.map((_, i) =>
                    axisValue(
                      limits[Math.floor(i / 2) * 2],
                      limits[Math.floor(i / 2) * 2 + 1],
                      i % 2 ? 1.04 : -0.04,
                    ),
                  ),
                );
              }}
            >
              Fit magnetic positions
            </button>
          )}
          {bounds && (
            <button
              className="text-button"
              disabled={!!draftSnapshot}
              onClick={() => setBounds(null)}
            >
              <RotateCcw size={13} />
              Reset zoom
            </button>
          )}
          <button
            className="icon-button"
            aria-label="Export plot as PNG"
            title="Export plot as PNG"
            disabled={
              !data ||
              query.isFetching ||
              !!query.error ||
              data.revision !== revision
            }
            onClick={() =>
              canvas.current?.toBlob((blob) => {
                if (blob) {
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `${sample.name}-${x}.png`;
                  a.click();
                  setTimeout(() => URL.revokeObjectURL(url), 30000);
                }
              })
            }
          >
            <Download size={14} />
          </button>
        </div>
      </div>
      {!!data?.probability_levels?.length && (
        <details className="probability-audit">
          <summary>
            Probability coverage ·{" "}
            {formatNumber(data.probability_denominator, 0)} finite events
          </summary>
          <div>
            <p>{data.contour_geometry}</p>
            <table>
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Estimated mass</th>
                  <th>Binned events</th>
                  <th>Tied bins</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.probability_levels.map((level) => (
                  <tr key={level.probability}>
                    <td>{(level.probability * 100).toFixed(2)}%</td>
                    <td>
                      {level.estimated_probability == null
                        ? "—"
                        : `${(level.estimated_probability * 100).toFixed(2)}%`}
                    </td>
                    <td>
                      {level.binned_event_probability == null
                        ? "—"
                        : `${(level.binned_event_probability * 100).toFixed(2)}%`}
                    </td>
                    <td>{formatNumber(level.tied_bins, 0)}</td>
                    <td>{level.reason ?? "Available"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
