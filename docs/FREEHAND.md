# Freehand gates in native desktop plots

Choose **Freehand gate** or press **F** in a planar plot. Click to start and trace
the outline. Returning to the start ring closes it; clicking again or pressing
Enter also closes it. You can instead hold the pointer down, trace and release.
The same tool works in the main desktop and independent plot windows.

The naming dialog reviews the captured polygon before saving. **Edit shape
visually** previews the entire parent population and its exact draft count. Saved
freehand polygons support ordinary vertex editing, movement, scaling, group
propagation, dependent populations, statistics, reports, undo/redo and scientific
exports. Native imported compensation, transform and ratio definitions stay
attached to their ordered axes. Portable projects retain the drawing provenance;
standard GatingML represents the scientific polygon and its coordinate definitions.

The trace samples the pointer path at two CSS-pixel intervals, including available
coalesced PointerEvents. Those recorded scientific vertices are saved without
further simplification. The start ring marks the closing tolerance. Trace movement
is bounded to the visible plot; the review dialog closes the last recorded vertex
back to the first. Polygons use the engine's existing even-odd rule with included
boundaries, so a self-crossing outline can select multiple lobes. Zero signed
shoelace area no longer rejects such an outline; collinear legacy shapes remain
invalid. A stationary click cannot create a population.

The counter shows the server's 2,000-vertex limit. Exceeding it blocks finishing
and saving; the software does not truncate or simplify a partial outline. Cancel
and redraw after zooming out to reduce the number of sampled vertices. **Cancel
freehand gate**, Escape and pointer cancellation discard the unsaved trace.

Starting a trace locks source navigation and activates native close protection.
Workspace/coordinate changes and plot resizing retain the outline and block
finishing, including after the old size is restored. Cancel and redraw against
the current view. Histogram/CDF and 3D views disable this planar tool and its
shortcut. The naming dialog continues to check the expected workspace revision.

Dense float64 outlines use conservative Y-interval indexing and bounding-box
selection before applying the original boundary/crossing predicate. Small inputs,
other numeric dtypes, unstable coordinate spans and heavily overlapping edge
intervals retain the vectorized path. This changes the amount of work, preserving
the scientific rule. Complex repeated scribbles can still require the original
cost and remain part of the full performance work.

`tests/test_freehand_gates.py` checks independently labelled self-crossing masks,
full-parent read-only previews, saved child membership, undo/redo, coordinate
scales, dense rectangle/C/bow-tie populations, original boundary tolerances and
array shapes. `tools/desktop_freehand_smoke.mjs` checks ten actual native main/popup
workflows: closure methods, exact labelled populations, draft guards, cancellation,
limit rejection, unavailable display modes, fixed compensation/ratio coordinates,
ordinary editing and restart. Its pen-cancellation case is a synthetic browser
event, not a physical device test.

`tools/benchmark_freehand.py` verifies all one million event memberships for
2,000-vertex rare, dense and spread populations in five repetitions. The record
is `artifacts/benchmark-freehand-mask.json`; the original one-call comparison is
`artifacts/benchmark-freehand-mask-before-index.json`. These measure direct
scientific masks with allocation, excluding API/transport/rendering; host activity
and OS caches are uncontrolled.

The input workflow follows [FlowJo's documented freehand drawing tool](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-gating/gw-gatedrawing).
Complete numerical/feature parity, bisectors, spider/curly quadrants, automatic
gating, physical pen/touch coverage, broader acquisitions and platform/installer
validation remain in the retained full objective.
