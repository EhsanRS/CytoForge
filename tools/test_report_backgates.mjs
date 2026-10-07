import assert from "node:assert/strict";
import test from "node:test";
import {
  backgateSummary,
  drawBackgatePoints,
} from "../.tmp/backgate-tests/backgates.js";
import {
  backgateAncestryPlots,
  backgateAncestryPlacement,
} from "../.tmp/backgate-tests/reportBackgates.js";

const transform = { kind: "linear" };
const sample = "a".repeat(32);
const gates = [
  {
    id: "b".repeat(32),
    sample_id: sample,
    parent_id: null,
    name: "First",
    kind: "range",
    x: "X",
    y: null,
    x_transform: transform,
    bounds: [0, 4],
  },
  {
    id: "c".repeat(32),
    sample_id: sample,
    parent_id: "b".repeat(32),
    name: "Second",
    kind: "rectangle",
    x: "X",
    y: "Y",
    x_transform: transform,
    y_transform: transform,
    bounds: [0, 4, 1, 4],
  },
  {
    id: "d".repeat(32),
    sample_id: sample,
    parent_id: "c".repeat(32),
    name: "Interest",
    kind: "range",
    x: "Z",
    y: null,
    x_transform: transform,
    bounds: [2, 4],
  },
];
const workspace = { gates };
const current = {
  sample_id: sample,
  gate_id: gates[2].id,
  x: "Y",
  y: "X",
  mode: "scatter",
  bounds: [0, 4, 0, 4],
  graph_options: {
    typography: { gate_labels: { font_size_pt: 18 } },
    gate_style: { fill_opacity: 0.4 },
  },
};
const page = {
  width_mm: 210,
  height_mm: 297,
  margin_mm: 12,
  background: "#ffffff",
};
const layout = () => ({
  pages: [page],
  elements: [],
  show_header: true,
  show_footer: true,
});

test("all ancestry stages show the final population in each original parent and gate coordinates", () => {
  const before = JSON.stringify([workspace, current]);
  const plots = backgateAncestryPlots(workspace, current);
  assert.deepEqual(
    plots.map((p) => p.gate_id),
    [null, gates[0].id, gates[1].id],
  );
  assert.deepEqual(
    plots.map((p) => [p.x, p.y, p.mode]),
    [
      ["X", null, "histogram"],
      ["X", "Y", "scatter"],
      ["Z", null, "histogram"],
    ],
  );
  assert.deepEqual(
    plots.map((p) => p.coordinate_gate_id),
    gates.map((g) => g.id),
  );
  assert.ok(
    plots.every(
      (p) =>
        p.backgate_id === gates[2].id &&
        p.bounds === null &&
        p.overlays.length === 0,
    ),
  );
  assert.equal(new Set(plots.map((p) => p.id)).size, 3);
  assert.equal(JSON.stringify([workspace, current]), before);
  assert.deepEqual(plots[0].graph_options, current.graph_options);
  plots[0].graph_options.gate_style.fill_opacity = 0.1;
  assert.equal(current.graph_options.gate_style.fill_opacity, 0.4);
  assert.equal(plots[1].graph_options.gate_style.fill_opacity, 0.4);
});

test("an explicit backgate takes precedence when the current plot shows an ancestor", () => {
  const plots = backgateAncestryPlots(workspace, {
    ...current,
    gate_id: gates[0].id,
    backgate_id: gates[2].id,
  });
  assert.equal(plots.length, 3);
  assert.ok(plots.every((p) => p.backgate_id === gates[2].id));
});

test("a root population produces one complete all-event ancestry stage", () => {
  const plots = backgateAncestryPlots(workspace, {
    ...current,
    gate_id: gates[0].id,
  });
  assert.equal(plots.length, 1);
  assert.equal(plots[0].gate_id, null);
  assert.equal(plots[0].backgate_id, gates[0].id);
});

