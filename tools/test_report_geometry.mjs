import assert from "node:assert/strict";
import test from "node:test";
import {
  arrangeReportElements,
  reportObjectBounds,
  selectedReportUnits,
} from "../.tmp/report-geometry-tests/reportGeometry.js";

const object = (id, x, y, width = 10, height = 10, extra = {}) => ({
  id,
  kind: "text",
  page: 0,
  x_mm: x,
  y_mm: y,
  width_mm: width,
  height_mm: height,
  rotation: 0,
  group_id: null,
  position_locked: false,
  text: `source:${id}`,
  ...extra,
});
const arrange = (
  elements,
  action,
  selected = elements.map((item) => item.id),
) => arrangeReportElements(elements, selected, 0, action);
const near = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const freeze = (elements) => Object.freeze(elements.map(Object.freeze));

test("rotated bounds match the four visible corners", () => {
  const bounds = reportObjectBounds(
    object("a", 30, 20, 40, 10, { rotation: 90 }),
  );
  near(bounds.left, 45);
  near(bounds.right, 55);
  near(bounds.top, 5);
  near(bounds.bottom, 45);
  const diagonal = reportObjectBounds(
    object("b", 10, 10, 10, 10, { rotation: 45 }),
  );
  near(diagonal.left, 15 - Math.sqrt(50));
  near(diagonal.bottom, 15 + Math.sqrt(50));
});

for (const [action, x] of [
  ["left", [50, 35]],
  ["center", [77.5, 67.5]],
  ["right", [105, 100]],
]) {
  test(`${action} alignment uses rotated bounds and preserves sources`, () => {
    const source = freeze([
      object("a", 50, 20, 20),
      object("b", 100, 100, 40, 10, { rotation: 90 }),
    ]);
    const next = arrange(source, action);
    next.forEach((item, index) => {
      near(item.x_mm, x[index]);
      assert.equal(item.y_mm, source[index].y_mm);
      assert.equal(item.text, source[index].text);
      assert.equal(item.rotation, source[index].rotation);
    });
    assert.equal(source[1].x_mm, 100);
  });
}

for (const [action, y] of [
  ["top", [20, 20]],
  ["middle", [60, 50]],
  ["bottom", [100, 80]],
]) {
  test(`${action} alignment accounts for unequal object heights`, () => {
    const source = freeze([
      object("a", 20, 20, 10, 10),
      object("b", 80, 80, 10, 30),
    ]);
    const next = arrange(source, action);
    next.forEach((item, index) => {
      near(item.y_mm, y[index]);
      assert.equal(item.x_mm, source[index].x_mm);
    });
  });
}

test("horizontal centers distribute unequal widths between fixed endpoints", () => {
  const source = freeze([
    object("last", 110, 0),
    object("middle", 40, 0, 30),
    object("first", 10, 0),
  ]);
  const next = arrange(source, "distribute-horizontal");
  assert.deepEqual(
    next.map((item) => item.id),
    ["last", "middle", "first"],
  );
  assert.deepEqual(
    next.map((item) => item.x_mm),
    [110, 50, 10],
  );
  assert.equal(next[0], source[0]);
  assert.equal(next[2], source[2]);
});

test("vertical centers distribute unequal heights between fixed endpoints", () => {
  const source = freeze([
    object("a", 20, 10, 10, 20),
    object("b", 40, 40),
    object("c", 70, 100, 10, 40),
  ]);
  const next = arrange(source, "distribute-vertical");
  assert.deepEqual(
    next.map((item) => item.y_mm),
    [10, 65, 100],
  );
  assert.deepEqual(
    next.map((item) => item.x_mm),
    [20, 40, 70],
  );
});

test("horizontal gaps space rotated objects of different sizes", () => {
  const source = freeze([
    object("a", 10, 10),
    object("b", 30, 30, 10, 30, { rotation: 90 }),
    object("c", 110, 10, 20),
  ]);
  const next = arrange(source, "space-horizontal");
  const [a, b, c] = next.map(reportObjectBounds);
  near(b.left - a.right, 30);
  near(c.left - b.right, 30);
  near(a.left, 10);
  near(c.right, 130);
  near(next[1].x_mm, 60);
});

test("vertical gaps retain the selected outer extent", () => {
  const source = freeze([
    object("a", 10, 10),
    object("b", 40, 40, 10, 30),
    object("c", 90, 100, 10, 20),
  ]);
  const next = arrange(source, "space-vertical");
  assert.deepEqual(
    next.map((item) => item.y_mm),
    [10, 45, 100],
  );
  const [a, b, c] = next.map(reportObjectBounds);
  near(b.top - a.bottom, 25);
  near(c.top - b.bottom, 25);
});

