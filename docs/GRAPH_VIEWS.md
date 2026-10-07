# Scientific graph displays

The desktop analysis view and independent native plot windows offer density,
scatter, histogram, CDF, contour, zebra, pseudocolor and [3D](THREE_DIMENSIONAL.md)
displays. Graph settings
control resolution (16–384 bins in saved definitions, selected presets in the
UI), automatic axis extent, density smoothing, probability spacing, palette,
outlier visibility and marker limits. Selecting **All finite events** includes
rare intensities that the default robust automatic axes can exclude. Zoom and
pan operate in the displayed, transformed coordinate system.

Native plot duplication and session recovery preserve graph settings,
including explicit fonts. **Graph settings → Plot fonts** controls axis labels,
axis numbers, gate names, statistics, legends and titles separately. Choose Sans,
Serif or Monospace, a physical point size (4–144 pt), normal/bold weight,
normal/italic style and text color. Reset a role or all fonts to inherit the
original display defaults. Layout Studio uses the same inspector. The legend
role controls report population names; statistics controls report population
counts and the desktop plot footer. Title controls the desktop population heading
and the report element's caption.

Explicit typefaces use bundled DejaVu fonts for native plots and publication
figures. Native point sizes follow 1 pt = 4/3 CSS pixels; report sizes remain
physical points. SVG/PDF graph text and explicitly styled graph captions use glyph
paths, preserving the faces without requiring the recipient to install fonts.
Font changes redraw the view without requesting new scientific data or replacing
3D event buffers. Styles persist with native plot sessions, layout definitions,
workspace archives and report templates. Report review hashes include them.
Axis margins expand for larger fonts; an unfittable figure requires a larger
panel or smaller fonts. Font settings do not alter event counts, finite masks,
coordinates, normalization, probability contours or raw acquisition files.
Styled captions reserve the measured glyph descent, including letters such as
“g” and “y”. If the panel limits the visible caption rows, the report records an
abbreviation warning.

Plot sessions also retain resolution and zoom. Histogram/CDF zoom has two limits; bivariate zoom has four;
3D axes have six and retain a separate camera pose.
The previously saved Y channel remains available when returning to a bivariate
display. Gate edits synchronize while each window keeps its own display settings.
The native IPC boundary validates every option and the allowed bounds shape.

[Independent coordinate definitions](AXIS_DEFINITIONS.md) select raw data,
sample/embedded/fixed compensation and ratios per axis or 3D scalar. These choices
persist in native windows, new gates and report definitions.

## Scientific definitions

All displays retain separate selected population, finite and viewport event
counts. Population gates always evaluate the complete event arrays with the
saved gate geometry and coordinate basis. Smoothing, resolution, palettes,
contour drawing and marker sampling never define a gate mask.

CDF values are the exact empirical frequency `100 × count(X ≤ edge) / finite N`
at every displayed bin edge. Repeated values at the edge are included. Events
below the view contribute to the curve; events above it remain in the
denominator. A zoomed curve need not start at 0% or end at 100%. The display
connects those edge values; it does not retain every individual empirical jump.
An empty finite population yields undefined values and an explicit reason.
Range gates still use the engine's existing lower-inclusive, upper-exclusive
gate boundaries. CDF overlays normalize each source to its own finite count.

Contours are highest-density regions of a full-event two-dimensional binned
estimate. Optional Gaussian smoothing uses a width in grid bins and preserves
the grid's total event mass. Default smoothing is enabled for contour, zebra and
pseudocolor, and disabled for legacy density. For each target probability, the
engine ranks grid cells by estimated density and chooses the threshold that
reaches the requested mass. Equal-density cells are included together, so
achieved coverage can exceed the target. **Probability coverage** exposes target,
estimated mass, empirical binned event fraction, ties and unavailable levels.

Linear spacing uses 2%, 5% or 10% targets below 100%; default contour spacing is
5%, and zebra spacing is 2%. Logarithmic spacing uses ten targets starting at
95%, halving each time. These are documented CytoForge numerical conventions;
they do not establish pixel or algorithm identity with FlowJo.

