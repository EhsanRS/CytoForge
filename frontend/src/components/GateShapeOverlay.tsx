import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import type { Gate } from "../types";
import { axisFraction, axisValue } from "../graph";
import {
  changeGateHandle,
  gateShape,
  moveGate,
  spiderViewport,
  type Point,
} from "../gateGeometry";

const padding = { left: 64, top: 24, right: 24, bottom: 54 };
export function GateShapeOverlay({
  gate,
  bounds,
  width,
  height,
  disabled,
  onChange,
  onActiveChange,
  segments,
}: {
  gate: Gate;
  bounds: number[];
  width: number;
  height: number;
  disabled: boolean;
  onChange: (gate: Gate) => void;
  onActiveChange?: (active: boolean) => void;
  segments?: Point[][];
}) {
  const [selected, setSelected] = useState("body");
  const [error, setError] = useState("");
  const gesture = useRef<{
    gate: Gate;
    key: string;
    start: Point;
    pointer: number;
    view: string;
  } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const clipId = useId();
  const holeMaskId = useId();
  const holes = gate.holes ?? [];
  const vertexCount =
    gate.vertices.length + holes.reduce((n, ring) => n + ring.length, 0);
  const ringPoints = (ring: Point[]) =>
    ring
      .map(pixel)
      .map((p) => p.join(","))
      .join(" ");
  const w = width - padding.left - padding.right,
    h = height - padding.top - padding.bottom;
  const twoD = bounds.length === 4;
  const pointAt = (x: number, y: number): Point => {
    const rect = svg.current!.getBoundingClientRect();
    return [
      axisValue(bounds[0], bounds[1], (x - rect.left - padding.left) / w),
      twoD
        ? axisValue(bounds[2], bounds[3], 1 - (y - rect.top - padding.top) / h)
        : 0,
    ];
  };
  const pixel = (point: Point): Point => [
    padding.left + axisFraction(point[0], bounds[0], bounds[1]) * w,
    twoD
      ? padding.top + h - axisFraction(point[1], bounds[2], bounds[3]) * h
      : padding.top + h / 2,
  ];
  const cancel = () => {
    const original = gesture.current?.gate;
    gesture.current = null;
    if (original) onChange(original);
    onActiveChange?.(false);
  };
  useEffect(() => {
    if (disabled) cancel();
  }, [disabled]);
  const viewKey = JSON.stringify([bounds, width, height]);
  useEffect(() => {
    if (gesture.current && gesture.current.view !== viewKey) {
      cancel();
      setError("The view changed; the active drag was cancelled.");
    }
  }, [viewKey]);
  const apply = (operation: () => Gate) => {
    try {
      onChange(operation());
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  };
  let shape;
  try {
    shape = gateShape(gate);
  } catch (err) {
    return (
      <div className="shape-edit-error" role="alert">
        {(err as Error).message}
      </div>
    );
  }
  const handles: { key: string; point: Point; label: string }[] = [];
  let vertices: Point[] = [],
    rect: { x: number; y: number; width: number; height: number } | null = null;
  let spiderSegments: Point[][] = [];
  let viewportError = "";
  if (shape.type === "interval") {
    const intervals = shape.intervals!.map(([lo, hi], i) => [
      lo ?? bounds[2 * i],
      hi ?? bounds[2 * i + 1],
    ]);
    const centers = intervals.map(([lo, hi]) => axisValue(lo, hi, 0.5));
    const clip = (v: number, axis: number) =>
      Math.max(bounds[2 * axis], Math.min(bounds[2 * axis + 1], v));
    const start = pixel([
      clip(intervals[0][0], 0),
      twoD ? clip(intervals[1][1], 1) : 0,
    ]);
    const end = pixel([
      clip(intervals[0][1], 0),
      twoD ? clip(intervals[1][0], 1) : 0,
    ]);
    rect = {
      x: start[0],
      y: twoD ? start[1] : padding.top,
      width: Math.max(0, end[0] - start[0]),
      height: twoD ? Math.max(0, end[1] - start[1]) : h,
    };
    shape.intervals!.forEach((interval, axis) =>
      interval.forEach((value, side) => {
        if (value === null) return;
        const point: Point = [centers[0], centers[1] ?? 0];
        point[axis] = value;
        handles.push({
          key: `b${2 * axis + side}`,
          point,
          label: `Resize ${axis === 0 ? "X" : "Y"} ${side ? "maximum" : "minimum"}`,
        });
      }),
    );
    if (twoD)
      shape.intervals![0].forEach((a, i) =>
        shape.intervals![1].forEach((b, j) => {
          if (a !== null && b !== null)
            handles.push({
              key: `c${i}${2 + j}`,
              point: [a, b],
              label: `Resize corner ${i + 1}, ${j + 1}`,
            });
        }),
      );
  } else if (shape.type === "polygon") {
    vertices = shape.vertices!;
    vertices.forEach((point, i) =>
      handles.push({
        key: `v${i}`,
        point,
        label: `Move polygon vertex ${i + 1}`,
      }),
    );
    holes.forEach((ring, r) =>
      ring.forEach((point, i) =>
        handles.push({
          key: `h${r}v${i}`,
          point,
          label: `Move hole ${r + 1} vertex ${i + 1}`,
        }),
      ),
    );
  } else if (shape.type === "curly") {
    handles.push({
      key: "curly-center",
      point: shape.center!,
      label: "Move curly center",
    });
    spiderSegments = segments ?? [];
  } else if (shape.type === "spider") {
    handles.push({
      key: "spider-center",
      point: shape.center!,
      label: "Move spider center",
    });
    try {
      spiderSegments = spiderViewport(shape.spider!, bounds);
      spiderSegments.forEach((segment, arm) => {
        if (segment.length !== 2) return;
        const point = [0, 1].map((i) =>
          axisValue(segment[0][i], segment[1][i], 0.9),
        ) as Point;
        handles.push({
          key: `spider-arm${arm}`,
          point,
          label: `Rotate spider arm ${arm + 1}`,
        });
      });
    } catch (err) {
      viewportError = (err as Error).message;
    }
  } else {
    const center = shape.center!,
      radii = shape.radii!,
      angle = shape.angle!;
    const vertex = (theta: number): Point => [
      center[0] +
        radii[0] * Math.cos(theta) * Math.cos(angle) -
        radii[1] * Math.sin(theta) * Math.sin(angle),
      center[1] +
        radii[0] * Math.cos(theta) * Math.sin(angle) +
        radii[1] * Math.sin(theta) * Math.cos(angle),
    ];
    vertices = Array.from({ length: 129 }, (_, i) =>
      vertex((i * Math.PI) / 64),
    );
    for (let axis = 0; axis < 2; axis++)
      for (let side = 0; side < 2; side++)
        handles.push({
          key: `r${axis}${side ? "m" : "p"}`,
          point: vertex((axis * Math.PI) / 2 + side * Math.PI),
          label: `Resize ellipse axis ${axis + 1} ${side ? "negative" : "positive"}`,
        });
    const c = pixel(center),
      end = pixel(vertex(0));
    const length = Math.hypot(end[0] - c[0], end[1] - c[1]);
    if (length > 0 && Number.isFinite(length)) {
      const target: Point = [
        end[0] + (28 * (end[0] - c[0])) / length,
        end[1] + (28 * (end[1] - c[1])) / length,
      ];
      handles.push({
        key: "rotate",
        point: [
          axisValue(bounds[0], bounds[1], (target[0] - padding.left) / w),
          axisValue(bounds[2], bounds[3], 1 - (target[1] - padding.top) / h),
        ],
        label: "Rotate ellipse",
      });
    }
  }
  const down = (event: PointerEvent<SVGElement>, key: string) => {
    event.stopPropagation();
    if (disabled || event.button !== 0 || gesture.current) return;
    event.preventDefault();
    event.currentTarget.focus();
    svg.current!.setPointerCapture(event.pointerId);
    gesture.current = {
      gate,
      key,
      start: pointAt(event.clientX, event.clientY),
      pointer: event.pointerId,
      view: viewKey,
    };
    setSelected(key);
    setError("");
    onActiveChange?.(true);
  };
  const move = (event: PointerEvent<SVGElement>) => {
    const active = gesture.current;
    if (!active || disabled || event.pointerId !== active.pointer) return;
    event.stopPropagation();
    const point = pointAt(event.clientX, event.clientY);
    apply(() =>
      active.key === "body"
        ? moveGate(active.gate, [
            point[0] - active.start[0],
            point[1] - active.start[1],
          ])
        : changeGateHandle(active.gate, active.key, point),
    );
  };
  const up = (event: PointerEvent<SVGElement>) => {
    if (event.pointerId !== gesture.current?.pointer) return;
    move(event);
    gesture.current = null;
    onActiveChange?.(false);
    event.stopPropagation();
  };
  const insertVertex = (x: number, y: number) => {
    if (disabled || shape.type !== "polygon") return;
    if (vertexCount >= 2000) {
      setError("A polygon supports up to 2,000 vertices across all rings.");
      return;
    }
    const box = svg.current!.getBoundingClientRect(),
      target = [x - box.left, y - box.top];
    let nearest = 0,
      nearestRing = -1,
      distance = Infinity;
    [gate.vertices, ...holes].forEach((ring, r) =>
      ring.forEach((p, i) => {
        const a = pixel(p),
          b = pixel(ring[(i + 1) % ring.length]);
        const dx = b[0] - a[0],
          dy = b[1] - a[1],
          squared = dx * dx + dy * dy;
        const t = squared
          ? Math.max(
              0,
              Math.min(
                1,
                ((target[0] - a[0]) * dx + (target[1] - a[1]) * dy) / squared,
              ),
            )
          : 0;
        const d = Math.hypot(
          target[0] - a[0] - t * dx,
          target[1] - a[1] - t * dy,
        );
        if (d < distance) {
          distance = d;
          nearest = i;
          nearestRing = r - 1;
        }
      }),
    );
    if (distance <= 12)
      apply(() => {
        const insert = (ring: Point[]) => [
          ...ring.slice(0, nearest + 1),
          pointAt(x, y),
          ...ring.slice(nearest + 1),
        ];
        return nearestRing < 0
          ? { ...gate, vertices: insert(gate.vertices) }
          : {
              ...gate,
              holes: holes.map((ring, r) =>
                r === nearestRing ? insert(ring) : ring,
              ),
            };
      });
  };
  return (
    <>
      <svg
        ref={svg}
        className={`gate-shape-overlay ${disabled ? "disabled" : ""}`}
        data-shape-bounds={JSON.stringify(bounds)}
        width={width}
        height={height}
        aria-label={
          gate.magnetic ? "Edit magnetic anchor geometry" : "Edit gate geometry"
        }
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={cancel}
        onLostPointerCapture={() => {
          if (gesture.current) cancel();
        }}
        onDoubleClick={(event) => {
          if (selected !== "body") return;
          event.preventDefault();
          event.stopPropagation();
          insertVertex(event.clientX, event.clientY);
        }}
        onKeyDown={(event) => {
          if (disabled) return;
          if (event.key === "Enter") {
            event.preventDefault();
            event.stopPropagation();
            return;
          }
          if (event.key === "Escape" && gesture.current) {
            event.preventDefault();
            event.stopPropagation();
            cancel();
            return;
          }
          if (
            ["Delete", "Backspace"].includes(event.key) &&
            (selected.startsWith("v") || /^h\d+v\d+$/.test(selected))
          ) {
            event.preventDefault();
            event.stopPropagation();
            const hole = /^h(\d+)v(\d+)$/.exec(selected);
            const ringIndex = hole ? Number(hole[1]) : -1;
            const index = hole ? Number(hole[2]) : Number(selected.slice(1));
            const ring = ringIndex < 0 ? gate.vertices : holes[ringIndex];
            if (ring.length <= 3) {
              setError("A polygon needs at least three vertices.");
              return;
            }
            apply(() =>
              ringIndex < 0
                ? { ...gate, vertices: ring.filter((_, i) => i !== index) }
                : {
                    ...gate,
                    holes: holes.map((value, r) =>
                      r === ringIndex
                        ? value.filter((_, i) => i !== index)
                        : value,
                    ),
                  },
            );
            setSelected("body");
            return;
          }
          const direction: Record<string, Point> = {
            ArrowLeft: [-1, 0],
            ArrowRight: [1, 0],
            ArrowUp: [0, 1],
            ArrowDown: [0, -1],
          };
          if (!direction[event.key]) return;
          event.preventDefault();
          event.stopPropagation();
          const [dx, dy] = direction[event.key].map(
            (v) => v * (event.shiftKey ? 10 : 1),
          );
          const origin =
            selected === "body"
              ? (handles[0]?.point ?? [bounds[0], bounds[2] ?? 0])
              : handles.find((handle) => handle.key === selected)?.point;
          if (!origin) return;
          const p = pixel(origin);
          const point: Point = [
            axisValue(bounds[0], bounds[1], (p[0] + dx - padding.left) / w),
            twoD
              ? axisValue(
                  bounds[2],
                  bounds[3],
                  1 - (p[1] - dy - padding.top) / h,
                )
              : 0,
          ];
          apply(() =>
            selected === "body"
              ? moveGate(gate, [point[0] - origin[0], point[1] - origin[1]])
              : changeGateHandle(gate, selected, point),
          );
        }}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={padding.left} y={padding.top} width={w} height={h} />
          </clipPath>
          <mask
            id={holeMaskId}
            maskUnits="userSpaceOnUse"
            x={0}
            y={0}
            width={width}
            height={height}
          >
            <rect x={0} y={0} width={width} height={height} fill="white" />
            {holes.map((ring, r) => (
              <polygon
                key={r}
                points={ringPoints(ring)}
                fill="black"
                fillRule="evenodd"
              />
            ))}
          </mask>
        </defs>
        <g
          className="gate-shape-body"
          clipPath={`url(#${clipId})`}
          style={{ color: gate.color }}
          role="button"
          tabIndex={disabled ? -1 : 0}
          aria-label="Move entire gate"
          aria-disabled={disabled}
          onFocus={() => setSelected("body")}
          onPointerDown={(e) => down(e, "body")}
        >
          {shape.type === "spider" || shape.type === "curly" ? (
            <path
              style={{
                fill: "none",
                stroke: "currentColor",
                strokeWidth: 2,
                pointerEvents: "stroke",
              }}
              d={spiderSegments
                .filter((s) => s.length >= 2)
                .map((s) =>
                  s
                    .map((p, i) => `${i ? "L" : "M"}${pixel(p).join(",")}`)
                    .join(""),
                )
                .join(" ")}
            />
          ) : rect ? (
            <rect {...rect} />
          ) : (
            <polygon
              fillRule="evenodd"
              mask={holes.length ? `url(#${holeMaskId})` : undefined}
              points={vertices
                .map(pixel)
                .filter((p) => p.every(Number.isFinite))
                .map((p) => p.join(","))
                .join(" ")}
            />
          )}
          {holes.map((ring, r) => (
            <polygon
              key={r}
              points={ringPoints(ring)}
              style={{ fill: "none", stroke: "currentColor" }}
            />
          ))}
        </g>
        {handles.map(({ key, point, label }) => {
          const p = pixel(point);
          if (
            !p.every(Number.isFinite) ||
            p[0] < 0 ||
            p[0] > width ||
            p[1] < 0 ||
            p[1] > height
          )
            return null;
          return (
            <circle
              key={key}
              data-shape-handle={key}
              className={`gate-shape-handle ${selected === key ? "selected" : ""}`}
              cx={p[0]}
              cy={p[1]}
              r={
                key === "rotate"
                  ? 6
                  : vertexCount > 80 &&
                      (key.startsWith("v") || key.startsWith("h")) &&
                      selected !== key
                    ? 1.4
                    : 5
              }
              style={
                vertexCount > 80 &&
                (key.startsWith("v") || key.startsWith("h")) &&
                selected !== key
                  ? { strokeWidth: 0.8 }
                  : undefined
              }
              role="button"
              aria-label={label}
              aria-disabled={disabled}
              tabIndex={disabled ? -1 : 0}
              onFocus={() => setSelected(key)}
              onPointerDown={(e) => down(e, key)}
            />
          );
        })}
      </svg>
      {(error || viewportError) && (
        <div className="shape-edit-error" role="alert">
          {error || viewportError}
        </div>
      )}
    </>
  );
}
