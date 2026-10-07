import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import {
  patchGraphGateStyle,
  strokeAndFillGate,
} from "../.tmp/gate-style-tests/graphGateStyle.js";
import {
  patchGraphText,
  scientificGraphOptions,
} from "../.tmp/gate-style-tests/graphTypography.js";
import { CloudScene } from "../.tmp/gate-style-tests/scene3d.js";
const { validState } = createRequire(import.meta.url)(
  "../desktop/plot-windows.cjs",
);
const { Resvg } = createRequire(import.meta.url)("@resvg/resvg-js");
globalThis.Path2D = class {
  d = "";
  moveTo(x, y) {
    this.d += `M ${x} ${y} `;
  }
  lineTo(x, y) {
    this.d += `L ${x} ${y} `;
  }
  closePath() {
    this.d += "z ";
  }
  rect(x, y, w, h) {
    this.d += `M ${x} ${y} L ${x + w} ${y} L ${x + w} ${y + h} L ${x} ${y + h} z `;
  }
  addPath(path) {
    this.d += path.d;
  }
};
const state = {
  workspaceId: "a".repeat(32),
  sampleId: "b".repeat(32),
  gateId: null,
  x: "X",
  y: "Y",
  mode: "scatter",
};

test("resetting gate appearance preserves fonts and scientific settings in independent windows", () => {
  const original = {
    sigma: 2,
    palette: "viridis",
    gate_style: { fill_opacity: 0.3 },
    typography: { title: { font_size_pt: 18 } },
  };
  const changed = patchGraphGateStyle(original, {
    fill_color: "#123456",
    show_labels: false,
  });
  assert.deepEqual(scientificGraphOptions(changed), {
    sigma: 2,
    palette: "viridis",
  });
  assert.equal(original.gate_style.fill_color, undefined);
  const reset = patchGraphGateStyle(original, { fill_opacity: null });
  assert.deepEqual(reset, {
    sigma: 2,
    palette: "viridis",
    typography: original.typography,
  });
  assert.deepEqual(patchGraphText(original, "title", { font_size_pt: null }), {
    sigma: 2,
    palette: "viridis",
    gate_style: original.gate_style,
  });
  assert.equal(validState({ ...state, graphOptions: changed }), true);
  assert.equal(
    validState(JSON.parse(JSON.stringify({ ...state, graphOptions: changed }))),
    true,
  );
});

function context() {
  const result = {
    globalAlpha: 1,
    lineWidth: 1.5,
    fillStyle: "#ffffff",
    font: "default",
    calls: [],
    stack: [],
    clips: [],
    save() {
      this.stack.push({
        globalAlpha: this.globalAlpha,
        lineWidth: this.lineWidth,
        fillStyle: this.fillStyle,
        clips: this.clips.slice(),
      });
    },
    restore() {
      Object.assign(this, this.stack.pop());
    },
    fill(rule) {
      this.calls.push({
        kind: "fill",
        rule,
        opacity: this.globalAlpha,
        color: this.fillStyle,
      });
    },
    stroke() {
      this.calls.push({
        kind: "stroke",
        width: this.lineWidth,
        opacity: this.globalAlpha,
      });
    },
    fillText(text, x, y) {
      this.calls.push({ kind: "text", text, x, y, font: this.font });
    },
    fillRect(x, y, w, h) {
      this.calls.push({
        kind: "marker",
        x,
        y,
        w,
        h,
        color: this.fillStyle,
        opacity: this.globalAlpha,
        clips: this.clips.slice(),
      });
    },
    clip(path, rule) {
      this.clips.push({ d: path.d, rule });
    },
  };
  for (const method of [
    "setTransform",
    "clearRect",
    "beginPath",
    "moveTo",
    "lineTo",
  ])
    result[method] = () => {};
  return result;
}

const region = {
  outer: [
    [10, 10],
    [90, 10],
    [90, 90],
    [10, 90],
  ],
  holes: [],
  bounds: [0, 0, 100, 100],
};
function pixels(ctx) {
  const paint = ctx.calls.find((call) => call.kind === "marker");
  let body = `<rect x="${paint.x}" y="${paint.y}" width="${paint.w}" height="${paint.h}" fill="${paint.color}" opacity="${paint.opacity}"/>`;
  let defs = "";
  for (const [index, clip] of paint.clips.entries()) {
    defs += `<clipPath id="clip${index}"><path d="${clip.d}" clip-rule="${clip.rule}"/></clipPath>`;
    body = `<g clip-path="url(#clip${index})">${body}</g>`;
  }
  return new Resvg(
    `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs>${defs}</defs><rect width="100" height="100" fill="white"/>${body}</svg>`,
    { font: { loadSystemFonts: false } },
  ).render().pixels;
}
function rgb(data, x, y) {
  return [...data.subarray((y * 100 + x) * 4, (y * 100 + x) * 4 + 3)];
}
test("closed gate holes use even-odd clips while borders retain their opacity and width", () => {
  const canvas = context();
  strokeAndFillGate(
    canvas,
    {
      gate_style: {
        fill_opacity: 0.4,
        fill_color: "#d12345",
        line_width_px: 4.5,
      },
    },
    "#abcdef",
    region,
  );
  const paint = canvas.calls.find((call) => call.kind === "marker");
  assert.equal(paint.opacity, 0.4);
  assert.equal(paint.color, "#d12345");
  assert.equal(paint.clips[0].rule, "evenodd");
  assert.deepEqual(canvas.calls.at(-1), {
    kind: "stroke",
    width: 4.5,
    opacity: 1,
  });
  assert.equal(canvas.globalAlpha, 1);
  assert.equal(canvas.fillStyle, "#ffffff");
});

