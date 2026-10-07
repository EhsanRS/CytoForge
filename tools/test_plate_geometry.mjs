import test from "node:test";
import assert from "node:assert/strict";
import {
  plateDimensions,
  platePosition,
  validPlateGeometry,
  wellName,
} from "../.tmp/plate-geometry-tests/plateGeometry.js";
import { plateAcquisitionPlot } from "../.tmp/plate-geometry-tests/platePlotWindows.js";

for (const [format, rows, columns] of [
  [6, 2, 3],
  [12, 3, 4],
  [24, 4, 6],
  [48, 6, 8],
  [96, 8, 12],
  [384, 16, 24],
  [1536, 32, 48],
]) {
  test(`${format} wells use ${rows} rows and ${columns} columns`, () =>
    assert.deepEqual(plateDimensions({ format }), [rows, columns]));
}

test("custom geometry retains row and column order even when total well counts match", () => {
  assert.deepEqual(
    plateDimensions({ format: "custom", geometry: { rows: 2, columns: 3 } }),
    [2, 3],
  );
  assert.deepEqual(
    plateDimensions({ format: "custom", geometry: { rows: 3, columns: 2 } }),
    [3, 2],
  );
});
test("physical dimension limits reject ambiguous, oversized and malformed values", () => {
  for (const value of [
    null,
    [],
    {},
    { rows: 0, columns: 3 },
    { rows: 2.5, columns: 3 },
    { rows: "2", columns: 3 },
    { rows: true, columns: 3 },
    { rows: 96, columns: 17 },
    { rows: 97, columns: 1 },
    { rows: 1, columns: 97 },
    { rows: 2, columns: 3, unknown: 1 },
    Object.create({ rows: 2, columns: 3 }),
  ])
    assert.equal(validPlateGeometry(value), false);
  assert.equal(validPlateGeometry({ rows: 16, columns: 96 }), true);
  assert.throws(() =>
    plateDimensions({ format: 96, geometry: { rows: 8, columns: 12 } }),
  );
  assert.throws(() => plateDimensions({ format: "__proto__" }));
});
test("well coordinates match independent alphabetic positions and round-trip beyond Z", () => {
  for (const [well, row, column] of [
    ["A01", 0, 0],
    ["AA01", 26, 0],
    ["CR96", 95, 95],
    ["a_002", 0, 1],
  ])
    assert.deepEqual(platePosition(well), [row, column]);
  assert.equal(wellName(95, 95), "CR96");
  assert.throws(() => platePosition("A00"));
});

const workspace = {
  id: "0".repeat(32),
  samples: [
    { id: "1".repeat(32), channels: [{ name: "DNA" }] },
    { id: "2".repeat(32), channels: [{ name: "CD3" }, { name: "CD4" }] },
  ],
};
const plate = {
  format: "custom",
  geometry: { rows: 2, columns: 3 },
  assignments: { A01: workspace.samples.map((s) => s.id) },
};
test("each well replicate gets its own full native plot source and compatible parameters", () => {
  const first = plateAcquisitionPlot(
    workspace,
    plate,
    "A01",
    workspace.samples[0].id,
  );
  const second = plateAcquisitionPlot(
    workspace,
    plate,
    "a1",
    workspace.samples[1].id,
  );
  assert.equal(first.mode, "histogram");
  assert.equal(first.x, "DNA");
  assert.equal(first.y, null);
  assert.equal(second.mode, "density");
  assert.equal(second.x, "CD3");
  assert.equal(second.y, "CD4");
  assert.notEqual(first.sampleId, second.sampleId);
  for (const state of [first, second]) {
    assert.equal(state.gateId, null);
    assert.equal(state.coordinateGateId, null);
    assert.equal(state.groupId, null);
    assert.equal(state.pooled, false);
    assert.equal(state.sampleFilter, "");
    assert.equal(state.bounds, null);
  }
});
test("native well opening leaves workspace, assignments and sibling view state intact", () => {
  const before = JSON.stringify({ workspace, plate });
  const first = plateAcquisitionPlot(
    workspace,
    plate,
    "A01",
    workspace.samples[0].id,
  );
  first.graphOptions.palette = "gray";
  const second = plateAcquisitionPlot(
    workspace,
    plate,
    "A01",
    workspace.samples[1].id,
  );
  assert.deepEqual(second.graphOptions, {});
  assert.equal(JSON.stringify({ workspace, plate }), before);
});
test("empty/outside wells, missing acquisitions and missing channels cannot spawn a plot", () => {
  assert.throws(() =>
    plateAcquisitionPlot(workspace, plate, "B01", workspace.samples[0].id),
  );
  assert.throws(() =>
    plateAcquisitionPlot(
      workspace,
      { ...plate, assignments: { C01: [workspace.samples[0].id] } },
      "C01",
      workspace.samples[0].id,
    ),
  );
  assert.throws(() =>
    plateAcquisitionPlot(
      { ...workspace, samples: [] },
      plate,
      "A01",
      workspace.samples[0].id,
    ),
  );
  assert.throws(() =>
    plateAcquisitionPlot(
      {
        ...workspace,
        samples: [{ id: workspace.samples[0].id, channels: [] }],
      },
      plate,
      "A01",
      workspace.samples[0].id,
    ),
  );
});
