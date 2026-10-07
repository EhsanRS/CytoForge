# Visual gate editing in desktop windows

Select a population and choose **Edit gate on plot**, or double-click a visible
gate with the **Select** tool. Shape handles appear directly in the primary plot
panel in both the main desktop window and individual plot windows. Borders take
precedence over overlapping filled gates. **Apply gate edit** saves the draft;
**Cancel gate edit** returns to the original view without changing the workspace.

**Numeric settings** carries the draft and its revision into the detailed editor.
You can also choose **Edit selected gate**, then **Edit shape visually** in that
dialog. The detailed editor accepts unsaved geometric gates in the creation dialog.

The plot shows the entire parent population in the gate's own coordinate system.
Its saved transforms, compensation references, ratio expressions and output
clamps define those coordinates. Editing uses the original geometry rather than
converting a displayed approximation into a new scientific gate.
Axes retain their ordered definitions even when both use the same measured
parameter with different compensation references. The preview labels those
references so the coordinate basis remains visible.

- Drag inside the tinted shape to move it.
- Drag a boundary or corner handle to resize a range or rectangle. Imported
  hyperrectangles retain their unbounded sides. Quadrants retain their quadrant
  number and boundary convention while their thresholds move.
- Drag polygon vertices. Double-click a selected edge to insert a vertex. Focus
  a vertex and press Delete or Backspace to remove it; at least three vertices
  remain required. All 2,000 supported vertices remain available for editing.
- Ellipses have handles along both axes and an outer rotation handle. Imported
  symmetric positive definite ellipsoids retain their dimensions and distance
  threshold while their covariance changes with the requested geometry.
- Focus a shape or handle and use arrow keys to nudge it by one display pixel;
  Shift moves ten. **Expand 5%** and **Contract 5%** scale finite shapes about
  their center. Unbounded gates use their defined boundary handles.
- **Fit gate** zooms the preview view to the geometry. Histogram and
  CDF previews support one-dimensional gates; all five planar graph modes support
  bivariate previews.

For magnetic gates, the editable shape is explicitly the **original anchor**.
The shared scientific engine resolves its following position, and the count
includes that resolved position. Visual editing preserves the magnetic setting.
See [MAGNETIC_GATES.md](MAGNETIC_GATES.md) for the numerical convention and bounds.

The full-event draft count refreshes after a short debounce. Pointer movement
updates the local shape immediately while the scientific preview runs. Pending,
failed and stale previews show their state instead of presenting old counts as
current. Nonfinite coordinate events and complements follow the ordinary gate
engine's semantics; the parent denominator includes every selected parent event.
The visible-event count is reported separately from that full parent denominator.

Changes remain a draft until **Apply gate edit** or the dialog's **Save population**.
Saving uses the ordinary atomic
gate mutation and updates dependent gates, statistics and other native windows.
Undo and redo preserve that scientific change. Save is disabled during a drag.
Escape cancels an active drag and restores its original draft geometry. Closing
the application continues to use the existing unsaved-gate protection.
A window resize or other viewport change also cancels an active drag before its
coordinate mapping changes. Geometry edits retain concurrent local name, color,
parent and coordinate-setting changes in the dialog.
The inline editor retains the original sample, axes, graph settings and zoom;
its parent preview does not replace these saved view settings. Sample, population
and workspace navigation stay locked until the draft is applied or canceled.
Switching to numeric settings keeps the native unsaved-gate close protection.

An external workspace edit retains the local draft and disables its controls and
preview. Review the current saved population, then choose **Keep draft and use
current workspace** to deliberately rebase it. A removed source must be restored
before an existing gate can be saved. Old preview responses cannot replace a
newer draft or coordinate basis.

Freehand creation is described in [FREEHAND.md](FREEHAND.md).
Linked bisector/quadrant thresholds, counts, atomic edits and family deletion are
described in [PARTITIONS.md](PARTITIONS.md).

## Scientific isolation and verification