test("groups move together even when only one member is selected", () => {
  const source = freeze([
    object("a", 10, 10, 10, 10, { group_id: "g" }),
    object("b", 30, 25, 20, 10, { group_id: "g" }),
    object("c", 100, 80, 30),
  ]);
  const next = arrange(source, "right", ["a", "c"]);
  assert.deepEqual(
    next.map((item) => item.x_mm),
    [90, 110, 100],
  );
  assert.deepEqual(
    next.map((item) => item.y_mm),
    [10, 25, 80],
  );
  assert.equal(next[1].x_mm - next[0].x_mm, 20);
  assert.deepEqual(
    next.map((item) => item.group_id),
    ["g", "g", null],
  );
});

test("one locked member fixes its entire group for alignment and dragging", () => {
  const source = freeze([
    object("a", 10, 10, 10, 10, { group_id: "g" }),
    object("b", 40, 10, 10, 10, { group_id: "g", position_locked: true }),
    object("c", 80, 10),
    object("d", 100, 10),
  ]);
  const units = selectedReportUnits(source, ["a", "c", "d"], 0);
  assert.deepEqual(
    units.map((unit) => unit.locked),
    [true, false, false],
  );
  const next = arrange(source, "left", ["a", "c", "d"]);
  assert.equal(next[0], source[0]);
  assert.equal(next[1], source[1]);
  assert.deepEqual(
    next.map((item) => item.x_mm),
    [10, 40, 80, 80],
  );
});

test("groups and selections on other pages retain their positions", () => {
  const source = freeze([
    object("a", 20, 10, 10, 10, { group_id: "g" }),
    object("other-page", 200, 40, 10, 10, { group_id: "g", page: 1 }),
    object("b", 80, 20),
  ]);
  const next = arrange(source, "left");
  assert.equal(next[1], source[1]);
  assert.deepEqual(
    next.map((item) => item.x_mm),
    [20, 200, 20],
  );
});

test("distribution counts whole movable groups and returns no-op identity", () => {
  const source = freeze([
    object("a", 10, 10, 10, 10, { group_id: "g" }),
    object("b", 30, 10, 10, 10, { group_id: "g" }),
    object("c", 100, 10),
  ]);
  for (const action of [
    "distribute-horizontal",
    "distribute-vertical",
    "space-horizontal",
    "space-vertical",
  ])
    assert.equal(arrange(source, action), source);
  assert.equal(arrange(source, "left", ["a"]), source);
  const aligned = freeze([object("d", 10, 20), object("e", 10, 30)]);
  assert.equal(arrange(aligned, "left"), aligned);
});

test("grouping and ungrouping preserve position locks and include whole groups", () => {
  const source = freeze([
    object("a", 10, 20, 10, 10, { group_id: "old" }),
    object("b", 30, 40, 10, 10, { group_id: "old", position_locked: true }),
    object("c", 100, 200),
  ]);
  const next = arrangeReportElements(source, ["a", "c"], 0, "group", "new");
  assert.deepEqual(
    next.map((item) => item.group_id),
    ["new", "new", "new"],
  );
  const ungrouped = arrange(next, "ungroup", ["a"]);
  assert.deepEqual(
    ungrouped.map((item) => item.group_id),
    [null, null, null],
  );
  ungrouped.forEach((item, index) => {
    assert.equal(item.x_mm, source[index].x_mm);
    assert.equal(item.y_mm, source[index].y_mm);
    assert.equal(item.position_locked, source[index].position_locked);
  });
  assert.throws(
    () => arrangeReportElements(source, ["c"], 0, "group"),
    /identifier/,
  );
});

test("layer ordering keeps other pages at the same array slots and whole groups intact", () => {
  const source = freeze([
    object("a", 10, 10, 10, 10, { group_id: "g", position_locked: true }),
    object("other-page", 10, 10, 10, 10, { page: 1 }),
    object("b", 40, 10),
    object("c", 60, 10, 10, 10, { group_id: "g" }),
  ]);
  const next = arrange(source, "front", ["a"]);
  assert.deepEqual(
    next.map((item) => item.id),
    ["b", "other-page", "a", "c"],
  );
  assert.equal(next[1], source[1]);
  const back = arrange(next, "back", ["c"]);
  assert.deepEqual(
    back.map((item) => item.id),
    ["a", "other-page", "c", "b"],
  );
  assert.equal(arrange(back, "back", ["a"]), back);
});

test("out-of-range geometry fails atomically without clamping individual objects", () => {
  const source = freeze([
    object("a", 0, 20),
    object("b", 30, 20, 40, 10, { rotation: 90 }),
  ]);
  assert.throws(() => arrange(source, "left"), /position range/);
  assert.deepEqual(
    source.map((item) => item.x_mm),
    [0, 30],
  );
});

test("overlapping selections retain their extent with equal negative gaps", () => {
  const source = freeze([
    object("a", 10, 10, 40),
    object("b", 25, 30, 40),
    object("c", 40, 50, 40),
  ]);
  const next = arrange(source, "space-horizontal");
  const [a, b, c] = next.map(reportObjectBounds);
  near(b.left - a.right, -25);
  near(c.left - b.right, -25);
  near(a.left, 10);
  near(c.right, 80);
});
