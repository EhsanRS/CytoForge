import type { CurlyGeometry, Gate, SpiderGeometry } from "./types";
import { axisFraction, axisValue } from "./graph";

export type Point = [number, number];
export interface GateShape {
  type: "interval" | "polygon" | "ellipse" | "spider" | "curly";
  intervals?: [number | null, number | null][];
  vertices?: Point[];
  holes?: Point[][];
  center?: Point;
  radii?: Point;
  angle?: number;
  spider?: SpiderGeometry;
  curly?: CurlyGeometry;
}

export function validateCurly(value: CurlyGeometry): void {
  if (![...value.center, ...value.coefficients].every(Number.isFinite))
    throw new Error("Enter a finite curly center and noise coefficients.");
  if (value.coefficients.some((v) => v < 0))
    throw new Error("Noise coefficients must be nonnegative.");
  if (value.convention !== "sqrt-positive-intensity-v1")
    throw new Error("This curly boundary convention is unsupported.");
}

export function validateSpider(value: SpiderGeometry): void {
  const tau = 2 * Math.PI;
  if (
    ![...value.center, ...value.scale, ...value.angles].every(Number.isFinite)
  )
    throw new Error("Enter finite spider coordinates, scales and angles.");
  if (value.scale.some((v) => v <= 0))
    throw new Error("Spider coordinate scales must remain positive.");
  if (value.angles.some((a) => a < 0 || a >= tau))
    throw new Error("Spider angles must be between 0 and 360 degrees.");
  const offsets = value.angles.map((a) => (a - value.angles[0] + tau) % tau);
  const gaps = offsets.map((v, i) => (i === 3 ? tau : offsets[i + 1]) - v);
  if (gaps.some((v) => v < 1e-6))
    throw new Error(
      "Keep spider arms in their circular order without crossing or collapsing them.",
    );
}

export function spiderViewport(
  value: SpiderGeometry,
  bounds: number[],
): Point[][] {
  const origin = value.center.map((v, i) =>
    axisFraction(v, bounds[2 * i], bounds[2 * i + 1]),
  );
  if (!origin.every(Number.isFinite))
    throw new Error(
      "Fit the spider center into view or use numeric settings for this coordinate range.",
    );
  const spanLogs = [0, 1].map((i) => {
    const lo = bounds[2 * i],
      hi = bounds[2 * i + 1];
    const half = hi / 2 - lo / 2;
    return half > 0 ? Math.log(half) + Math.LN2 : Math.log(hi - lo);
  });
  return value.angles.map((angle) => {
    const cardinal = new Map<number, Point>([
      [0, [1, 0]],
      [Math.PI / 2, [0, 1]],
      [Math.PI, [-1, 0]],
      [(3 * Math.PI) / 2, [0, -1]],
    ]);
    const direction = cardinal.get(angle) ?? [Math.cos(angle), Math.sin(angle)];
    const logs = direction.map(
      (v, i) => Math.log(Math.abs(v)) + Math.log(value.scale[i]) - spanLogs[i],
    );
    const largest = Math.max(...logs);
    const vector = direction.map(
      (v, i) => Math.sign(v) * Math.exp(logs[i] - largest),
    );
    let enter = 0,
      leave = Infinity;
    for (let i = 0; i < 2; i++) {
      if (vector[i] === 0) {
        if (origin[i] < 0 || origin[i] > 1) return [];
      } else {
        const ends = [
          (0 - origin[i]) / vector[i],
          (1 - origin[i]) / vector[i],
        ].sort((a, b) => a - b);
        enter = Math.max(enter, ends[0]);
        leave = Math.min(leave, ends[1]);
      }
    }
    if (leave < enter || !Number.isFinite(leave)) return [];
    return [enter, leave].map(
      (t) =>
        [0, 1].map((i) =>
          axisValue(
            bounds[2 * i],
            bounds[2 * i + 1],
            Math.max(0, Math.min(1, origin[i] + t * vector[i])),
          ),
        ) as Point,
    );
  });
}

