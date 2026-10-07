import assert from "node:assert/strict";
import test from "node:test";
import {
  autoSpillOutputs,
  selectAutoSpillDetectors,
  spectralControlNames,
  spectralDetectorVector,
} from "../.tmp/spectral-autospill-tests/spectralAutoSpill.js";

const controls = [
  { name: "Fluor1", primary_detector: "D1", sample_id: "a", gate_id: "g" },
  { name: "Fluor2", primary_detector: "D1", sample_id: "b", gate_id: null },
];

test("conventional review retains acquired output identities in old reports", () => {
  assert.deepEqual(autoSpillOutputs({ detectors: ["D2", "D1"], controls }), [
    "D2",
    "D1",
  ]);
});

test("spectral review keeps source order separate from the measured detector order", () => {
  assert.deepEqual(
    autoSpillOutputs({
      kind: "spectral",
      detectors: ["D2", "D1", "D3"],
      controls,
    }),
    ["Fluor1", "Fluor2"],
  );
});

test("spectral detector selection preserves populations and shared peak detectors", () => {
  const selected = selectAutoSpillDetectors(
    ["D3", "D1", "D4"],
    controls,
    "spectral",
  );
  assert.deepEqual(selected, controls);
  assert.equal(selected.length, 2);
  assert.notEqual(selected, controls);
});

test("removing a peak reassigns its sources while retaining their sample and population", () => {
  const selected = selectAutoSpillDetectors(["D3", "D4"], controls, "spectral");
  assert.deepEqual(
    selected,
    controls.map((control) => ({ ...control, primary_detector: "D3" })),
  );
  assert.equal(controls[0].primary_detector, "D1");
});

test("conventional selection still creates exactly one control per detector", () => {
  const selected = selectAutoSpillDetectors(
    ["D2", "D1"],
    controls,
    "spillover",
  );
  assert.deepEqual(selected[0], {
    name: "D2",
    primary_detector: "D2",
    sample_id: "",
    gate_id: null,
  });
  assert.deepEqual(selected[1], controls[0]);
});

test("detector weights and backgrounds follow names through reordering and removal", () => {
  assert.deepEqual(
    spectralDetectorVector(["D3", "D1", "D2"], { D1: 7, D2: 0.2 }, 1),
    [1, 7, 0.2],
  );
  assert.deepEqual(
    spectralDetectorVector(["D2", "D3"], { D1: -70, D2: 34 }, 0),
    [34, 0],
  );
  assert.deepEqual(spectralDetectorVector(["D4"], { D1: 7 }, 1), []);
});

test("nonfinite backgrounds and invalid inverse variances fail before submission", () => {
  for (const value of [0, -1, Infinity, NaN])
    assert.throws(
      () => spectralDetectorVector(["D1"], { D1: value }, 1),
      /finite and positive/,
    );
  assert.throws(
    () => spectralDetectorVector(["D1"], { D1: Infinity }, 0),
    /finite/,
  );
  assert.deepEqual(spectralDetectorVector(["D1"], { D1: -20 }, 0), [-20]);
});

test("only explicit detector properties supply settings, including prototype-like names", () => {
  assert.deepEqual(
    spectralDetectorVector(["constructor", "toString"], {}, 1),
    [],
  );
  assert.deepEqual(
    spectralDetectorVector(
      ["__proto__", "constructor"],
      { ["__proto__"]: 4 },
      1,
    ),
    [4, 1],
  );
  assert.deepEqual(
    spectralDetectorVector(["D1"], Object.create({ D1: 3 }), 1),
    [],
  );
});

test("spectral output naming prevents acquired collisions and duplicate source names", () => {
  const renamed = spectralControlNames(
    [
      { ...controls[0], name: "D1" },
      { ...controls[1], name: "D1" },
      { ...controls[0], name: "Kept name" },
    ],
    ["D1", "Unmixed D1"],
  );
  assert.deepEqual(
    renamed.map((control) => control.name),
    ["Unmixed D1 2", "Unmixed D1 3", "Kept name"],
  );
  assert.equal(renamed[0].sample_id, "a");
  assert.equal(renamed[0].gate_id, "g");
});