for (const damage of [
  "missing",
  "cycle",
  "foreign",
  "parent_missing",
  "parent_foreign",
]) {
  test(`invalid ${damage} ancestry is rejected without modifying inputs`, () => {
    const copy = structuredClone(workspace);
    if (damage === "missing") copy.gates.pop();
    if (damage === "cycle") copy.gates[0].parent_id = gates[2].id;
    if (damage === "foreign") copy.gates[2].sample_id = "e".repeat(32);
    if (damage === "parent_missing") copy.gates.shift();
    if (damage === "parent_foreign") copy.gates[0].sample_id = "e".repeat(32);
    const before = JSON.stringify(copy);
    assert.throws(() => backgateAncestryPlots(copy, current));
    assert.equal(JSON.stringify(copy), before);
  });
}

test("ordered repeated parameters, ratios and compensation survive 3D ancestry", () => {
  const dimensions = [
    {
      channel: "R",
      ratio_channels: ["X", "Y"],
      transform: { kind: "asinh", cofactor: 5 },
      compensation_ref: "uncompensated",
      minimum: 1,
      maximum: 10,
    },
    {
      channel: "X",
      transform: { kind: "linear" },
      compensation_ref: "e".repeat(32),
      minimum: 0,
      maximum: 3,
    },
    {
      channel: "X",
      transform: { kind: "log" },
      compensation_ref: "uncompensated",
      minimum: 1,
      maximum: 100,
    },
  ];
  const box = { ...gates[2], kind: "hyperrectangle", x: null, dimensions };
  const copy = { gates: [gates[0], gates[1], box] };
  const plot = backgateAncestryPlots(copy, current).at(-1);
  assert.equal(plot.mode, "3d");
  assert.deepEqual([plot.x, plot.y, plot.three_d.z], ["R", "X", "X"]);
  assert.deepEqual(plot.x_dimension.ratio_channels, ["X", "Y"]);
  assert.equal(plot.y_dimension.compensation_ref, "e".repeat(32));
  assert.equal(plot.three_d.z_dimension.compensation_ref, "uncompensated");
  assert.equal(plot.three_d.z_transform.kind, "log");
  assert.equal(plot.three_d.z_dimension.minimum, null);
  assert.equal(dimensions[2].minimum, 1);
});

test("boolean stages retain the current explicit axes, camera and coordinate definition", () => {
  const boolean = {
    ...gates[2],
    kind: "boolean",
    x: null,
    y: null,
    dimensions: [],
  };
  const view = {
    ...current,
    mode: "3d",
    coordinate_gate_id: gates[1].id,
    three_d: { z: "Z", yaw: 0.6 },
    x_dimension: { channel: "Y", transform, compensation_ref: "uncompensated" },
  };
  const plot = backgateAncestryPlots(
    { gates: [gates[0], gates[1], boolean] },
    view,
  ).at(-1);
  assert.equal(plot.mode, "3d");
  assert.equal(plot.coordinate_gate_id, gates[1].id);
  assert.equal(plot.x, "Y");
  assert.equal(plot.three_d.yaw, 0.6);
  assert.equal(plot.x_dimension.compensation_ref, "uncompensated");
});

for (const orientation of ["horizontal", "vertical"]) {
  test(`${orientation} ancestry is fully paginated within page margins without partial writes`, () => {
    const original = layout();
    const before = JSON.stringify(original);
    const { pages, placements } = backgateAncestryPlacement(
      original,
      9,
      page,
      orientation,
    );
    assert.equal(placements.length, 9);
    assert.equal(pages.length, orientation === "horizontal" ? 3 : 5);
    assert.equal(placements[0].page, 0);
    assert.deepEqual(
      placements.slice(0, 2).map((p) => [p.x_mm, p.y_mm]),
      orientation === "horizontal"
        ? [
            [12, 36],
            [108, 36],
          ]
        : [
            [12, 36],
            [12, 137],
          ],
    );
    for (const p of placements) {
      assert.ok(p.x_mm >= 12 && p.x_mm + p.width_mm <= 198);
      assert.ok(p.y_mm >= 36 && p.y_mm + p.height_mm <= 273);
    }
    assert.equal(JSON.stringify(original), before);
  });
}