export function gateShape(gate: Gate): GateShape {
  const dimensions = gate.dimensions?.length ?? 0;
  if (dimensions > 2)
    throw new Error(
      "Use numeric bounds for gates with more than two dimensions.",
    );
  if (gate.kind === "curly") {
    if (!gate.curly)
      throw new Error("Enter a curly center and noise coefficients.");
    validateCurly(gate.curly);
    return { type: "curly", center: gate.curly.center, curly: gate.curly };
  }
  if (gate.kind === "spider") {
    if (!gate.spider) throw new Error("Enter a spider center and four arms.");
    validateSpider(gate.spider);
    return { type: "spider", center: gate.spider.center, spider: gate.spider };
  }
  if (
    ["range", "rectangle", "quadrant", "hyperrectangle"].includes(gate.kind)
  ) {
    let intervals: [number | null, number | null][];
    if (gate.kind === "hyperrectangle") {
      intervals = gate.dimensions!.map((d) => [d.minimum, d.maximum]);
    } else if (gate.kind === "quadrant") {
      intervals = [
        [2, 3].includes(gate.quadrant)
          ? [gate.bounds[0], null]
          : [null, gate.bounds[0]],
        [1, 2].includes(gate.quadrant)
          ? [gate.bounds[1], null]
          : [null, gate.bounds[1]],
      ];
    } else {
      intervals = Array.from(
        { length: gate.kind === "range" ? 1 : 2 },
        (_, i) =>
          [gate.bounds[2 * i], gate.bounds[2 * i + 1]] as [number, number],
      );
    }
    if (!intervals.length)
      throw new Error("This gate has no geometric dimensions.");
    return { type: "interval", intervals };
  }
  if (gate.kind === "polygon")
    return { type: "polygon", vertices: gate.vertices, holes: gate.holes };
  if (gate.kind === "ellipse") {
    if (!gate.center || !gate.radii)
      throw new Error("Enter an ellipse center and positive radii.");
    return {
      type: "ellipse",
      center: gate.center,
      radii: gate.radii,
      angle: gate.angle,
    };
  }
  if (gate.kind === "ellipsoid" && dimensions === 2) {
    const matrix = gate.covariance;
    if (
      !matrix ||
      matrix.length !== 2 ||
      matrix.some((row) => row.length !== 2)
    )
      throw new Error("Enter a two-dimensional covariance matrix.");
    const scale = Math.max(...matrix.flat().map(Math.abs));
    if (!scale || matrix[0][1] !== matrix[1][0])
      throw new Error(
        "Visual ellipsoid editing requires a symmetric positive definite covariance.",
      );
    const a = matrix[0][0] / scale,
      b = matrix[0][1] / scale,
      c = matrix[1][1] / scale;
    const large = (a + c) / 2 + Math.hypot(a - c, 2 * b) / 2;
    const small = (a * c - b * b) / large;
    if (!(small > 0 && large > 0))
      throw new Error(
        "Visual ellipsoid editing requires a symmetric positive definite covariance.",
      );
    const threshold = Math.sqrt(gate.distance_square ?? 1);
    const radii: Point = [
      Math.sqrt(scale) * Math.sqrt(large) * threshold,
      Math.sqrt(scale) * Math.sqrt(small) * threshold,
    ];
    if (!radii.every(Number.isFinite) || radii.some((r) => r <= 0))
      throw new Error(
        "These ellipsoid axes cannot be represented as finite visual handles.",
      );
    return {
      type: "ellipse",
      center: gate.coordinates as Point,
      radii,
      angle: Math.atan2(2 * b, a - c) / 2,
    };
  }
  throw new Error(
    "Visual editing is available for geometric gates with one or two dimensions.",
  );
}

function checked(gate: Gate): Gate {
  const values = [
    ...gate.bounds,
    ...gate.vertices.flat(),
    ...(gate.holes ?? []).flat(2),
    ...(gate.center ?? []),
    ...(gate.radii ?? []),
    ...(gate.coordinates ?? []),
    ...(gate.covariance?.flat() ?? []),
    gate.angle,
    ...(gate.dimensions ?? []).flatMap((d) =>
      [d.minimum, d.maximum].filter((v): v is number => v !== null),
    ),
  ];
  if (!values.every(Number.isFinite))
    throw new Error(
      "The proposed geometry exceeds the finite coordinate range.",
    );
  if (gate.spider) validateSpider(gate.spider);
  if (gate.curly) validateCurly(gate.curly);
  return gate;
}