The authenticated `POST /api/workspaces/{id}/gates/preview-shape` route validates
the gate and the complete candidate workspace graph before computing a preview.
It does not mutate the workspace, history, jobs or event files. Each request uses
an isolated scientific engine with a 64 MiB cache so draft masks with the same
gate ID and workspace revision cannot contaminate saved populations. The preview
projects only the edited gate; unrelated sibling magnetic searches are excluded.
Normal exploration and report overlays retain their previous behavior.

Fifteen source cases cover independently labelled membership for ranges,
rectangles, polygons, rotated ellipses, hyperrectangles, ellipsoids and quadrants;
complements, parent/child masks, saving and undo; raw ratios and zero denominators;
fixed compensation overriding the sample matrix; distinct axis references for
one measured parameter; bounded native transforms;
magnetic anchors; empty/unsaved populations; legacy range metadata; stale,
unsupported and cross-sample requests; authentication, origin and host guards.
Evidence: `artifacts/pytest-shape-editor-axis-regression.log` and
`artifacts/pytest-ordered-coordinates-full.log` (696 scientific/API cases).

`tools/desktop_gate_editing_smoke.mjs` exercises actual native pointer/keyboard
input in fourteen workflows, synchronized windows, dependent membership,
revisions, imported covariance, distinct axis references, null limits, CDF
denominators, dense polygons, drag/resize cancellation and restart. Proofs
record their binary and explicit headless sandbox exception. Native pointer
coordinates have browser rounding; scientific membership is evaluated against
the actual resulting geometry, not a rounded display or event sample.

`tools/desktop_inline_gate_smoke.mjs` adds nine native main/popup workflows:
exact independently labelled draft and dependent memberships; saved-border
selection through overlapping fills; original-view recovery; canceled and
keyboard gestures; native close protection; concurrent revisions; numeric-editor
handoff; removed populations and source samples; and desktop restart. The
scientific backend is unchanged from the recorded 696-case suite, verified by
source hashes. UI and installer evidence are recorded for the current build.

The million-event ASGI benchmark records complete request/model validation,
fresh draft masks, parent plots and response-byte consumption. Five repetitions
per shape produce identical complete response bytes and exactly 100,003 labelled
target events from one million parents. On this host the measured medians were
48 ms for range, 160 ms for rectangle, 180 ms for ellipse and 233 ms for polygon.
OS caches and other host activity were uncontrolled; socket transport, native
rendering, body construction and result parsing were excluded. The record is
`artifacts/benchmark-gate-editing.json`.

## Remaining scope

This editor supports one- and two-dimensional geometric gates. General
nonsymmetric or indefinite quadratic forms retain their exact numeric editor;
they are not silently replaced with symmetric ellipses. Finite handle geometry
and viewport representation are required. Linked spider center/arm controls are
described in [Spider gates](SPIDER_GATES.md); bisectors and automatic density gates
are described in [Partitions](PARTITIONS.md) and [Autogating](AUTOGATING.md).
Shared-center curly controls and their reviewed raw-unit noise coefficients are
described in [Curly quadrants](CURLY_GATES.md). Vendor comparisons, higher-dimensional
surfaces, additional styling and broader real-acquisition
and extreme-coordinate comparisons remain part of the full objective.

The interaction requirements were checked against the primary
[FlowJo gate-editing documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-gating/gw-gatechanging)
and [drawing documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-gating/gw-gatedrawing).
This evidence does not establish complete FlowJo numerical or feature parity,
normal sandboxed Linux startup, Windows/macOS/ARM operation or hardware GPU
performance.
Polygon gates can contain explicit excluded rings. Move/scale acts on all rings;
each excluded vertex has its own pointer and keyboard handle. Double-click a ring
edge to insert a vertex; Delete retains at least three vertices in that ring.
Numeric settings can add, edit or remove exclusions. The total limit is 2,000
vertices across all rings and 128 excluded rings. Overlapping exclusions remove
their union, including their boundaries. Automatic density suggestions retain
these rings through the ordinary native editor; see [AUTOGATING.md](AUTOGATING.md).