test("new ancestry pages preserve existing objects and selected page dimensions", () => {
  const original = { ...layout(), elements: [{ id: "kept", page: 0 }] };
  const landscape = { ...page, width_mm: 297, height_mm: 210 };
  const result = backgateAncestryPlacement(
    original,
    5,
    landscape,
    "horizontal",
  );
  assert.equal(result.placements[0].page, 1);
  assert.equal(result.pages[0].width_mm, 210);
  assert.equal(result.pages[1].width_mm, 297);
  assert.deepEqual(original.elements, [{ id: "kept", page: 0 }]);
});

for (const invalid of ["zero", "fraction", "tiny", "pages", "elements"]) {
  test(`invalid ${invalid} placement is rejected without a partial ancestry`, () => {
    const original = layout();
    let count = 3;
    let geometry = page;
    if (invalid === "zero") count = 0;
    if (invalid === "fraction") count = 1.5;
    if (invalid === "tiny") geometry = { ...page, width_mm: 30 };
    if (invalid === "pages")
      original.pages = Array.from({ length: 32 }, () => page);
    if (invalid === "elements")
      original.elements = Array.from({ length: 255 }, () => ({ id: "kept" }));
    const before = JSON.stringify(original);
    assert.throws(() =>
      backgateAncestryPlacement(original, count, geometry, "horizontal"),
    );
    assert.equal(JSON.stringify(original), before);
  });
}

test("planar backgate squares use both coordinates and restore the canvas", () => {
  const calls = [];
  const ctx = {
    save: () => calls.push("save"),
    restore: () => calls.push("restore"),
    fillRect: (...v) => calls.push(v),
  };
  drawBackgatePoints(
    ctx,
    [
      [2, 3],
      [4, 5],
    ],
    (x) => x * 10,
    (y) => 100 - y * 10,
    100,
  );
  assert.deepEqual(calls, [
    "save",
    [19, 69, 2.4, 2.4],
    [39, 49, 2.4, 2.4],
    "restore",
  ]);
  assert.equal(ctx.fillStyle, "#f0b96ae0");
});

test("univariate highlights are a bottom rug independent of count or CDF heights", () => {
  const calls = [];
  const ctx = {
    save() {},
    restore() {},
    beginPath() {},
    moveTo: (...v) => calls.push(["move", ...v]),
    lineTo: (...v) => calls.push(["line", ...v]),
    stroke() {},
  };
  drawBackgatePoints(
    ctx,
    [
      [2, 0],
      [4, 0],
    ],
    (x) => x * 10,
    null,
    100,
  );
  assert.deepEqual(calls, [
    ["move", 20, 99],
    ["line", 20, 93],
    ["move", 40, 99],
    ["line", 40, 93],
  ]);
});

test("sampling summary distinguishes finite, visible and drawn counts, including empty populations", () => {
  const format = (n) => String(n);
  assert.equal(backgateSummary(null, format), "");
  assert.equal(backgateSummary({ backgate_count: 0 }, format), "");
  assert.equal(
    backgateSummary(
      {
        backgate_count: 12000,
        backgate_visible_count: 9000,
        backgate_displayed_count: 6000,
        backgate_sampling: "seed 43",
      },
      format,
    ),
    " · 12000 backgate finite · 9000 in axes · 6000 sampled highlights",
  );
  assert.equal(
    backgateSummary(
      {
        backgate_count: 0,
        backgate_visible_count: 0,
        backgate_displayed_count: 0,
      },
      format,
    ),
    " · 0 backgate finite · 0 in axes · 0 highlights",
  );
});