export function moveGate(gate: Gate, delta: Point): Gate {
  let change: Partial<Gate>;
  if (gate.kind === "curly")
    change = {
      curly: {
        ...gate.curly!,
        center: [
          gate.curly!.center[0] + delta[0],
          gate.curly!.center[1] + delta[1],
        ],
      },
    };
  else if (gate.kind === "spider")
    change = {
      spider: {
        ...gate.spider!,
        center: [
          gate.spider!.center[0] + delta[0],
          gate.spider!.center[1] + delta[1],
        ],
      },
    };
  else if (gate.kind === "hyperrectangle")
    change = {
      dimensions: gate.dimensions!.map((d, i) => ({
        ...d,
        minimum: d.minimum === null ? null : d.minimum + delta[i],
        maximum: d.maximum === null ? null : d.maximum + delta[i],
      })),
    };
  else if (gate.kind === "polygon")
    change = {
      vertices: gate.vertices.map((p) => [p[0] + delta[0], p[1] + delta[1]]),
      holes: gate.holes?.map((ring) =>
        ring.map((p) => [p[0] + delta[0], p[1] + delta[1]]),
      ),
    };
  else if (gate.kind === "ellipse")
    change = {
      center: [gate.center![0] + delta[0], gate.center![1] + delta[1]],
    };
  else if (gate.kind === "ellipsoid")
    change = { coordinates: gate.coordinates!.map((v, i) => v + delta[i]) };
  else if (gate.kind === "quadrant")
    change = { bounds: gate.bounds.map((v, i) => v + delta[i]) };
  else
    change = {
      bounds: gate.bounds.map((v, i) => v + delta[Math.floor(i / 2)]),
    };
  return checked({ ...gate, ...change });
}

function ellipseGate(
  gate: Gate,
  center: Point,
  radii: Point,
  angle: number,
): Gate {
  if (!radii.every((r) => Number.isFinite(r) && r > 0))
    throw new Error("Ellipse radii must remain positive.");
  if (gate.kind === "ellipse")
    return checked({ ...gate, center, radii, angle });
  const divisor = Math.sqrt(gate.distance_square ?? 1);
  const a = (radii[0] / divisor) ** 2,
    b = (radii[1] / divisor) ** 2;
  const c = Math.cos(angle),
    s = Math.sin(angle);
  const covariance = [
    [c * c * a + s * s * b, c * s * (a - b)],
    [c * s * (a - b), s * s * a + c * c * b],
  ];
  return checked({ ...gate, coordinates: center, covariance });
}

