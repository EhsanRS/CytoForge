# Native curly quadrants

Choose **Curly quadrants** or press **C** on a two-dimensional fluorescence plot
with nonlinear, invertible axis scales. Click the shared center, review its
coordinates and both noise coefficients, and choose **Create population**.
The tool works in the main desktop and independent native plot windows. Scatter,
time, ratio, computed and arbitrary derived axes, linear display scales and
clipped or WSP lookup scales do not enable native creation.

**Edit gate on plot** on any member opens the entire family's editor. Drag the
shared center or nudge its focused handle with arrow keys; Shift moves ten pixels.
The preview reports each member's count using every eligible parent event.
**Numeric settings** retains the draft and exposes the center and both noise
coefficients. Centers use saved transformed plot coordinates. Coefficients use
the saved scientific columns' intensity units and need review against instrument
settings and controls. Initial coefficients are 1; this is an initial editable
value, not instrument calibration or a recovered vendor default.

Saving changes all four members in one revision and one undoable operation,
retaining IDs, descendants, names and colors. Other native windows update counts
while keeping their own axes and zoom. Cancel preserves the saved family.
Concurrent changes retain the draft and block stale Apply until explicit review.
Propagation creates independent family IDs on target samples; family deletion
and Undo preserve exact definitions. Portable projects and normal desktop restart
retain center, coefficients, convention and complete coordinate definitions.

## Scientific convention

The saved version is `sqrt-positive-intensity-v1`. Let `(cx, cy)` be the center
after applying the inverse saved axis transforms, and let `(ax, ay)` be the
nonnegative noise coefficients. Scientific event columns retain the saved
ordered compensation and ratio definitions before axis display transforms.

```
H(x) = cy + ay * (sqrt(max(x, cx, 0)) - sqrt(max(cx, 0)))
V(y) = cx + ax * (sqrt(max(y, cy, 0)) - sqrt(max(cy, 0)))
Xpositive = x >= V(y)
Ypositive = y >= H(x)
```

Q1 is X negative/Y positive; Q2 is positive on both tests; Q3 is X positive/Y
negative; Q4 is negative on both tests. Exact boundaries belong to the positive
side. The center belongs to Q2. Left and down arms are straight; right and up
limits grow with the square root of positive intensity. Compensated negative
values contribute zero shot-noise intensity. If positive curves cross, the same
two tests still assign exactly one label, and Q4 can extend above or right of
the nominal center. Displayed boundary pieces follow both adjacent populations,
including this crossing region. These explicit rules are the CytoForge convention.

Every finite eligible parent event belongs to one member. Nonfinite raw values
or values whose saved display transform is nonfinite belong to no member. Axis
clipping and WSP lookup/clamp scales are rejected because the center must have an
unambiguous inverse. Display paths are sampled curves; scientific membership
does not use their polygons, the viewport, subsampling or a numerical boundary
tolerance. Near cancellation, exact rational inequalities compare the real
square root without rounding it. Exponent-aligned arithmetic avoids overflow
when finite intensities and coefficients reach opposite extremes. Saved members
share one immutable uint8 label array within the bounded scientific cache;
drafts use a separate engine and cannot replace saved membership.

## Evidence and retained scope

`tests/test_curly_gates.py` verifies independent random labels, exact boundaries
and adjacent floats, negative and extreme intensities, high-precision radical
comparisons, compensation and duplicate-axis ratios, log eligibility, read-only
previews, cache isolation, atomic edits, propagation/delete/undo, portable
projects, CSV/FCS population export, reversed projections and crossing paths.
`tools/desktop_curly_smoke.mjs` exercises the actual native center handle,
shared numeric settings, independent popup counts, stale drafts and restart in
an isolated Linux x64 headless profile with the explicit test sandbox exception
and hardware GPU disabled. `tools/benchmark_curly.py` validates every label and
full-parent preview count on one million events. Its local TestClient timings
include plotting and JSON, exclude socket transport/native rendering and leave
workspace/history unchanged; host activity and OS caches are uncontrolled.

The interaction and square-root noise model draw on
[FlowJo's curly quadrant documentation](https://docs.flowjo.com/flowjo/graphs-and-gating/advanced-gates/gw-gatecurlyquad/).
That public documentation does not specify all coefficient defaults, negative
intensity behavior or crossing ownership. This implementation does not establish
FlowJo numerical equivalence. Vendor comparisons, real-acquisition/FMO
calibration, faithful WSP import and standard GatingML interoperability remain
required. Standard GatingML export currently returns an explicit unsupported
error rather than replacing curly gates with finite polygons. Portable projects
retain exact geometry, and selected-event CSV/FCS uses exact membership.
Broader hardware, normal sandboxed launch, Windows/macOS/ARM validation and
every other retained FlowJo requirement remain in the full project scope.
