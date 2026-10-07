# Automatic density gates

In a two-dimensional native main or popup plot, choose **Automatic density gate**
(**A**) and click inside a population. Adjust **Density coverage**, **Smoothing**
and **Resolution**. Review the outline and full-event count, then choose **Use
automatic gate** or press Enter while the plot has focus. The normal population
dialog lets you name the gate, inspect numeric coordinates and edit its geometry
before saving. Escape or **Cancel automatic gate** discards the suggestion.
Histogram, CDF and 3D views do not enable this tool.

Coverage is a highest-density probability target for all finite events in the
selected parent, across all density regions. It is not a requested percentage
for the single clicked region. The preview separately reports that region's
actual polygon event count, parent denominator, excluded rings and vertex count.
The density uses every finite parent event, including events outside the displayed
viewport. Optional density bounds use four scientific-coordinate limits. Requests
with insufficient finite-parent mass or a selected contour touching these bounds
are rejected explicitly; enlarging the bounds or adjusting the density settings
can resolve them.

The numerical convention is `highest-density-seeded-contour-v1`: a normalized
two-dimensional histogram, optional Gaussian smoothing in bins with retained
finite mass, and an inclusive highest-density threshold. Equal-density bins stay
together and the achieved probability can exceed the requested target. Smoothed
contours interpolate bin centers. Unsmoothed fields and peak thresholds follow
exact bin-cell boundaries, using four-connected components. Only the region
containing the clicked seed is selected. Other components are not joined by a
convex hull. There is no vertex truncation or geometric simplification: a contour
exceeding 2,000 total vertices or 128 excluded rings requires a lower resolution
or more smoothing.

The accepted gate is a static polygon with explicit excluded rings. It retains
ordered transforms, compensation references and ratio definitions from the native
plot coordinates, including different definitions with the same axis label.
Moving or scaling a gate moves every ring together. Individual excluded vertices
can be dragged or moved with arrow keys in the native plot editor; double-clicking
a ring edge inserts a vertex and Delete removes a selected vertex while retaining
at least three. Numeric settings can add, edit or remove excluded rings. Ring
interiors and boundaries are excluded, including overlaps between excluded rings.
The preview and saved counts use the same full-event scientific predicate.

Preview requests do not change the workspace, history or saved masks. Density
fields share the bounded, revision-keyed array cache. Parameter changes debounce
and cancel outdated requests; accepting is disabled until the current request
finishes. Workspace or coordinate changes retain the outline and block saving.
Source navigation and native close are protected until the draft is accepted or
cancelled. Saving uses the ordinary revision-checked population mutation.

Portable projects retain the native polygon, excluded rings and original density
provenance. GatingML 2.0 exports an ordinary Boolean strategy: the outer polygon
AND the complement of every excluded polygon, retaining the parent and any gate
complement. Standard import preserves this strategy and scientific membership;
it exposes Boolean/helper populations rather than reconstructing a native hole
editor. Canvas/PNG and vector/PDF graph exports draw every ring. The stored
provenance records the original suggestion; later manual geometry edits do not
rerun the density algorithm.

Twenty independent source/API cases cover event boundaries, overlapping holes,
parent/complement behavior, disconnected components, density rings, unsmoothed
peaks, interpolated topology, nonfinite and extreme coordinates, readonly caches,
revision rejection, ordered transformed ratios and schema-valid GatingML
membership. Nine isolated native desktop checks cover main/popup interaction,
review, excluded vertex edits, close/stale guards, undo/redo and restart. Evidence
for source, packaged and live builds is recorded separately.

FlowJo's documented density-contour autogating interaction is a workflow reference:
[Graph Window gating documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-gatedrawing).
Its numerical algorithm is not reproduced or claimed equivalent. Real-acquisition
comparisons, exhaustive controls, spider/curly quadrants, physical input, normal
sandbox startup, Windows/macOS/ARM and the remaining full feature scope still
require work.