The density domain is the automatic sample axis domain, enlarged when explicit
view bounds extend it. Zooming inside that domain preserves the probability
estimate. The probability denominator remains all finite events in the selected
population. Events outside the density domain are reported rather than silently
renormalizing; an unattainable target has a reason and no threshold. Changing
resolution, extent, smoothing or the domain changes the estimate. Unsmoothed
contours trace the exact union of included bin cells, including separate corner
contacts and holes. Peak-density regions also use these cell boundaries: a
threshold equal to the peak otherwise collapses to a point under interpolation.
Other smoothed levels interpolate bin centers. The density threshold, included
ties, outlier classification and probability coverage do not change with this
geometry choice. The coverage panel and export provenance describe it; each
exported contour level records its geometry method. Paths are clipped to the
density domain. Geometry stops
at 150,000 vertices with an explicit drawing-limit flag; count and probability
calculations remain complete.

Exact cell boundaries use ContourPy's combined coordinate/offset output and
vector operations over batches of at most 4,096 complete rings. Ring starts,
closures, corner contacts and hole orientation remain independent. This avoids
repeating small NumPy operations for every disconnected region. The batch size
limits temporary vector buffers; a single large ring and ContourPy's original
coordinate buffer can still be large.

Zebra colors successive probability bands and draws the same contour paths.
Pseudocolor maps the log of the full-event density estimate continuously through
the selected palette. It is a binned density display. Outliers are events below
the outermost available probability threshold, including events outside the
density domain. Outlier total, viewport and displayed counts remain distinct.
When necessary, scatter and outlier markers use deterministic uniform sampling
at the selected limit, reported in the footer and export provenance. Increase
the marker limit to display more events; marker sampling does not preserve every
rare event. Backgate markers retain their separate 6,000-event cap.

## Gate appearance

**Graph settings → Gate appearance** controls border width, gate name visibility,
fill opacity and an optional fill colour. Fills apply to closed two-dimensional
gates. Open spider and curly separators retain their borders. The 3D viewer and
3D reports use border width and name visibility for boxes; axis labels have
their own controls. Independent native windows retain their own appearance.

Polygon fills subtract the union of excluded rings. Overlapping, nested and
partly external holes remain excluded, and self-intersecting outlines follow
the same even-odd rule as the scientific polygon mask. Publication SVGs keep
these regions as vector paths and separate exclusion clips. Fills do not alter
event identities, gate masks, population counts or density estimates.

Border widths use CSS pixels in the desktop view and 0.75 physical points per
CSS pixel in publication figures. Saved plot definitions, report templates and
project history retain the settings. Resetting appearance preserves fonts and
scientific settings. Plot fonts and gate styles also survive 3D window resizing.

## Reports and portable projects

**Add current plot** captures graph mode, settings, resolution and zoom in the
Layout Studio. The report inspector supports the same graph settings, CDF and
histogram modes for one parameter, and density, scatter, contour, zebra and
pseudocolor for two. CDF always uses cumulative frequency; histogram display
normalizations remain specific to histograms.

Figures are vector SVG, including probability paths, colored density bins and
markers. Native PDF and portable report exports include mode, resolved settings,
finite denominators, CDF counts/edges, probability coverage, density bounds,
sampling and geometry-limit provenance. Saved plot definitions travel with a
portable scientific project; native window geometry remains a local session
setting. Both outputs use the same scientific payload builder as exploration.
Legacy density reports retain their layer-color tint with the default ocean
setting; other palettes and pseudocolor/zebra use the selected color ramp.

Current plots also retain their selected backgate in report layers, templates and
portable projects. **Add backgate ancestry** creates successive parent views with
the final population highlighted. Counts, visible markers and sampling remain
distinct; see [Backgates and ancestry reports](BACKGATING.md).

## Verification and remaining scope

`tests/test_graph_views.py` checks literal tied CDF counts, finite denominators,
population masks, missing Y values in one-dimensional graphs, probability mass
and tied bins, missing density-domain mass, invariant estimates under zoom,
deterministic sampling, extreme finite values, empty/constant data, vector
figures, per-source CDF overlays and portable project/report round trips.
Exact bin boundaries are compared against literal oriented cell edges for every
3×3 occupancy pattern, including holes, corner contacts and domain boundaries,
and additional rectangular fields. Sparse 25-event regions are checked at
16/32/160/384 bins; a checkerboard drawing limit retains complete count and mass
audits. Large isolated and mixed fields cross multiple 4,096-ring batches and
are checked against complete literal cell edges and signed area. Smoothed peaks
retain positive bin area while other levels keep their
interpolated geometry.
`tools/desktop_plot_windows_smoke.mjs` checks actual independent native windows,
one/two-dimensional zoom, duplication, options and restart recovery.
`tools/desktop_graph_report_smoke.mjs` exports a genuine five-page native PDF
with all new graph types and checks its attached scientific manifest.
`tools/validate_graph_pdf.py` independently checks the actual PDF's vector paths,
page geometry, attachment and literal CDF source counts with PyMuPDF.
`tools/desktop_contour_boundary_smoke.mjs` verifies actual native contour pixels
in isolated sparse, hole, smoothed-peak and 32,768-event fragmented windows,
complete probability/count audits at the drawing limit, exact rare-event sidebar
counts and tooltips, unchanged scientific history, and a genuine four-page PDF
with geometry provenance.
`tools/validate_contour_pdf.py` independently inspects and rasterizes that PDF,
checking all 25 sparse boundaries and the enclosing region's hole.
`tools/benchmark_graphs.py` measures scientific payload calculation for a
million-event synthetic dataset at 160/384 bins; rendering and serialization
are outside that benchmark's timing.

