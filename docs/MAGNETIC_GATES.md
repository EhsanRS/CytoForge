# Magnetic gates

Enable **Magnetic gate** in **Edit selected gate**, set the maximum movement,
and choose **Preview magnetic position**. The preview reports the events inside
the original and resolved shapes, the full parent count, and the displacement.
Saving enables automatic population following for that sample. A magnet marks
the population in the desktop tree. **Fit magnetic positions** in the plot
footer brings the original position, moved shape and displacement arrow into
view without changing membership. Each native plot window keeps its own view.

Copying a strategy retains its original gate coordinates and tracking settings.
Each target acquisition calculates its own position. Editing a parent,
compensation matrix, gate coordinate transform or tracking setting recalculates
the position. Subsets, Boolean operands, statistics and event exports use the
resolved gate. Undo/redo, desktop restart and portable project archives retain
the original geometry and tracking settings.

**Freeze reviewed position** copies the previewed geometry into the draft as a
static gate. Save to commit that position. Clearing **Magnetic gate** returns to
the stored original coordinates. A changed workspace invalidates the preview;
the editor retains the draft and requires review before another preview or save.
Previewing never changes workspace revision, history or event data.

FlowJo describes magnetic gates as nearby population following on existing
univariate and bivariate gates, with per-sample recalculation and an arrow from
the original position. Its public documentation does not specify the numerical
optimizer. CytoForge uses the following documented convention; equivalence to
FlowJo's numerical algorithm has not been established. See
[FlowJo's magnetic gate documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/advanced-gates1/gw-gatemagnetic).

## Scientific convention

- `local-window-count-v1` is saved with the tracking settings. The stored gate is
  the anchor; resolving a position never rewrites those coordinates.
- Supported shapes are ranges, rectangles, polygons, rotated ellipses, bounded
  one/two-dimensional hyperrectangles and symmetric positive definite
  two-dimensional ellipsoids. Shape dimensions, orientation, vertices and
  covariance are preserved. Explicit dimensions retain compensation references,
  coordinate transforms and ratios.
- The maximum movement is a positive radius through four gate widths, default
  two. Distance is Euclidean after dividing each translation coordinate by that
  axis's full gate bounding-box width. It is independent of display zoom.
- Every finite parent event in the gate's coordinate basis contributes to the
  calculation. Events outside the bounded possible search footprint cannot
  influence the ordinary count surface. Plot sampling, resolution, smoothing,
  contour geometry limits and current visible bounds do not enter the search.
- A grid with 32 cells per gate width represents full-event counts. Correlation
  with the complete shape's footprint gives a gate-window count surface. A
  Gaussian filter with sigma one cell selects the nearest positive local maximum
  using a five-cell neighborhood. Ties have deterministic distance, score and
  coordinate ordering.
- Exact shape membership refines that proposal over neighboring grid positions
  within the radius. Polygons with more than 128 vertices compare the grid
  proposal and anchor, retaining every vertex. Movements smaller than one grid
  cell compare bounded direction candidates with exact event counts. These are
  discrete numerical search conventions, rather than a continuous optimizer.
- The final counts always cover the entire finite parent, including polygon
  boundary tolerance. A proposal containing fewer actual events than the anchor
  is discarded. Tied exact counts prefer the smallest movement. Empty,
  nonfinite-only and distant populations retain the anchor. A movement near the
  search boundary is flagged for review. A coarse search can retain an anchor
  for a shape whose interior is poorly represented by the grid.
- Complement gates optimize the positive shape first, then complement within
  the full parent. Preview **events inside shape** refer to the positive shape;
  population membership/counts reflect the requested complement. Existing
  boundary conventions remain unchanged: rectangular upper bounds are excluded,
  ellipse boundaries are included, and polygons use the established even-odd
  membership rule with boundary tolerance.
- Unsupported unbounded, Boolean, container, QC and higher-dimensional magnetic
  shapes are rejected explicitly. Non-magnetic gates keep their existing
  behavior. Inactive tracking fields are omitted from legacy gate serialization.

The scientific array cache and a separate 256-entry resolution cache are scoped
to workspace, revision, acquisition, compensation mode and gate fingerprint.
Returned resolution records are independent copies. No event arrays or complete
gate models are stored in the resolution cache.

## Exchange and verification

CSV and FCS exports contain the resolved population. GatingML exports the current
resolved geometry as a **static snapshot**, with the original gate and
resolution retained in custom provenance. Import reports that snapshot behavior.
Portable projects preserve automatic following. Recognized active magnetic
flags in FlowJo WSP imports produce an explicit conversion issue, since the
source optimizer and anchor semantics require further compatibility work; the
original XML is retained.

Scientific checks in `tests/test_magnetic.py` cover independently labelled
populations, the nearest population against a larger distant population, exact
full-event masks, parent/history changes, compensation, transformed ratios,
copied acquisitions, complements/subsets/statistics, 2,000-vertex polygons,
tiny movement bounds, deterministic ties, all supported shape exports, portable
restoration, stale/cyclic/missing-channel previews and local authentication.
`tools/benchmark_magnetic.py` measures complete authenticated million-event
preview requests with explicit cold/warm application-cache scope.
`tools/desktop_magnetic_smoke.mjs` exercises the native editor, popup
synchronization, review of concurrent changes, parent undo, independent copied
positions, freezing and desktop restart. Package and platform verification are
recorded separately in release evidence; full FlowJo parity remains active.
