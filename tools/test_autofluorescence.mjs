import assert from "node:assert/strict";
import test from "node:test";
import {
  afReferenceSettings,
  savedAFReferences,
  reviewedAFReferences,
} from "../.tmp/autofluorescence-tests/autofluorescence.js";

const outputs = ["F1", "AF1", "AF2"];
test("AF roles follow source order and preserve the legacy single-reference format", () => {
  assert.deepEqual(afReferenceSettings(["AF2", "AF1"], outputs), {
    af_output: null,
    af_outputs: ["AF1", "AF2"],
  });
  assert.deepEqual(afReferenceSettings([" AF1 "], outputs), {
    af_output: "AF1",
    af_outputs: [],
  });
  assert.deepEqual(afReferenceSettings([], outputs), {
    af_output: null,
    af_outputs: [],
  });
});
test("AF roles reject duplicates, empty names and outputs outside the spectral calculation", () => {
  for (const names of [["AF1", "AF1"], ["AF1", " AF1 "], [""], ["Missing"]])
    assert.throws(() => afReferenceSettings(names, outputs));
});
test("saved roles reopen for zero, one and multiple references", () => {
  const controls = outputs.map((name) => ({ name }));
  assert.deepEqual(savedAFReferences({ controls }), []);
  assert.deepEqual(
    savedAFReferences({ controls, af_output: "AF1", af_outputs: [] }),
    ["AF1"],
  );
  assert.deepEqual(
    savedAFReferences({ controls, af_outputs: ["AF2", "AF1"] }),
    ["AF1", "AF2"],
  );
  assert.throws(() =>
    savedAFReferences({ controls, af_output: "AF1", af_outputs: ["AF2"] }),
  );
  assert.throws(() => savedAFReferences({ controls, af_outputs: ["Missing"] }));
});
const row = {
  output: "AF1",
  closest_output: "AF2",
  weighted_cosine: -0.99,
  orthogonal_fraction: 0.05,
  separation_angle_degrees: (Math.asin(0.05) * 180) / Math.PI,
  weakly_separated: true,
};
test("weighted AF review accepts signed similarity and consistent separation diagnostics", () => {
  assert.deepEqual(reviewedAFReferences([row], outputs), [row]);
  assert.deepEqual(reviewedAFReferences(undefined, outputs), []);
  const only = {
    ...row,
    closest_output: null,
    weighted_cosine: null,
    orthogonal_fraction: 1,
    separation_angle_degrees: 90,
    weakly_separated: false,
  };
  assert.deepEqual(reviewedAFReferences([only], ["AF1"]), [only]);
});
test("weighted AF review rejects malformed, contradictory and nonfinite scientific results", () => {
  for (const value of [
    "bad",
    [null],
    [row, row],
    ...[
      { output: "Missing" },
      { closest_output: "AF1" },
      { closest_output: null },
      { weighted_cosine: null },
      { weighted_cosine: Infinity },
      { weighted_cosine: -1.1 },
      { orthogonal_fraction: NaN },
      { orthogonal_fraction: -0.01 },
      { orthogonal_fraction: 1.1 },
      { separation_angle_degrees: NaN },
      { separation_angle_degrees: 30 },
      { weakly_separated: false },
      { orthogonal_fraction: "0.05" },
    ].map((change) => [{ ...row, ...change }]),
  ])
    assert.equal(reviewedAFReferences(value, outputs), null);
});
