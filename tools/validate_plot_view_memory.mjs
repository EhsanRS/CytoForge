import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";
const { viewMemory } = createRequire(import.meta.url)(
  "../desktop/plot-view-memory.cjs",
);
const valid = (v) =>
  v &&
  /^[a-f0-9]{32}$/.test(v.workspaceId) &&
  /^[a-f0-9]{32}$/.test(v.sampleId) &&
  (v.gateId === null || /^[a-f0-9]{32}$/.test(v.gateId)) &&
  Array.isArray(v.bounds);
const state = (i, overrides = {}) => ({
  workspaceId: "a".repeat(32),
  sampleId: "b".repeat(32),
  gateId: i === null ? null : i.toString(16).padStart(32, "0"),
  bounds: [0, 5, 0, 5],
  ...overrides,
});
const memory = viewMemory(valid);
const original = state(null);
memory.remember(original);
original.bounds[0] = 99;
assert.equal(
  memory.candidates(original.workspaceId, original.sampleId)[0].bounds[0],
  0,
);
const returned = memory.serialize();
returned[0].bounds[0] = 88;
assert.equal(memory.latest(original.workspaceId).bounds[0], 0);
for (let i = 0; i < 96; i++) memory.remember(state(i));
assert.equal(memory.size, 96);
assert(!memory.serialize().some((v) => v.gateId === null));
memory.remember(state(0, { bounds: [-1, 7, -2, 9] }));
memory.remember(state(96));
assert(memory.serialize().some((v) => v.gateId === state(0).gateId));
assert(!memory.serialize().some((v) => v.gateId === state(1).gateId));
const restored = viewMemory(valid, memory.serialize());
assert.deepEqual(restored.serialize(), memory.serialize());
restored.remember(state(0, { bounds: [1, 2, 3, 4] }));
assert.notDeepEqual(restored.serialize(), memory.serialize());
restored.remember(state(0, { sampleId: "c".repeat(32) }));
restored.remember(state(0, { workspaceId: "d".repeat(32) }));
assert.equal(restored.candidates("a".repeat(32), "c".repeat(32)).length, 1);
assert.equal(restored.candidates("d".repeat(32), "b".repeat(32)).length, 1);
assert.equal(restored.candidates("e".repeat(32), "b".repeat(32)).length, 0);
assert.throws(() => viewMemory(valid, [state(0), state(0)]));
assert.throws(() => viewMemory(valid, [state(0, { sampleId: "../file" })]));
assert.throws(() => viewMemory(valid, "not an array"));
assert.throws(() =>
  viewMemory(
    valid,
    Array.from({ length: 97 }, (_, i) => state(i)),
  ),
);
const main = viewMemory(valid, [], 192);
for (let i = 0; i < 250; i++) main.remember(state(i));
assert.equal(main.size, 192);
assert.equal(main.serialize()[0].gateId, state(58).gateId);
const evidence = {
  status: "passed",
  checks: [
    "Inputs and returned views cannot mutate stored history",
    "Popup histories retain at most 96 populations; least recently used view is evicted",
    "Revisiting a population refreshes its recency and settings",
    "Restoration and duplication preserve history while later edits remain independent",
    "Samples and workspaces cannot collide or leak into source-specific candidates",
    "Duplicate, malformed and oversized stored histories are rejected",
    "Main workspace history is independently bounded to 192 populations",
  ],
};
writeFileSync(
  "artifacts/plot-view-memory-validation.json",
  JSON.stringify(evidence, null, 2),
);
console.log(JSON.stringify(evidence));
