import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import {
  canvasGraphText,
  graphTextCSS,
  nativePlotPadding,
  patchGraphText,
  scientificGraphOptions,
} from "../.tmp/graph-typography-tests/graphTypography.js";
const { validState } = createRequire(import.meta.url)(
  "../desktop/plot-windows.cjs",
);
const state = {
  workspaceId: "a".repeat(32),
  sampleId: "b".repeat(32),
  gateId: null,
  x: "X",
  y: "Y",
  mode: "density",
};

test("12 physical points become 16 CSS pixels and use the bundled face", () => {
  const options = {
    typography: {
      axis_labels: {
        font_size_pt: 12,
        font_family: "serif",
        font_weight: "bold",
        font_style: "italic",
        color: "#112233",
      },
    },
  };
  assert.deepEqual(canvasGraphText(options, "axis_labels", 11, "#abcdef"), {
    font: 'italic bold 16px "CytoForge Serif"',
    pixels: 16,
    color: "#112233",
  });
  assert.equal(graphTextCSS(options, "axis_labels").fontSize, "12pt");
});
test("inherited fonts keep legacy sizes, colors and labels", () => {
  assert.deepEqual(canvasGraphText({}, "tick_labels", 11, "#112233"), {
    font: "normal normal 11px Inter, system-ui",
    pixels: 11,
    color: "#112233",
  });
  assert.deepEqual(
    graphTextCSS({ typography: { title: { color: null } } }, "title"),
    {},
  );
  assert.deepEqual(nativePlotPadding({}), {
    left: 64,
    top: 24,
    right: 24,
    bottom: 54,
  });
});
test("font edits produce identical scientific requests", () => {
  const options = {
    sigma: 2,
    palette: "viridis",
    axis_extent: "full",
    typography: { axis_labels: { font_size_pt: 12 } },
  };
  assert.deepEqual(scientificGraphOptions(options), {
    sigma: 2,
    palette: "viridis",
    axis_extent: "full",
  });
  assert.deepEqual(
    scientificGraphOptions(
      patchGraphText(options, "axis_labels", { font_size_pt: 40 }),
    ),
    scientificGraphOptions(options),
  );
  assert.equal(options.typography.axis_labels.font_size_pt, 12);
});
test("resetting a role preserves other fonts and numerical options", () => {
  const options = {
    point_limit: 50000,
    typography: {
      axis_labels: { font_size_pt: 12 },
      legend: { font_family: "mono" },
    },
  };
  assert.deepEqual(
    patchGraphText(options, "axis_labels", { font_size_pt: null }),
    { point_limit: 50000, typography: { legend: { font_family: "mono" } } },
  );
  assert.deepEqual(
    patchGraphText(
      { point_limit: 50000, typography: { title: { color: "#123456" } } },
      "title",
      { color: null },
    ),
    { point_limit: 50000 },
  );
});
test("measured large fonts increase margins without changing event coordinates", () => {
  const large = nativePlotPadding(
    {
      typography: {
        axis_labels: { font_size_pt: 40 },
        tick_labels: { font_size_pt: 30 },
      },
    },
    180,
    100,
  );
  assert.ok(
    large.left >= 180 + (40 * 4) / 3 && large.bottom > 100 && large.right >= 56,
  );
  const oversized = nativePlotPadding(
    {
      typography: {
        axis_labels: { font_size_pt: 144 },
        tick_labels: { font_size_pt: 144 },
      },
    },
    500,
    400,
  );
  assert.ok(600 - oversized.left - oversized.right < 40);
});
for (const role of [
  "axis_labels",
  "tick_labels",
  "gate_labels",
  "statistics",
  "legend",
  "title",
]) {
  test(`native plot window accepts and copies validated ${role} fonts`, () => {
    const value = {
      ...state,
      graphOptions: {
        typography: {
          [role]: {
            font_size_pt: 12.5,
            font_family: "serif",
            font_weight: "bold",
            font_style: "italic",
            color: "#123456",
          },
        },
      },
    };
    assert.equal(validState(value), true);
    assert.equal(validState(JSON.parse(JSON.stringify(value))), true);
  });
}
for (const [label, typography] of [
  ["unknown role", { unknown: { font_size_pt: 12 } }],
  ["unknown field", { title: { url: "private" } }],
  ["array", []],
  ["array style", { title: [] }],
  ["boolean", { title: { font_size_pt: true } }],
  ["numeric string", { title: { font_size_pt: "12" } }],
  ["infinity", { title: { font_size_pt: Infinity } }],
  ["too small", { title: { font_size_pt: 3 } }],
  ["too large", { title: { font_size_pt: 145 } }],
  ["arbitrary face", { title: { font_family: "url(private)" } }],
  ["injected color", { title: { color: "#123456; display:none" } }],
  ["prototype field", JSON.parse('{"title":{"__proto__":{"x":1}}}')],
])
  test(`native IPC rejects ${label} without throwing`, () =>
    assert.equal(
      validState({ ...state, graphOptions: { typography } }),
      false,
    ));
