import assert from "node:assert/strict";
import test from "node:test";
import {
  spreadingPreset,
  initialSpreadingControls,
} from "../.tmp/spreading-tests/spreading.js";

const sample = "a".repeat(32),
  gate = "b".repeat(32);
const matrix = { outputs: ["F1", "AF"], kind: "spectral", provenance: {} };
test("median calculations prefill each named AF reference, including empty legacy list fields", () => {
  const controls = [
    { name: "F1", positive: { sample_id: sample, gate_id: gate } },
  ];
  const references = [
    { name: "AF1", population: { sample_id: sample, gate_id: null } },
    { name: "AF2", population: { sample_id: "c".repeat(32), gate_id: gate } },
  ];
  const rows = initialSpreadingControls({
    ...matrix,
    outputs: ["F1", "AF1", "AF2"],
    provenance: {
      kind: "control_calculation",
      request: { controls, autofluorescence_controls: references },
    },
  });
  assert.deepEqual(
    rows.map((r) => [r.output, r.sample_id]),
    [
      ["F1", sample],
      ["AF1", sample],
      ["AF2", "c".repeat(32)],
    ],
  );
  const single = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "control_calculation",
      request: {
        controls,
        autofluorescence_controls: [],
        autofluorescence: { ...references[0], name: "AF" },
      },
    },
  });
  assert.equal(single[1].sample_id, sample);
});
test("median spreading uses captured threshold gates and never discards an unsaved threshold", () => {
  const threshold = {
    sample_id: sample,
    gate_id: gate,
    threshold_channel: "RawReference",
    minimum: 1,
  };
  const request = {
    controls: [{ name: "F1", positive: threshold }],
    autofluorescence: { name: "AF", population: threshold },
  };
  const provenance = { kind: "control_calculation", request };
  const empty = initialSpreadingControls({ ...matrix, provenance });
  assert.ok(empty.every((c) => c.sample_id === "" && c.gate_id === null));
  const captured = "d".repeat(32);
  const rows = initialSpreadingControls({
    ...matrix,
    provenance: {
      ...provenance,
      control_populations: [
        { output: "F1", sample_id: sample, gate_id: captured },
      ],
    },
  });
  assert.deepEqual(rows[0], {
    output: "F1",
    sample_id: sample,
    gate_id: captured,
  });
  assert.equal(rows[1].sample_id, "");
});
test("AutoSpill prefers saved acquired populations for each output, including shared peaks", () => {
  const captured = "d".repeat(32);
  const rows = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "autospill_calculation",
      control_populations: [
        { output: "AF", sample_id: "c".repeat(32), gate_id: null },
        { output: "F1", sample_id: sample, gate_id: captured },
      ],
      request: {
        controls: [
          {
            name: "F1",
            primary_detector: "D1",
            sample_id: sample,
            gate_id: gate,
          },
          {
            name: "AF",
            primary_detector: "D1",
            sample_id: sample,
            gate_id: gate,
          },
        ],
      },
    },
  });
  assert.deepEqual(rows, [
    { output: "F1", sample_id: sample, gate_id: captured },
    { output: "AF", sample_id: "c".repeat(32), gate_id: null },
  ]);
});
test("malformed or ambiguous captured populations fall back to the original controls", () => {
  for (const control_populations of [
    "bad",
    [null, 3, {}],
    [{ output: "F1", sample_id: sample, gate_id: "bad" }],
    [
      { output: "F1", sample_id: sample, gate_id: gate },
      { output: "F1", sample_id: "c".repeat(32), gate_id: null },
    ],
    [{ output: "Changed", sample_id: sample, gate_id: "d".repeat(32) }],
  ]) {
    const rows = initialSpreadingControls({
      ...matrix,
      provenance: {
        kind: "autospill_calculation",
        control_populations,
        request: {
          controls: [
            {
              name: "F1",
              primary_detector: "D1",
              sample_id: sample,
              gate_id: gate,
            },
          ],
        },
      },
    });
    assert.deepEqual(rows[0], {
      output: "F1",
      sample_id: sample,
      gate_id: gate,
    });
  }
});
test("spectral AutoSpill uses source names when controls share an acquired peak", () => {
  const rows = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "autospill_calculation",
      request: {
        controls: [
          {
            name: "F1",
            primary_detector: "D1",
            sample_id: sample,
            gate_id: gate,
          },
          {
            name: "AF",
            primary_detector: "D1",
            sample_id: "c".repeat(32),
            gate_id: null,
          },
        ],
      },
    },
  });
  assert.deepEqual(rows, [
    { output: "F1", sample_id: sample, gate_id: gate },
    { output: "AF", sample_id: "c".repeat(32), gate_id: null },
  ]);
});
const request = {
  algorithm: "autospread",
  name: "Reviewed spreading",
  controls: [{ output: "F1", sample_id: sample, gate_id: gate }],
  quantiles: 32,
  events_per_bin: 120,
  significance: 0.001,
  max_events: 1024,
};
test("a reviewed spreading report takes precedence over captured AutoSpill populations", () => {
  const rows = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "autospill_calculation",
      autospread: { request },
      control_populations: [
        { output: "F1", sample_id: "c".repeat(32), gate_id: null },
      ],
    },
  });
  assert.deepEqual(rows[0], request.controls[0]);
});
// Use an event limit that provides the required eight bins of 120 events.
test("reuse preserves the complete reviewed scientific settings and selected subset", () => {
  assert.deepEqual(spreadingPreset({ request }, matrix.outputs), {
    name: request.name,
    controls: request.controls,
    quantiles: 32,
    events_per_bin: 120,
    significance: 0.001,
    max_events: 1024,
  });
});
test("reusing a request copies its controls without mutating the saved report", () => {
  const preset = spreadingPreset({ request }, matrix.outputs);
  preset.controls[0].gate_id = null;
  assert.equal(request.controls[0].gate_id, gate);
});
test("saved controls take priority over inferred median populations", () => {
  const result = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "control_calculation",
      autospread: { request },
      request: {
        controls: [{ name: "F1", positive: { sample_id: "c".repeat(32) } }],
      },
    },
  });
  assert.deepEqual(result[0], request.controls[0]);
  assert.deepEqual(result[1], { output: "AF", sample_id: "", gate_id: null });
});
test("spectral autofluorescence prefills the saved unstained population", () => {
  const result = initialSpreadingControls({
    ...matrix,
    provenance: {
      kind: "control_calculation",
      request: {
        controls: [
          { name: "F1", positive: { sample_id: sample, gate_id: gate } },
        ],
        autofluorescence: {
          name: "AF",
          population: { sample_id: "c".repeat(32), gate_id: null },
        },
      },
    },
  });
  assert.deepEqual(result, [
    { output: "F1", sample_id: sample, gate_id: gate },
    { output: "AF", sample_id: "c".repeat(32), gate_id: null },
  ]);
});
test("conventional AutoSpill matches primary detector identities", () => {
  const result = initialSpreadingControls({
    ...matrix,
    kind: "spillover",
    provenance: {
      kind: "autospill_calculation",
      request: {
        controls: [
          { primary_detector: "F1", sample_id: sample, gate_id: gate },
        ],
      },
    },
  });
  assert.deepEqual(result[0], request.controls[0]);
});
test("changed output identities prevent implicit reuse of a stale panel", () => {
  assert.equal(spreadingPreset({ request }, ["Renamed", "AF"]), null);
});
test("invalid event limits and duplicate primary controls cannot become presets", () => {
  assert.equal(
    spreadingPreset(
      { request: { ...request, max_events: 959 } },
      matrix.outputs,
    ),
    null,
  );
  assert.equal(
    spreadingPreset(
      {
        request: {
          ...request,
          controls: [...request.controls, ...request.controls],
        },
      },
      matrix.outputs,
    ),
    null,
  );
});
test("malformed imported provenance remains a usable empty control form", () => {
  for (const provenance of [
    null,
    { request: { controls: "bad" } },
    { autospread: { request: "bad" } },
    { request: { controls: [null, 3, {}] }, kind: "control_calculation" },
  ]) {
    assert.doesNotThrow(() =>
      initialSpreadingControls({ ...matrix, provenance }),
    );
    assert.equal(
      initialSpreadingControls({ ...matrix, provenance })[0].sample_id,
      "",
    );
  }
});
