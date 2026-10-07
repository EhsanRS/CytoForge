import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Box, Download, RotateCcw } from "lucide-react";
import { api, binaryApi, params, saveBlob } from "../api";
import { backgateSummary } from "../backgates";
import { CloudScene, defaultCamera } from "../scene3d";
import { graphTextCSS, scientificGraphOptions } from "../graphTypography";
import { useGraphFonts } from "../useGraphFonts";
import {
  channelLabel,
  formatNumber,
  type ThreeDData,
  type ThreeDView,
} from "../types";
import { ErrorState, Loading } from "./Common";
import type { PlotProps } from "./Plot";
import { virtualDimension } from "../coordinates";

const wrap = (v: number) =>
  ((((v + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
const clamp = (v: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, v));

export default function Plot3D({
  workspaceId,
  revision,
  sample,
  gateId,
  pooledScope,
  x,
  y,
  threeD,
  onThreeDChange,
  graphOptions = {},
  backgateId,
  coordinateGateId,
  xTransform,
  yTransform,
  xDimension,
  yDimension,
  onReady,
  onDraw,
  initialBounds = null,
  onBoundsChange,
  tool = "inspect",
  plotParameters,
  onDraftChange,
  drawingReset = 0,
}: PlotProps) {
  const [localView, setLocalView] = useState<ThreeDView>(
    () =>
      threeD ?? {
        z: sample.channels[2]?.name ?? sample.channels[0]?.name ?? "",
      },
  );
  const view = onThreeDChange ? (threeD ?? localView) : localView;
  const update = (patch: Partial<ThreeDView>) => {
    const next = { ...view, ...patch };
    onThreeDChange ? onThreeDChange(next) : setLocalView(next);
  };
  const [localBounds, setLocalBounds] = useState<number[] | null>(
    initialBounds,
  );
  const bounds = onBoundsChange ? initialBounds : localBounds;
  const setBounds = (value: number[] | null) =>
    onBoundsChange ? onBoundsChange(value) : setLocalBounds(value);
  // Camera and glyph changes reuse the same revision-bound event buffers.
  const geometry = {
    z: view.z,
    z_transform: view.z_transform,
    color_by: view.color_by,
    size_by: view.size_by,
    color_transform: view.color_transform,
    size_transform: view.size_transform,
    z_dimension: view.z_dimension,
    color_dimension: view.color_dimension,
    size_dimension: view.size_dimension,
    compensation: view.compensation,
    all_events: view.all_events,
    color_bounds: view.color_bounds,
    size_bounds: view.size_bounds,
  };
  const parameters = params({
    x,
    y,
    gate_id: gateId,
    pooled: pooledScope ? "true" : null,
    group_id: pooledScope?.group_id,
    sample_filter: pooledScope?.sample_filter,
    mode: "3d",
    three_d: JSON.stringify(geometry),
    graph_options: JSON.stringify(scientificGraphOptions(graphOptions)),
    bounds: bounds ? JSON.stringify(bounds) : null,
    backgate_id: backgateId,
    coordinate_gate_id: coordinateGateId,
    x_transform: xTransform ? JSON.stringify(xTransform) : null,
    y_transform: yTransform ? JSON.stringify(yTransform) : null,
    x_dimension: xDimension ? JSON.stringify(xDimension) : null,
    y_dimension: yDimension ? JSON.stringify(yDimension) : null,
  });
  const query = useQuery({
    queryKey: ["plot3d", workspaceId, revision, sample.id, parameters],
    queryFn: ({ signal }) =>
      api<ThreeDData>(
        `/workspaces/${workspaceId}/samples/${sample.id}/plot?${parameters}`,
        { signal },
      ),
  });
  const data = query.data?.revision === revision ? query.data : undefined;
  const stage = useRef<HTMLDivElement>(null),
    canvas = useRef<HTMLCanvasElement>(null),
    overlay = useRef<HTMLCanvasElement>(null);
  const scene = useRef<CloudScene | null>(null),
    buffers = useRef<Float32Array[]>([]);
  const [version, setVersion] = useState(0),
    [loaded, setLoaded] = useState(0);
  const [streamError, setStreamError] = useState<Error | null>(null),
    [retry, setRetry] = useState(0);
  const [renderer, setRenderer] = useState("software");
  const [boxEditor, setBoxEditor] = useState(false),
    [box, setBox] = useState<string[]>([]);
  const [boxError, setBoxError] = useState("");
  const [boxSnapshot, setBoxSnapshot] = useState<{
    data: ThreeDData;
    parameters: string;
  } | null>(null);
  const boxStale =
    !!boxSnapshot &&
    (boxSnapshot.data.revision !== revision ||
      boxSnapshot.parameters !== parameters);
  const draftCallback = useRef(onDraftChange);
  draftCallback.current = onDraftChange;
  const cancelBox = () => {
    setBoxEditor(false);
    setBoxSnapshot(null);
    setBoxError("");
    draftCallback.current?.(false);
  };
  useEffect(() => {
    cancelBox();
  }, [drawingReset]);
  useEffect(() => () => draftCallback.current?.(false), []);
  const readyCallback = useRef(onReady);
  readyCallback.current = onReady;
  const current = useRef({ data, view, graphOptions });
  current.current = { data, view, graphOptions };
  const currentUpdate = useRef(update);
  currentUpdate.current = update;
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!current.current.data) return;
      event.preventDefault();
      currentUpdate.current({
        zoom: clamp(
          (current.current.view.zoom ?? 1) * Math.exp(-event.deltaY * 0.001),
          0.1,
          10,
        ),
      });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, []);
  useEffect(() => {
    if (!canvas.current || !overlay.current || !stage.current) return;
    const cloud = new CloudScene(canvas.current, overlay.current);
    scene.current = cloud;
    const draw = () => {
      if (current.current.data)
        cloud.draw(
          current.current.data,
          current.current.view,
          current.current.graphOptions,
        );
      setRenderer(cloud.kind);
    };
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        cloud.resize(width, height);
        draw();
      }
    });
    observer.observe(stage.current);
    canvas.current.addEventListener("webglcontextlost", draw);
    canvas.current.addEventListener("webglcontextrestored", draw);
    const element = canvas.current;
    return () => {
      observer.disconnect();
      element.removeEventListener("webglcontextlost", draw);
      element.removeEventListener("webglcontextrestored", draw);
      cloud.dispose();
      scene.current = null;
    };
  }, []);
  const dataKey = data?.data_key;
  useEffect(() => {
    const controller = new AbortController();
    buffers.current = [];
    scene.current?.setBuffers([]);
    setLoaded(0);
    setStreamError(null);
    readyCallback.current?.(false);
    if (!data || !dataKey) {
      setVersion((v) => v + 1);
      return () => controller.abort();
    }
    const metadata = data;
    const stream = async () => {
      let count = 0;
      for (
        let start = 0;
        start < metadata.displayed_count;
        start += metadata.chunk_events
      ) {
        const expected = Math.min(
          metadata.chunk_events,
          metadata.displayed_count - start,
        );
        const { buffer, headers } = await binaryApi(
          `/workspaces/${workspaceId}/samples/${sample.id}/plot3d/points?${parameters}&${params({ revision: metadata.revision, data_key: metadata.data_key, start, count: expected })}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        if (
          headers.get("X-CytoForge-3D-Key") !== metadata.data_key ||
          Number(headers.get("X-CytoForge-Revision")) !== metadata.revision ||
          Number(headers.get("X-CytoForge-3D-Events")) !== expected ||
          buffer.byteLength !== expected * 32
        )
          throw new Error("The 3D event stream changed. Refresh the plot.");
        const values = new Float32Array(buffer);
        for (let i = 0; i < values.length; i += 8)
          if (
            ![
              values[i],
              values[i + 1],
              values[i + 2],
              values[i + 3],
              values[i + 4],
              values[i + 7],
            ].every(Number.isFinite)
          )
            throw new Error(
              "The 3D event stream contains an invalid coordinate.",
            );
        buffers.current.push(values);
        count += expected;
        scene.current?.setBuffers(buffers.current.slice());
        setLoaded(count);
        setVersion((v) => v + 1);
      }
      setVersion((v) => v + 1);
    };
    void stream().catch((error) => {
      if (!controller.signal.aborted)
        setStreamError(
          error instanceof Error ? error : new Error(String(error)),
        );
    });
    return () => controller.abort();
  }, [dataKey, retry]);
  const ready =
    !!data &&
    loaded === data.displayed_count &&
    !query.isFetching &&
    !query.error &&
    !streamError;
  useEffect(() => {
    readyCallback.current?.(ready);
  }, [ready]);
  const fontRevision = useGraphFonts(graphOptions);
  const typography = JSON.stringify(graphOptions.typography ?? {});
  const gateStyle = JSON.stringify(graphOptions.gate_style ?? {});
  useEffect(() => {
    if (!data || !scene.current) return;
    scene.current.draw(data, view, graphOptions);
    setRenderer(scene.current.kind);
  }, [data, view, version, typography, gateStyle, fontRevision]);
  const drag = useRef<{
    x: number;
    y: number;
    view: ThreeDView;
    pan: boolean;
  } | null>(null);
  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!data || event.button > 1) return;
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      view: { ...view, pan: [...(view.pan ?? [0, 0])] },
      pan: event.shiftKey || event.button === 1 || tool === "pan",
    };
  };
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const origin = drag.current;
    if (!origin) return;
    const dx = event.clientX - origin.x,
      dy = event.clientY - origin.y;
    if (origin.pan) {
      const unit =
          4 / ((scene.current?.height ?? 380) * (origin.view.zoom ?? 1)),
        pan = origin.view.pan ?? [0, 0];
      update({
        pan: [
          clamp(pan[0] - dx * unit, -5, 5),
          clamp(pan[1] + dy * unit, -5, 5),
        ],
      });
    } else
      update({
        yaw: wrap((origin.view.yaw ?? defaultCamera.yaw) + dx * 0.008),
        pitch: clamp(
          (origin.view.pitch ?? defaultCamera.pitch) + dy * 0.008,
          -1.5,
          1.5,
        ),
      });
  };
  const cameraZoom = (factor: number) =>
    update({ zoom: clamp((view.zoom ?? 1) * factor, 0.1, 10) });
  const channels = (plotParameters ?? sample.channels).map((c) => ({
    name: c.name,
    label: channelLabel(c),
  }));
  for (const dim of data?.axes ?? [])
    if (!channels.some((c) => c.name === dim.channel))
      channels.push({
        name: dim.channel,
        label: dim.ratio_channels?.join(" / ") ?? dim.channel,
      });
  const select = (field: "color_by" | "size_by", label: string) => (
    <label>
      {label}
      <select
        aria-label={`3D ${label.toLowerCase()} parameter`}
        disabled={boxEditor}
        value={view[field] ?? ""}
        onChange={(e) =>
          update({
            [field]: e.target.value || null,
            ...(field === "color_by"
              ? {
                  color_transform: null,
                  color_bounds: null,
                  color_dimension: virtualDimension(sample, e.target.value, [
                    xDimension,
                    yDimension,
                    view.z_dimension,
                    view.color_dimension,
                    view.size_dimension,
                  ]),
                }
              : {
                  size_transform: null,
                  size_bounds: null,
                  size_dimension: virtualDimension(sample, e.target.value, [
                    xDimension,
                    yDimension,
                    view.z_dimension,
                    view.color_dimension,
                    view.size_dimension,
                  ]),
                }),
          })
        }
      >
        <option value="">Uniform</option>
        {view[field] && !channels.some((c) => c.name === view[field]) && (
          <option value={view[field]!}>{view[field]} · unavailable</option>
        )}
        {channels.map((c) => (
          <option key={c.name} value={c.name}>
            {c.label}
          </option>
        ))}
      </select>
    </label>
  );
  const openBox = () => {
    if (!data || !ready || boxEditor) return;
    setBoxSnapshot({ data, parameters });
    setBox(data.bounds.map(String));
    setBoxError("");
    setBoxEditor(true);
    draftCallback.current?.(true);
  };
  const applyBox = (gate: boolean) => {
    if (
      !boxSnapshot ||
      boxStale ||
      !ready ||
      data?.data_key !== boxSnapshot.data.data_key
    ) {
      setBoxError(
        "Workspace or plot coordinates changed. Cancel these bounds and reopen them against the current view.",
      );
      return;
    }
    const original = boxSnapshot.data;
    const limits = box.map((v) => (v.trim() === "" ? NaN : Number(v)));
    if (
      limits.length !== 6 ||
      !limits.every(Number.isFinite) ||
      [0, 2, 4].some((i) => limits[i] >= limits[i + 1])
    ) {
      setBoxError("Enter a finite minimum below the maximum for each axis.");
      return;
    }
    if (gate)
      onDraw?.("hyperrectangle", {
        x: original.x,
        y: original.y,
        bounds: [],
        dimensions: original.axes.map((dim, i) => ({
          ...dim,
          minimum: limits[i * 2],
          maximum: limits[i * 2 + 1],
        })),
      });
    else setBounds(limits);
    cancelBox();
  };
  return (
    <div className="plot-component plot-3d">
      <div className="three-d-controls">
        {select("color_by", "Color")}
        {select("size_by", "Size")}
        <label>
          Default coordinates
          <select
            aria-label="3D compensation"
            disabled={boxEditor}
            value={view.compensation ?? "coordinate"}
            onChange={(e) => {
              setBounds(null);
              update({
                compensation: e.target.value as ThreeDView["compensation"],
              });
            }}
          >
            <option value="coordinate">Sample / gate definitions</option>
            <option value="uncompensated">Raw for unspecified axes</option>
          </select>
        </label>
        <label>
          Point size
          <input
            aria-label="3D point size"
            type="range"
            min="0.5"
            max="12"
            step="0.5"
            value={view.point_size ?? 2}
            onChange={(e) => update({ point_size: Number(e.target.value) })}
          />
        </label>
        <label>
          Opacity
          <input
            aria-label="3D opacity"
            type="range"
            min="0.05"
            max="1"
            step="0.05"
            value={view.opacity ?? 0.7}
            onChange={(e) => update({ opacity: Number(e.target.value) })}
          />
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="3D show cube"
            checked={view.show_cube !== false}
            onChange={(e) => update({ show_cube: e.target.checked })}
          />
          Cube
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="3D show labels"
            checked={view.show_labels !== false}
            onChange={(e) => update({ show_labels: e.target.checked })}
          />
          Labels
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="3D all events"
            disabled={boxEditor}
            checked={view.all_events !== false}
            onChange={(e) => update({ all_events: e.target.checked })}
          />
          All events in view
        </label>
      </div>
      <div
        ref={stage}
        className="plot-stage three-d-stage"
        role="application"
        aria-label="Interactive 3D plot"
        tabIndex={0}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
        onLostPointerCapture={() => {
          drag.current = null;
        }}
        onDoubleClick={() => update(defaultCamera)}
        onKeyDown={(e) => {
          if (
            [
              "ArrowLeft",
              "ArrowRight",
              "ArrowUp",
              "ArrowDown",
              "+",
              "=",
              "-",
              "0",
            ].includes(e.key)
          )
            e.preventDefault();
          if (e.key === "ArrowLeft" || e.key === "ArrowRight")
            update({
              yaw: wrap(
                (view.yaw ?? defaultCamera.yaw) +
                  (e.key === "ArrowLeft" ? -0.1 : 0.1),
              ),
            });
          if (e.key === "ArrowUp" || e.key === "ArrowDown")
            update({
              pitch: clamp(
                (view.pitch ?? defaultCamera.pitch) +
                  (e.key === "ArrowUp" ? -0.1 : 0.1),
                -1.5,
                1.5,
              ),
            });
          if (e.key === "+" || e.key === "=") cameraZoom(1.15);
          if (e.key === "-") cameraZoom(1 / 1.15);
          if (e.key === "0") update(defaultCamera);
        }}
      >
        <canvas ref={canvas} className="three-d-gpu" aria-hidden="true" />
        <canvas
          ref={overlay}
          className="three-d-overlay"
          data-ready={ready}
          data-population-count={data?.count}
          data-finite-count={data?.finite_count}
          data-displayed-count={data?.displayed_count}
          data-loaded-count={loaded}
          data-renderer={renderer}
          aria-label="3D event cloud"
        />
        {query.isPending && <Loading label="Preparing 3D event cloud" />}
        {(query.error || streamError) && (
          <ErrorState
            error={(query.error ?? streamError)!}
            onRetry={() => {
              setRetry((v) => v + 1);
              void query.refetch();
            }}
          />
        )}
        {data && !ready && !streamError && (
          <span className="plot-updating">
            Loading {formatNumber(loaded, 0)} /{" "}
            {formatNumber(data.displayed_count, 0)} events…
          </span>
        )}
      </div>
      <div
        className="plot-footer"
        style={graphTextCSS(graphOptions, "statistics")}
      >
        <span>
          {data
            ? `${formatNumber(data.count, 0)} population · ${formatNumber(data.finite_count, 0)} finite XYZ · ${formatNumber(data.visible_count, 0)} in axes · ${formatNumber(loaded, 0)} ${view.all_events === false ? "sampled markers" : "loaded"}`
            : "3D event cloud"}{" "}
          · {renderer === "webgl2" ? "WebGL2" : "Software renderer"}
          {backgateSummary(data, (value) => formatNumber(value, 0))}
        </span>
        <div>
          <button
            className="text-button"
            disabled={!ready || boxEditor}
            onClick={openBox}
          >
            <Box size={13} />
            3D bounds / box gate
          </button>
          <button
            className="text-button"
            disabled={boxEditor}
            onClick={() => {
              setBounds(null);
              update(defaultCamera);
            }}
          >
            <RotateCcw size={13} />
            Reset 3D view
          </button>
          <button
            className="icon-button"
            aria-label="Download 3D plot image"
            disabled={!ready}
            onClick={() =>
              scene.current?.snapshot().toBlob((blob) => {
                if (blob) saveBlob(blob, `${sample.name}-3D.png`);
              })
            }
          >
            <Download size={14} />
          </button>
        </div>
      </div>
      {boxEditor && boxSnapshot && (
        <div
          className="three-d-box-editor"
          role="group"
          aria-label="3D box bounds"
        >
          {[boxSnapshot.data.x, boxSnapshot.data.y, boxSnapshot.data.z].map(
            (name, i) => (
              <fieldset key={i}>
                <legend>
                  {["X", "Y", "Z"][i]} · {name} (
                  {boxSnapshot.data.axes[i].transform.kind})
                </legend>
                {["minimum", "maximum"].map((side, j) => (
                  <label key={side}>
                    {side}
                    <input
                      aria-label={`3D ${["X", "Y", "Z"][i]} ${side}`}
                      type="number"
                      step="any"
                      value={box[i * 2 + j] ?? ""}
                      onChange={(e) =>
                        setBox((v) =>
                          v.map((n, k) =>
                            k === i * 2 + j ? e.target.value : n,
                          ),
                        )
                      }
                    />
                  </label>
                ))}
              </fieldset>
            ),
          )}
          <small>
            Bounds use the transformed coordinates shown on each axis. Gates
            evaluate every original event and include minima, exclude maxima.
          </small>
          {boxError && <p role="alert">{boxError}</p>}
          {boxStale && (
            <p role="alert">
              Workspace or plot coordinates changed. Your bounds are retained;
              cancel and reopen them before creating a gate.
            </p>
          )}
          <div className="button-row">
            <button className="button small" onClick={cancelBox}>
              Cancel
            </button>
            <button
              className="button small"
              disabled={boxStale || !ready}
              onClick={() => applyBox(false)}
            >
              Apply axis bounds
            </button>
            {onDraw && (
              <button
                className="button small primary"
                disabled={boxStale || !ready}
                onClick={() => applyBox(true)}
              >
                Create 3D box gate
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