`tools/benchmark_contour_performance.py` compares the preserved previous source
with the current implementation using five interleaved repetitions. All seven
scenarios produce identical complete JSON payload hashes, including path order
and the same prefix at the 150,000-vertex limit. On this Linux host, the
32,768-region, 256-bin checkerboard median falls from 2.3475s to 0.1572s; the
73,728-region, 384-bin case falls from 2.3639s to 0.1850s. Million-event bimodal
cases take 0.2280s at 160 bins and 0.3570s at 384 bins; the single smoothed peak
has no measured improvement. These are source calculation timings, excluding
loading, JSON serialization, IPC, native rendering and PDF. Separate 384-bin
processes report approximately 161.9/152.7 MiB peak RSS before/after using Linux
`VmHWM`; this accounting is approximate and does not establish a general memory
bound. Evidence: `artifacts/contour-performance-comparison.json`.

The authenticated plot endpoint encodes the shared JSON-compatible payload once
inside its worker. This avoids a second recursive conversion/copy over every
bin, path point and overlay. Scientific payload construction, strict nonfinite
JSON rejection, Unicode output, request validation, local access guards and
response security headers retain their previous behavior. No dependency was
added. `tests/test_plot_transport.py` verifies all eight graph modes with mixed
finite/nonfinite, empty, constant and extreme-value acquisitions; complete
response bytes match the previous encoding. Literal event counts, missing scalar
values, backgates, Unicode overlays, nulls and 3D tuple settings are checked.
Fragmented contour/zebra cases retain all 32,768 events, full grids and probability
audits alongside the same 150,000-vertex drawing limit; requests do not change
scientific snapshots or history.

`tools/benchmark_plot_transport.py` compares actual authenticated ASGI requests
using the preserved previous application, separate stores/caches and five
interleaved warmed repetitions. All 16 cases retain identical complete raw
response hashes. The 32,768-event fragment at 384 bins takes 1.4670s before and
0.5310s after; 100,000 scatter markers from 200,000 events take 0.5103s/0.1918s.
Million-event contour requests take 0.3767s at 160 bins and 0.5657s at 384 bins,
compared with 0.5393s/1.0127s before. These include source access, complete
scientific calculation, middleware, encoding and TestClient response consumption;
real sockets, native rendering and test-driver transfer are excluded. Small
requests show variable improvements and the small CDF case shows none. Evidence:
`artifacts/plot-transport-comparison.json`; the exact baseline application source
is retained in `artifacts/plot-transport-baseline-app.py`.

Primary implementation references:
[ContourPy combined line output](https://contourpy.readthedocs.io/en/v1.4.0/user_guide/calculate/line_type.html)
and [Linux process memory accounting](https://docs.kernel.org/filesystems/proc.html).
The plot transport follows
[FastAPI's direct response contract](https://fastapi.tiangolo.com/advanced/response-directly/).

The full FlowJo-alternative goal remains active. Magnetic/automatic-contour gate
construction, synchronized sample stepping, additional graph styling, hardware
GPU verification and expanded all-event planar rendering, very large-workspace
stress and Windows/macOS/ARM release verification remain in scope.

Workflow references: [FlowJo graph displays](https://flowjo.com/docs/flowjo10/graphs-and-gating/data-visualization-and-display),
[contours](https://docs.flowjo.com/flowjo/graphs-and-gating/data-visualization-and-display/gw-contours/),
[zebra](https://docs.flowjo.com/flowjo/graphs-and-gating/data-visualization-and-display/gw-zebra/),
and [CDF](https://docs.flowjo.com/flowjo/graphs-and-gating/data-visualization-and-display/gw-cdf/).