test("open quadrant separators remain unfilled and opacity zero preserves the outline", () => {
  for (const [closed, opacity] of [
    [false, 0.4],
    [true, 0],
  ]) {
    const canvas = context();
    strokeAndFillGate(
      canvas,
      { gate_style: { fill_opacity: opacity } },
      "#abcdef",
      closed ? region : undefined,
    );
    assert.deepEqual(canvas.calls, [
      { kind: "stroke", width: 1.5, opacity: 1 },
    ]);
  }
});

for (const topology of ["overlap", "nested", "outside", "double_outer"]) {
  test(`compiled plot fill preserves scientific exclusion for ${topology} rings`, () => {
    const ctx = context();
    const rings = {
      ...region,
      holes: [
        [
          [30, 30],
          [60, 30],
          [60, 60],
          [30, 60],
        ],
      ],
    };
    if (topology === "overlap")
      rings.holes.push([
        [40, 40],
        [70, 40],
        [70, 70],
        [40, 70],
      ]);
    if (topology === "nested")
      rings.holes.push([
        [40, 40],
        [55, 40],
        [55, 55],
        [40, 55],
      ]);
    if (topology === "outside")
      rings.holes.push([
        [80, 80],
        [120, 80],
        [120, 120],
        [80, 120],
      ]);
    if (topology === "double_outer") {
      rings.outer = [...region.outer, region.outer[0], ...region.outer];
      rings.holes = [];
    }
    strokeAndFillGate(
      ctx,
      { gate_style: { fill_opacity: 0.4, fill_color: "#ff0000" } },
      "#000000",
      rings,
    );
    const rendered = pixels(ctx);
    assert.deepEqual(rgb(rendered, 50, 50), [255, 255, 255]);
    if (topology !== "double_outer")
      assert.deepEqual(rgb(rendered, 25, 25), [255, 153, 153]);
  });
}

test("3D software scene retains the event buffer, preserves styles after resize, and hides only gate names", () => {
  globalThis.window = { devicePixelRatio: 2 };
  const ctx = context();
  const element = (overlay) => ({
    style: {},
    getContext: () => (overlay ? ctx : null),
    addEventListener() {},
    removeEventListener() {},
  });
  const scene = new CloudScene(element(false), element(true));
  const buffer = new Float32Array([0.2, 0.3, 0.4, -1, 0.5, 1, 0, 0]);
  const original = buffer.slice();
  scene.setBuffers([buffer]);
  const metadata = {
    displayed_count: 1,
    graph_options: { palette: "ocean" },
    x: "X",
    y: "Y",
    z: "Z",
    ticks: [[], [], []],
    bounds: [0, 1, 0, 1, 0, 1],
    boxes: [
      { name: "Known box", color: "#123456", normalized: [0, 1, 0, 1, 0, 1] },
    ],
  };
  const options = {
    gate_style: { line_width_px: 4.5 },
    typography: { gate_labels: { font_size_pt: 18, font_family: "serif" } },
  };
  scene.resize(600, 380);
  scene.draw(metadata, { z: "Z" }, options);
  assert.ok(
    ctx.calls.some(
      (call) =>
        call.text === "Known box" &&
        call.font.includes('24px "CytoForge Serif"'),
    ),
  );
  assert.ok(
    ctx.calls.some((call) => call.kind === "stroke" && call.width === 4.5),
  );
  const marker = ctx.calls.find(
    (call) => call.kind === "marker" && call.w === 2,
  );
  assert.ok(marker);
  ctx.calls.length = 0;
  scene.resize(900, 500);
  scene.draw(metadata, { z: "Z" }, options);
  assert.ok(
    ctx.calls.some(
      (call) =>
        call.text === "Known box" &&
        call.font.includes('24px "CytoForge Serif"'),
    ),
  );
  ctx.calls.length = 0;
  scene.draw(
    metadata,
    { z: "Z" },
    patchGraphGateStyle(options, { show_labels: false }),
  );
  assert.ok(!ctx.calls.some((call) => call.text === "Known box"));
  assert.ok(ctx.calls.some((call) => call.text === "X · X"));
  assert.ok(ctx.calls.some((call) => call.kind === "marker" && call.w === 2));
  assert.deepEqual(buffer, original);
  scene.dispose();
});

for (const bad of [
  [],
  false,
  { fill_opacity: -1 },
  { fill_opacity: 1.1 },
  { fill_opacity: true },
  { fill_opacity: "0.4" },
  { fill_opacity: NaN },
  { line_width_px: 0.24 },
  { line_width_px: 12.1 },
  { line_width_px: Infinity },
  { line_width_px: true },
  { fill_color: "#fff" },
  { fill_color: "red" },
  { fill_color: "#123456;display:none" },
  { show_labels: 0 },
  { show_labels: "false" },
  { unknown: 1 },
  JSON.parse('{"__proto__":{}}'),
  { constructor: {} },
]) {
  test(`native plot IPC rejects malformed gate style ${JSON.stringify(bad)}`, () => {
    assert.equal(
      validState({ ...state, graphOptions: { gate_style: bad } }),
      false,
    );
  });
}