export function changeGateHandle(gate: Gate, key: string, point: Point): Gate {
  const shape = gateShape(gate);
  if (shape.type === "curly") {
    if (key !== "curly-center") throw new Error("Unknown curly handle.");
    return checked({ ...gate, curly: { ...shape.curly!, center: point } });
  }
  if (shape.type === "spider") {
    const value = shape.spider!;
    if (key === "spider-center")
      return checked({ ...gate, spider: { ...value, center: point } });
    const index = Number(key.replace("spider-arm", ""));
    if (!Number.isInteger(index) || index < 0 || index > 3)
      throw new Error("Unknown spider arm.");
    const delta = point.map((v, i) => v - value.center[i]);
    if (delta.every((v) => v === 0))
      throw new Error("Move the arm away from its center.");
    const normalized = delta.map((v, i) => v / value.scale[i]);
    if (!normalized.every(Number.isFinite) || normalized.every((v) => v === 0))
      throw new Error("Use numeric angles for this coordinate range.");
    const tau = 2 * Math.PI;
    const angle = (Math.atan2(normalized[1], normalized[0]) + tau) % tau;
    return checked({
      ...gate,
      spider: {
        ...value,
        angles: value.angles.map((a, i) =>
          i === index ? angle : a,
        ) as SpiderGeometry["angles"],
      },
    });
  }
  const hole = /^h(\d+)v(\d+)$/.exec(key);
  if (hole) {
    const ringIndex = Number(hole[1]),
      vertexIndex = Number(hole[2]);
    return checked({
      ...gate,
      holes: gate.holes?.map((ring, r) =>
        r === ringIndex
          ? ring.map((p, i) => (i === vertexIndex ? point : p))
          : ring,
      ),
    });
  }
  if (key.startsWith("v")) {
    const index = Number(key.slice(1));
    return checked({
      ...gate,
      vertices: gate.vertices.map((p, i) => (i === index ? point : p)),
    });
  }
  if (shape.type === "ellipse") {
    const center = shape.center!,
      radii = [...shape.radii!] as Point,
      angle = shape.angle!;
    if (key === "rotate")
      return ellipseGate(
        gate,
        center,
        radii,
        Math.atan2(point[1] - center[1], point[0] - center[0]),
      );
    const index = Number(key[1]);
    const direction = angle + (index * Math.PI) / 2;
    radii[index] = Math.abs(
      (point[0] - center[0]) * Math.cos(direction) +
        (point[1] - center[1]) * Math.sin(direction),
    );
    return ellipseGate(gate, center, radii, angle);
  }
  const indices = key.startsWith("c")
    ? [Number(key[1]), Number(key[2])]
    : [Number(key.slice(1))];
  const intervals = shape.intervals!.map(
    (v) => [...v] as [number | null, number | null],
  );
  for (const index of indices) {
    const axis = Math.floor(index / 2),
      side = index % 2,
      other = intervals[axis][1 - side];
    intervals[axis][side] = point[axis];
    if (other !== null && intervals[axis][0]! > intervals[axis][1]!)
      intervals[axis] = [intervals[axis][1], intervals[axis][0]];
  }
  if (gate.kind === "hyperrectangle")
    return checked({
      ...gate,
      dimensions: gate.dimensions!.map((d, i) => ({
        ...d,
        minimum: intervals[i][0],
        maximum: intervals[i][1],
      })),
    });
  if (gate.kind === "quadrant")
    return checked({
      ...gate,
      bounds: intervals.map((p) => p.find((v) => v !== null)!),
    });
  return checked({ ...gate, bounds: intervals.flat() as number[] });
}

export function scaleGate(gate: Gate, factor: number): Gate {
  const shape = gateShape(gate);
  if (shape.type === "curly")
    throw new Error("Move the curly center or change its noise coefficients.");
  if (shape.type === "spider")
    throw new Error(
      "Rotate spider arms or move their shared center to change the populations.",
    );
  if (shape.type === "ellipse")
    return ellipseGate(
      gate,
      shape.center!,
      shape.radii!.map((r) => r * factor) as Point,
      shape.angle!,
    );
  if (shape.type === "polygon") {
    const center = [0, 1].map((i) =>
      axisValue(
        Math.min(...gate.vertices.map((p) => p[i])),
        Math.max(...gate.vertices.map((p) => p[i])),
        0.5,
      ),
    );
    return checked({
      ...gate,
      vertices: gate.vertices.map((p) => [
        center[0] + (p[0] - center[0]) * factor,
        center[1] + (p[1] - center[1]) * factor,
      ]),
      holes: gate.holes?.map((ring) =>
        ring.map((p) => [
          center[0] + (p[0] - center[0]) * factor,
          center[1] + (p[1] - center[1]) * factor,
        ]),
      ),
    });
  }
  if (shape.intervals!.some((p) => p.includes(null)))
    throw new Error(
      "Resize the defined boundaries of an unbounded gate with its handles.",
    );
  const bounds = shape.intervals!.flatMap(([lo, hi]) => {
    const center = axisValue(lo!, hi!, 0.5);
    return [center + (lo! - center) * factor, center + (hi! - center) * factor];
  });
  return checked(
    gate.kind === "hyperrectangle"
      ? {
          ...gate,
          dimensions: gate.dimensions!.map((d, i) => ({
            ...d,
            minimum: bounds[2 * i],
            maximum: bounds[2 * i + 1],
          })),
        }
      : { ...gate, bounds },
  );
}
