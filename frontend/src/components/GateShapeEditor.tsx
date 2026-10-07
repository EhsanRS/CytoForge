import { useEffect, useState } from "react";
import type { Gate, MagneticResolution, PlotData, Workspace } from "../types";
import { formatNumber } from "../types";
import { api } from "../api";
import { axisValue } from "../graph";
import { gateShape, scaleGate, type Point } from "../gateGeometry";
import { Plot } from "./Plot";

interface Preview {
  revision: number;
  count: number;
  parent_count: number;
  plot: PlotData;
  magnetic: MagneticResolution | null;
  partition_counts?: {
    id: string;
    name: string;
    member: number;
    count: number;
  }[];
}
export function GateShapeEditor({
  gate,
  workspace,
  disabled,
  onChange,
  onActiveChange,
  pooledScope,
}: {
  gate: Gate;
  workspace: Workspace;
  disabled: boolean;
  onChange: (gate: Gate) => void;
  onActiveChange: (active: boolean) => void;
  pooledScope?: import("../types").PooledScope;
}) {
  const sample = workspace.samples.find((s) => s.id === gate.sample_id);
  const oneD = gate.dimensions?.length
    ? gate.dimensions.length === 1
    : gate.kind === "range";
  const [mode, setMode] = useState(oneD ? "histogram" : "density");
  const [bounds, setBounds] = useState<number[] | null>(null);
  const [result, setResult] = useState<
    (Preview & { key: string; basis: string }) | null
  >(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const basis = JSON.stringify([
    gate.sample_id,
    gate.parent_id,
    gate.x,
    gate.y,
    gate.x_transform,
    gate.y_transform,
    gate.dimensions?.map((dim) => ({ ...dim, minimum: null, maximum: null })),
  ]);
  const key = JSON.stringify([
    gate,
    workspace.id,
    workspace.revision,
    mode,
    bounds,
    pooledScope,
  ]);
  useEffect(() => {
    setBounds(null);
    setMode(oneD ? "histogram" : "density");
  }, [basis, oneD]);
  useEffect(() => {
    if (disabled) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setError("");
      try {
        const value = await api<Preview>(
          `/workspaces/${workspace.id}/gates/preview-shape`,
          {
            method: "POST",
            signal: controller.signal,
            body: JSON.stringify({
              revision: workspace.revision,
              gate,
              mode,
              bounds,
              bins: 96,
              ...(pooledScope ? { scope: pooledScope } : {}),
            }),
          },
        );
        if (controller.signal.aborted) return;
        setResult({ ...value, key, basis });
        if (!bounds) setBounds(value.plot.bounds);
      } catch (err) {
        if (!controller.signal.aborted) setError((err as Error).message);
      }
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [key, disabled]);
  if (!sample)
    return (
      <p className="form-error" role="alert">
        The source sample is unavailable.
      </p>
    );
  const ready =
    !disabled &&
    result?.basis === basis &&
    result.revision === workspace.revision;
  const current = ready && result?.key === key;
  const apply = (operation: () => void) => {
    try {
      operation();
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const fit = () => {
    if (!result) return;
    const shape = gateShape(gate);
    let points: Point[];
    if (shape.type === "polygon") points = shape.vertices!;
    else if (shape.type === "spider" || shape.type === "curly")
      points = [
        [
          Math.min(shape.center![0], result.plot.bounds[0]),
          Math.min(shape.center![1], result.plot.bounds[2]),
        ],
        [
          Math.max(shape.center![0], result.plot.bounds[1]),
          Math.max(shape.center![1], result.plot.bounds[3]),
        ],
      ];
    else if (shape.type === "interval") {
      const intervals = shape.intervals!.map((p, i) =>
        p.map((v, side) => v ?? result.plot.bounds[2 * i + side]),
      );
      points = [
        [intervals[0][0], intervals[1]?.[0] ?? 0],
        [intervals[0][1], intervals[1]?.[1] ?? 0],
      ];
    } else {
      const [a, b] = shape.radii!,
        [x, y] = shape.center!,
        c = Math.cos(shape.angle!),
        s = Math.sin(shape.angle!);
      points = Array.from({ length: 129 }, (_, i) => {
        const t = (i * Math.PI) / 64;
        return [
          x + a * Math.cos(t) * c - b * Math.sin(t) * s,
          y + a * Math.cos(t) * s + b * Math.sin(t) * c,
        ];
      });
    }
    if (!points.flat().every(Number.isFinite))
      throw new Error(
        "This geometry exceeds the finite visual range. Use numeric coordinates.",
      );
    const limits = [0, ...(oneD ? [] : [1])].flatMap((axis) => {
      let lo = Math.min(...points.map((p) => p[axis]));
      let hi = Math.max(...points.map((p) => p[axis]));
      if (lo === hi) {
        lo = result.plot.bounds[2 * axis];
        hi = result.plot.bounds[2 * axis + 1];
      }
      return [axisValue(lo, hi, -0.04), axisValue(lo, hi, 1.04)];
    });
    setBounds(limits);
  };
  return (
    <section
      className="gate-shape-editor"
      aria-label="Visual gate shape editor"
    >
      <div className="shape-editor-heading">
        <div>
          <strong>
            {gate.magnetic ? "Edit magnetic anchor" : "Edit gate shape"}
          </strong>
          <p>
            {gate.curly
              ? "Drag the shared center to move all four populations. Adjust the noise coefficients in the numeric settings. Counts use every eligible parent event."
              : gate.spider
                ? "Drag the shared center to move all four populations. Drag an arm handle to rotate its boundary; keep the arms in their circular order."
                : "Drag the shape to move it. Drag handles to resize; ellipse rotation uses the round outer handle."}
          </p>
          {gate.dimensions?.length ? (
            <p className="shape-axis-basis">
              {gate.dimensions
                .map((d, i) => {
                  const ref = d.compensation_ref;
                  const compensation =
                    ref === "uncompensated"
                      ? "raw"
                      : ref === "FCS"
                        ? "acquisition matrix"
                        : ref === "sample"
                          ? "sample compensation"
                          : (workspace.compensations.find((c) => c.id === ref)
                              ?.name ?? "missing matrix");
                  return `${i ? "Y" : "X"}: ${d.channel}${d.ratio_channels ? " (ratio)" : ""} · ${d.transform.kind} · ${compensation}`;
                })
                .join(" / ")}
            </p>
          ) : null}
        </div>
        <label className="field">
          Parent display
          <select
            aria-label="Shape editor graph mode"
            value={mode}
            disabled={disabled || dragging}
            onChange={(e) => setMode(e.target.value)}
          >
            {(oneD
              ? ["histogram", "cdf"]
              : ["density", "scatter", "contour", "zebra", "pseudocolor"]
            ).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {result && (
        <div className="shape-editor-plot">
          <Plot
            workspaceId={workspace.id}
            revision={workspace.revision}
            sample={sample}
            gateId={gate.parent_id}
            x={result.plot.x}
            y={result.plot.y}
            mode={result.plot.mode}
            previewPayload={result.plot}
            shapeEditor={{
              gate,
              disabled: !ready,
              onChange,
              onActiveChange: (active) => {
                setDragging(active);
                onActiveChange(active);
              },
            }}
          />
        </div>
      )}
      <div className="shape-editor-status" role="status">
        {current ? (
          <span>
            <strong>{formatNumber(result.count, 0)}</strong> draft events /{" "}
            {formatNumber(result.parent_count, 0)} parent
            {result.parent_count > 0
              ? ` · ${formatNumber((100 * result.count) / result.parent_count, 2)}%`
              : " · percentage undefined"}
            {` · ${formatNumber(result.plot.visible_count, 0)} visible in preview`}
          </span>
        ) : (
          <span>
            {disabled
              ? "Review the changed workspace to refresh this preview."
              : error
                ? "Preview unavailable"
                : "Updating full-event draft preview…"}
          </span>
        )}
        <span className="shape-editor-actions">
          <button
            className="button small"
            type="button"
            disabled={!ready || dragging}
            onClick={() => apply(fit)}
          >
            Fit gate
          </button>
          <button
            className="button small"
            type="button"
            disabled={!ready || dragging || !!gate.spider || !!gate.curly}
            onClick={() => apply(() => onChange(scaleGate(gate, 0.95)))}
          >
            Contract 5%
          </button>
          <button
            className="button small"
            type="button"
            disabled={!ready || dragging || !!gate.spider || !!gate.curly}
            onClick={() => apply(() => onChange(scaleGate(gate, 1.05)))}
          >
            Expand 5%
          </button>
        </span>
      </div>
      <p className="form-note">
        Arrow keys move the focused shape or handle by one pixel; Shift moves
        ten. Double-click a polygon edge to add a vertex; Delete removes a
        focused vertex. Escape cancels an active drag.
      </p>
      <p className="form-note">
        {gate.magnetic
          ? "Handles edit the original anchor. Draft counts include its resolved magnetic position. "
          : ""}
        The preview uses every parent event.{" "}
        {gate.partition
          ? "Saving updates the thresholds and coordinates of every linked population together."
          : "Save the population to apply these changes."}
      </p>
      {gate.partition && current && (
        <div
          className="partition-preview"
          aria-label="Linked population counts"
        >
          {result.partition_counts?.map((member) => (
            <span key={member.member}>
              {member.name}: <strong>{formatNumber(member.count, 0)}</strong>
            </span>
          ))}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
