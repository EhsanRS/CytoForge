# CytoForge

A desktop cytometry application built with Electron and a bundled, vectorized
Python scientific engine. Launching the executable opens its own application
window and starts its private analysis engine automatically. Everything in this
checkout—including Python, packages, browsers, caches, temporary files, datasets,
and installers—stays under this
directory. No cloud service is needed to analyze data.

**Status:** working 0.1 implementation; the full FlowJo alternative requested is
still being built. [The scope and validation ledger](docs/ROADMAP.md) records the
remaining work. Watson modeling, kinetics reference comparisons, biological validation and complete FlowJo workspace parity,
instrument-validated spectral workflows, and verified Windows/macOS
installers remain unfinished. WSP gate/saved-table import and GatingML exchange are implemented
with explicit compatibility reports and reference tests.

The desktop Samples panel now supports [reviewed sample concatenation](docs/CONCATENATION.md):
selected populations, explicit parameter matching, raw/compensated/scale values,
batch or keyword grouping, cancellation, and exact event origins. Merged samples
also open in independent native plot windows.

[Virtual group populations](docs/VIRTUAL_GROUPS.md) pool original samples in native
plot windows, with reviewed group gating, joint analysis, exact event export and
saved pooled reports. Each sample keeps its own events and correction.

[Panel harmonization](docs/CHANNEL_ALIASES.md) adds reviewed shared channel aliases
across detector panels. Original event columns and matrix bindings remain intact;
aliases work in native plot windows, gates, formulas, joint analyses and exports.

## Packaged desktop

The current Linux executable is
[CytoForge-0.1.0.AppImage](artifacts/installers/CytoForge-0.1.0.AppImage).
It bundles the interface, Electron and the scientific engine; an installed
Python, Node.js or running development server is unnecessary. To keep its
runtime data, profile, caches and temporary files in this checkout:

```bash
source tools/env.sh
CYTOFORGE_HOME="$PWD" APPIMAGE_EXTRACT_AND_RUN=1 ./artifacts/installers/CytoForge-0.1.0.AppImage
```

Windows installer/portable and macOS DMG/ZIP targets are configured but have
not yet been built and verified on those operating systems. The current Linux
build has been tested through its actual AppImage in headless desktop workflows,
with an explicit sandbox exception on this server. Normal startup on this host
currently fails because its privileged SUID sandbox helper is not configured;
interactive sandboxed launch remains unverified.

## Start from source

Prerequisites: Node.js ≥22.12 and `uv`. On Linux/macOS:

```bash
./start.sh
```

On **Windows 10/11 x64**, extract the Windows trial ZIP and double-click
**Start-CytoForge.cmd**. It downloads its tools inside the folder and opens the
desktop app; separately installed Python or Node.js is unnecessary. First launch
requires internet access. See [Windows trial and release instructions](docs/WINDOWS.md).
A native Windows build/run is still awaiting verification. The Linux AppImage
cannot run on Windows. `start.ps1` delegates to the same Windows launcher.

The launch scripts configure local
caches, install locked dependencies, build the interface if needed, and start
Electron. The desktop shell starts its private engine on an available loopback
port and stops it on exit. Installed builds include their own Python engine.
The selected desktop workspace is remembered across launches even when the
engine port changes; its identifier is stored under `.config` in the runtime
directory.

Double-click a sample or population to open its own native plot window, or use
**New plot window** to duplicate the current view. Each window has independent
axes and zoom; saved gates synchronize across windows. Open plots are restored
at the next launch. See [docs/PLOT_WINDOWS.md](docs/PLOT_WINDOWS.md).
Sample arrows retain the population and view while navigating a group;
Shift-click moves matching open plots together. Parent navigation, copied display
scales and group/search recovery are described in [docs/PLOT_NAVIGATION.md](docs/PLOT_NAVIGATION.md).
Population breadcrumbs restore each population's axes, zoom and 3D camera;
unfinished gate drawings are protected during navigation and native window closure.
Choose **3D** for independent XYZ clouds, camera controls, parameter color/size
and full-event volume gates. See [docs/THREE_DIMENSIONAL.md](docs/THREE_DIMENSIONAL.md)
for rendering, counts, reports and verification limits.

The default development command builds the interface and opens the desktop app:

```bash
source tools/env.sh
uv sync --frozen
npm ci
npm run dev
```

Create an experiment and import `.fcs`/`.csv` files, or open the reproducible
synthetic PBMC demo in its application window. The demo is clearly identified as
synthetic and never appears as a user acquisition. `npm start` opens an already
built desktop app. Electron manages its private engine and shuts it down on exit.

For remote progress checks, `npm run preview:desktop` launches the actual packaged
AppImage and streams its desktop window on **0.0.0.0:8001**. Open the session URL
written to `artifacts/desktop-preview-session.json`. Mouse and keyboard events go
to that desktop process; the viewer transfers imported files and completed
exports. The viewer also provides the Linux executable at `/app`. It does not
serve the application's React interface or proxy its scientific API. Its runtime
data stays under `.tmp/desktop-progress`.
Use the viewer's **Desktop window** selector to inspect and control a popup.
The session link is retained across viewer restarts so progress can continue at
the same address when a new desktop build is installed.

The remote viewer is headless and separate from normal desktop startup. This
server requires an explicit `CYTOFORGE_PREVIEW_NO_SANDBOX=1` override because its
sandbox helper and user namespaces are unavailable. The packaged launcher never
adds this override automatically. Set `CYTOFORGE_PREVIEW_PUBLIC_HOST` to the
server address when generating a remotely accessible session link.

The latest desktop candidate adds [captured populations](docs/POPULATION_SNAPSHOTS.md)
that preserve acquired-event membership after source gate edits or compensation
changes, including reuse of clustered populations as compensation/AF controls.
It includes [PhenoGraph clustering](docs/PHENOGRAPH.md) and raw acquisition analysis. Community gates retain fitted event identities;
saved raw parent populations keep their membership after compensation assignment.
It includes [multiple AF reference spectra](docs/AUTOSPILL.md)
for robust AutoSpill and median spectral calculations, with weighted review of
each AF direction against the span of all other sources. Saved median control
populations retain raw thresholds and reviewed QC flags for spreading reuse.
It includes acquired ratio and reviewed QC control populations for AutoSpill,
with captured event flags and saved populations that prefill spreading review.
It includes rectangular spectral AutoSpill with independent source names,
shared peak detectors, detector weights, background offsets and separate
source-convergence/detector-reconstruction review. It includes
[spillover spreading review](docs/AUTOSPREAD.md) for conventional and spectral
matrices, with saved controls, settings and CSV/JSON export.
It also includes [packed FCS integer imports](docs/IMPORTS.md) with mixed 1–64-bit
fields and [saved backgates and ancestry layouts](docs/BACKGATING.md),
gate appearance, shared plot fonts, custom plates, report templates, publication
controls and table cell formatting, at
`artifacts/candidates/population-snapshots/desktop/CytoForge-0.1.0.AppImage`.
Its complete engine, interface and desktop files match the checked sources.
All 1,561 Python tests and 28 compiled client checks pass; native interaction is
still awaiting validation. To preview that candidate in
its own workspace, stop your previous preview with Ctrl+C and run:

```bash
source tools/env.sh
CYTOFORGE_PREVIEW_NAME=population-snapshots \
CYTOFORGE_PREVIEW_BINARY=artifacts/candidates/population-snapshots/desktop/CytoForge-0.1.0.AppImage \
CYTOFORGE_PREVIEW_HOST=0.0.0.0 \
CYTOFORGE_PREVIEW_PORT=8001 \
CYTOFORGE_PREVIEW_PUBLIC_HOST=192.0.2.10 \
CYTOFORGE_PREVIEW_NO_SANDBOX=1 \
npm run preview:desktop
```

This retains the requested **0.0.0.0:8001** listener. The candidate's data and
caches stay under `.tmp/desktop-preview/population-snapshots`; its viewing link is
written to `artifacts/desktop-preview-population-snapshots-session.json`. Print that link
from a second terminal in this checkout:

```bash
node -p "require('./artifacts/desktop-preview-population-snapshots-session.json').url"
```

Click **New plot window** in the app to open an individual plot, then use the
viewer's **Desktop window** selector to inspect it. Other
candidate previews can use a different lowercase `CYTOFORGE_PREVIEW_NAME` and
port. Selecting a custom AppImage requires a named preview and a binary inside
this checkout. The original progress profile and promoted installer are retained.

`npm run dev:browser` remains available for isolated interface development at
**http://127.0.0.1:5173**; it is not the desktop launch command.

## Working workflows

- Bounded FCS/CSV import with gain/log/time preprocessing, chained datasets,
  acquisition spillover, dataset duplicate detection, mixed-batch reports, native
  progress/cancellation and crash recovery. Float/double, byte-aligned integer
  and little-endian packed integers, plus legacy ASCII FCS, are decoded into
  immutable memory-mapped event arrays.
  Reference acquisitions match FlowIO exactly. See [docs/IMPORTS.md](docs/IMPORTS.md)
  for numeric interpretation, limits and validation.
- Persistent workspaces and groups, sample annotations, atomic SQLite snapshots,
  revision conflicts, undo/redo after restart, and portable `.cytoforge` archives
  with SHA-256 integrity checks.
- FlowJo WSP gate/saved-table import and schema-validated GatingML 2.0 import/export, with
  sample mapping, retained source XML, diagnostic/count reports, open and
  multidimensional rectangles, ellipsoids, ratios, bounded scale transformations,
  fixed per-dimension compensation, and Boolean operand complements. Gate
  coordinates can be plotted, edited and used when drawing child populations;
  source/report records survive project export and undo/redo.
  Repeated measured parameters retain independent axis definitions in plots,
  native volume gates, reports and sample navigation. See
  [docs/PLOT_COORDINATES.md](docs/PLOT_COORDINATES.md).
  Native plots also expose independent raw/fixed-matrix/ratio definitions for
  each axis and 3D color/size, captured by new gates and layout plots; see
  [docs/AXIS_DEFINITIONS.md](docs/AXIS_DEFINITIONS.md).
  Saved sample-iterated tables retain source populations, raw/fixed compensation,
  keywords, controls, hidden inputs and restricted column/row formulas. Calculation
  differences and unconverted features are reported; see
  [docs/WSP_TABLES.md](docs/WSP_TABLES.md).
- Linear, log10, logicle, hyperlog, and cofactor arcsinh transforms. Logicle and
  hyperlog use the FlowUtils GatingML implementations. Gates retain the transform
  with which they were drawn when the display scale changes.
- Hierarchical rectangle, polygon, ellipse, range, quadrant, and boolean gates;
  coordinate editing, dependency-safe deletion, group tree propagation, and
  backgating. Membership and statistics use all events.
- Visual shape editing in the main desktop window and individual plot windows:
  double-click a visible gate or choose **Edit gate on plot** to drag, resize,
  rotate, edit polygon vertices, nudge with the keyboard, and review
  full-event draft counts before saving. Imported covariance, unbounded limits
  and native coordinate definitions are preserved. See
  [docs/GATE_EDITING.md](docs/GATE_EDITING.md) for supported shapes and verification.
- Freehand drawing in native main and popup plots: trace or drag an outline,
  review its full-event count and save the recorded polygon. Coordinate definitions
  and self-crossing lobes are preserved; stale, resized and oversized traces stay
  protected. Dense scientific masks index relevant edge intervals. See
  [docs/FREEHAND.md](docs/FREEHAND.md) for controls and verification.
- Linked bisectors in native histogram/CDF windows and shared quadrant thresholds:
  full-parent counts, atomic family edits, independent popup views, undo/redo,
  propagation, family deletion and GatingML relationship round trips. See
  [docs/PARTITIONS.md](docs/PARTITIONS.md).
- Linked spider populations in native plots: move a shared center and rotate four
  arms, review every member's full-parent count, and apply the family in one
  undoable operation. The scientific sectors extend beyond the viewport. See
  [docs/SPIDER_GATES.md](docs/SPIDER_GATES.md) for controls and interchange limits.
- Linked curly quadrants in native main and popup plots: move the shared center,
  review raw-unit noise coefficients and full-parent counts, and update all four
  populations atomically. Scientific membership uses unbounded square-root limits.
  See [docs/CURLY_GATES.md](docs/CURLY_GATES.md) for the explicit convention and
  remaining vendor/interchange validation.
- Automatic density gates in native main and popup plots: click a region, adjust
  density coverage and smoothing, review its full-event count, and edit/save an
  exact polygon with excluded rings. See [docs/AUTOGATING.md](docs/AUTOGATING.md)
  for the numerical convention and interchange behavior.
- Magnetic gates with nearby population following, previewed displacement and
  full-event counts, independent copied-sample positions, parent recalculation,
  native popup synchronization and reviewed freezing. GatingML exports resolved
  snapshots; portable projects preserve tracking. See [docs/MAGNETIC_GATES.md](docs/MAGNETIC_GATES.md)
  for the numerical convention and compatibility limits.
- Canvas density/scatter/histogram/CDF/contour/zebra/pseudocolor plots, graph settings, zoom/pan, channel selection, gate
  overlays, and PNG downloads. Scatter display is reproducibly limited to 12,000
  events; density uses full-data histogram bins. Plot bounds omit extreme tails
  by default and explicitly report events outside the view.
- Interactive XYZ clouds with independent native cameras, parameter color/size,
  full-event drawing, volume gates and vector publication reports. See the
  [3D verification scope](docs/THREE_DIMENSIONAL.md).
- Manual/acquisition compensation matrices with row-source orientation,
  condition/rank validation, sample assignment, and automatic recalculation.
- A single-stain control wizard with acquired-data cleanup gates or explicit
  raw thresholds, matched positive/negative references, full-population medians,
  finite event counts, diagnostic plots, condition/rank checks and provenance.
  Spectral outputs, optional autofluorescence extraction, electronic background
  and detector-weighted least squares work with gates, formulas and exports.
- AutoSpill adds Huber regression, automatic scatter cleanup, iterative linear/
  native-biex refinement and an optional unstained/empty-detector AF control.
  Review convergence, residuals for every detector pair, acquired/compensated
  previews, cleanup populations and portable reports before applying a matrix.
  The pinned author R comparison and physical-truth limitations are documented in
  [docs/AUTOSPILL.md](docs/AUTOSPILL.md).
- Derived parameters from a restricted, vectorized expression language:
  `ch("PE-A") / max(abs(ch("APC-A")), 1)`. No Python evaluation or arbitrary code
  is allowed. Missing/undefined values stay missing. Formulas, gates, statistics,
  FCS/CSV export, and portable archive restore work together.
- Acquisition quality diagnostics with time/rate and full-data marker quantile
  traces, interval review, optional raw saturation and pulse-ratio checks, exact
  retained/rejected populations, draft report/event CSV exports, cancellation,
  scientific fingerprints, portable archives and undo/redo. The robust-bin
  method and its scientific limits are documented in [docs/QC.md](docs/QC.md).
- Dean–Jett–Fox cell-cycle modeling with optional synchronous S phase, independent
  batch fits, peak/CV/ratio constraints, full-event DNA histograms, residuals and
  review warnings. Saved phase probabilities and assigned populations work in
  plots, formulas, statistics, FCS exports, portable projects and undo/redo.
  JSON, batch statistics/event CSV and SVG figures are available before saving.
  See [docs/CELL_CYCLE.md](docs/CELL_CYCLE.md) for assumptions and validation limits.
- Proliferation with an undivided reference, optional unstained control, Gaussian
  or lognormal dye/background models, constrained peak ratio and CV, generation
  probabilities and precursor frequency/division/proliferation/expansion/
  replication indices. Review full-event histograms, residuals and overlap before
  saving generation populations. Batch statistics, event CSV, JSON and standalone
  SVG exports are available. See [docs/PROLIFERATION.md](docs/PROLIFERATION.md).
- Desktop kinetics with exact time-bin statistics, clock calibration/alignment,
  explicit reset policies, baseline percentile thresholds, responder percentages,
  gap-preserving smoothing, editable/suggested intervals, batch overlays and
  peak/slope/area summaries. Frozen event-aligned outputs, ordinary range/responder
  gates, live table/plate metrics, replacement/history and portable reports work
  together. See [docs/KINETICS.md](docs/KINETICS.md) for exact definitions and limits.
- Saved biological models support rename, refit and replacement with stable
  output names and population IDs. Removal previews dependent formulas,
  populations and report selections. Previous fits remain available after
  replacement; removal and replacement participate in undo/redo and archives.
- Live experiment tables with counts, parent/total frequencies, arithmetic mean,
  median, sample SD, CV, MAD-based robust CV, geometric mean when defined, and
  percentiles. Metadata keys are namespaced in CSV exports to preserve statistics.
- Custom tables add per-column population mappings, control values, keywords,
  formulas with stable references, saved biological model statistics, hidden
  helpers, formatting and heatmaps. Pivots and four sample comparison tests
  include paired designs and multiplicity adjustment. CSV/XLSX/JSON exports
  retain every selected row; XLSX includes cell status and provenance. See
  [docs/TABLES.md](docs/TABLES.md) for exact definitions and bounds.
- Desktop plate analysis with 6/12/24/48/96/384/1536-well maps and custom rectangular
  dimensions, reviewed resizing, separate native acquisition windows, explicit replicate matching,
  CSV annotation plans, decimal dilution series, reviewed keyword application,
  full-event population measurements, formulas/biological metrics, heatmap/split/
  category/face views, selected-well groups, templates, SVG/CSV/JSON exports and
  native draft recovery across engine restarts. See [docs/PLATES.md](docs/PLATES.md)
  for aggregation semantics, import limits and remaining compatibility work.
- Native Layout Studio with positioned pages, plots/overlays, saved tables,
  biological/plate figures, live statistics and annotations, grouping and local
  undo/redo. Live table rows, pivots and corrected comparisons continue across
  row/column pages with their full-cohort provenance. Reviewed
  acquisition/panel/keyword batches export vector SVG/PDF
  and PNG with resolution metadata. PDFs attach verified source manifests;
  desktop drafts survive engine restarts. See [docs/REPORTS.md](docs/REPORTS.md)
  for normalization, export policies, limits and remaining publication work.
  Current source also supports [portable report compositions](docs/REPORT_TEMPLATES.md)
  with destination binding review, prototype previews, undoable import and saved
  template provenance. Native interaction and installer promotion are pending.
- Exact gated FCS/CSV exports and portable project import/export.
- Discovery analyses: PCA, UMAP, t-SNE, FlowSOM and PhenoGraph, shared models across selected
  sample populations, balanced/proportional seeded sampling, feature transforms
  and optional standardization. Separate worker processes keep the interface
  responsive and support cancellation. Results become event-aligned parameters
  for gating, statistics, report plots and FCS/CSV export; FlowSOM can create exact
  metacluster populations, and PhenoGraph can create fitted community populations.
  Raw analyses save acquired parent populations. Provenance includes scientific input definitions,
  settings, fitted event IDs, software versions and checksummed outputs.

## Compensation and spectral controls

Open **Compensation → Control wizard** and select an estimation method. The
median-difference method supports conventional spillover or
spectral unmixing. Select acquired detectors, then a positive and negative sample
and cleanup population for each stain. **Reuse a negative reference** fills all
control rows with the same selected reference. A mixed single-stain sample can
use complementary **At or above threshold** and **Below threshold** selections.
Thresholds are in acquired intensity units. Cleanup gates use their saved
transforms but are evaluated without compensation; gates on formulas, embeddings
or spectral outputs are rejected for control calculations.

Conventional compensation requires one control per detector. Each spillover row
is the positive-minus-negative median response divided by that difference in the
primary detector. Spectral references instead have unit peak detector response;
outputs therefore express peak-detector equivalent intensity. Optional detector
weights are positive inverse variances. Electronic background is a detector
vector subtracted from measured events before the least-squares solve.

For autofluorescence extraction, select representative unstained cells. Their
median detector spectrum minus electronic background becomes an additional
reference and output parameter. Use matched material and autofluorescence for
positive/negative controls. This median estimator follows the conventional
[positive/negative control approach](https://www.flowjo.com/docs/flowjo10/experiment-based-platforms/plat-comp-overview/plat-comp-faq).

For iterative AutoSpill, assign the whole cleaned single-stain population to each
detector. Optional autofluorescence uses representative unstained cells in an
otherwise empty measured detector. Parent gates use acquired values with saved
transforms; automatic scatter cleanup can be inspected and saved as raw
populations. Run, review convergence and every detector pair, download JSON/CSV,
then choose targets and apply. Trimming and correlated autofluorescence can bias
coefficients even with small residuals; compare control distributions and settings.
See [AUTOSPILL.md](docs/AUTOSPILL.md) for the exact algorithm, native R comparison,
strict unmet-tolerance acknowledgement and bounds. Rectangular spectral
AutoSpill, spreading diagnostics and real instrument/FlowJo validation remain pending.

**Calculate & review** displays the matrix, finite counts, robust separation,
reference spectra or raw scatter previews, and numerical conditioning. Medians
use all selected finite events; only scatter previews are capped at 200 events
per population. Overlapping, undersized, unseparated and rank-deficient controls
are rejected. Negative coefficients remain negative with a diagnostic message.
Then choose target samples and save the reviewed matrix. A workspace change
invalidates a new preview until it is recalculated.

Spectral assignment retains acquired detectors and appends distinctly named
output parameters. The outputs can be plotted, gated, used in formulas, and
exported/restored in FCS/CSV and projects. Unassigning or replacing the matrix
retains output definitions used by gates; inactive outputs become NaN. An
uncompensated population export contains acquired detector values and undefined
spectral outputs. Project archives preserve control populations, raw event hashes,
original coefficients and diagnostics; manual matrix edits retain that lineage.

## High-dimensional analyses

Open **Discovery**, choose samples/populations and shared markers, then run an
analysis. Up to two processes run concurrently; additional jobs queue. Add a
successful result to the workspace, then choose **Explore result**. Adding results
is atomic and undoable. Failed/cancelled jobs leave the experiment unchanged;
engine shutdown records unfinished jobs as interrupted for explicit restart.

- **PCA** uses full SVD on the fitted set and projects every eligible event.
  Explained variance and component weights are shown in provenance.
- **UMAP** fits a seeded two-dimensional embedding and transforms remaining
  eligible events using that model. Fitted and projected events share parameters.
- **t-SNE** embeds only fitted event IDs. It has no out-of-sample projection here;
  remaining event coordinates are NaN. Embedding gates describe the fitted subset,
  while parent frequencies retain the full population denominator.
- **FlowSOM** trains the published Python SOM implementation, then computes
  consensus metaclusters from 100 seeded 90% node resamples and average-linkage
  clustering of co-assignment distances. Every eligible event receives its nearest
  SOM node and metacluster. Node/cluster IDs are one-based. This implementation
  does not imply numerical equivalence to a FlowJo plugin.
- **PhenoGraph** builds a nearest-neighbour/Jaccard graph and selects the best
  modularity from seeded Louvain restarts. Only fitted event IDs receive community
  labels. Label 0 represents discarded small communities; other eligible events
  stay undefined. Results include a linear community parameter and optional
  community gates. See [PHENOGRAPH.md](docs/PHENOGRAPH.md) for parameters and limits.

Uncheck **Use assigned compensation or unmixing** to analyze acquired values. Applied raw
analyses retain acquired copies of their input parent populations, so later matrix
assignment preserves their saved membership.

Sampling affects the fitted model; event counts remain exact. Constant features
and nonfinite rows are explicitly reported. Scientific input changes mark saved
results as stale snapshots; rerun to refresh them. Applying a result from an older
workspace revision is rejected. Seeds reproduce tested local runs with locked
versions; exact numerical equivalence across OS/hardware remains unverified.
Archives retain result arrays, fitted event identities and input provenance.
The first UMAP/SOM job can spend time compiling Numba kernels; subsequent
jobs reuse the cache in the configured runtime directory.

## Linux server setup

This server lacks normal desktop libraries and blocks unprivileged user
namespaces. Private Ubuntu GUI libraries were downloaded and extracted to
`.cache/sysroot`; `tools/env.sh` uses them automatically on this host. System
packages were not installed. The one-time root ownership/mode commands required
for the Electron sandbox helper were sent to the authorized Slack webhook.

Headless desktop validation can run with an explicit, isolated test exception:

```bash
source tools/env.sh
CYTOFORGE_TEST_NO_SANDBOX=1 node tools/desktop_smoke.mjs
```

The smoke test checks Electron's effective command-line flags. Playwright's
default and the packager's upstream AppRun could both add `--no-sandbox`
automatically; the test settings and packaged launcher now prevent an implicit
override. This exception is only in the explicit test invocation. Normal startup
keeps renderer isolation and sandbox settings enabled. The actual AppImage's
normal launch on this server still fails because its sandbox helper lacks the
required root ownership/mode; see `artifacts/native-sandbox-probe.json`.
Interactive Linux launch and native releases on the other requested platforms
remain open release gates.

## Build installers

Build separately on each target OS/architecture; the Python sidecar cannot be
cross-compiled with PyInstaller.

```bash
source tools/env.sh
uv sync --frozen
npm ci
npm run setup:desktop
uv run --frozen python tools/package_engine.py
npm run package:desktop
```

Outputs go to `artifacts/engine/` and `artifacts/installers/`. The current Linux
build includes `artifacts/installers/CytoForge-0.1.0.AppImage` and
`artifacts/installers/linux-unpacked/`. Installed builds store data
under `~/CytoForge`; set `CYTOFORGE_HOME` to choose a portable directory. Development
defaults to this checkout. Runtime caches and logs follow the chosen directory.

## Verify

```bash
source tools/env.sh
uv run --frozen pytest -q
uv run --frozen ruff check backend tools tests
npm run build
npx playwright install chromium
npm run test:e2e
uv run --frozen python tools/benchmark.py
uv run --frozen python tools/benchmark_cellcycle.py
uv run --frozen python tools/benchmark_proliferation.py
uv run --frozen python tools/benchmark_autospill.py
uv run --frozen python tools/engine_smoke.py
```

In managed environments where cross-thread event-loop wakeups are restricted, the existing
Python HTTP workflows can use the application's ASGI interface on the caller's event loop:

```bash
source tools/env.sh
uv run --offline pytest -q --in-process-asgi
uv run --offline python tools/benchmark_population_comparison.py
```

The optional test transport runs application middleware, startup/shutdown and background
workers. Listening-server, private-engine startup and native desktop checks remain separate.
The comparison benchmark uses owned synthetic acquisitions, keeps temporary data in `.tmp`,
removes it after verification and writes timing, memory and exact-result evidence to
`artifacts/benchmark-population-comparison.json`.

Pytest uses `.tmp/pytest`. Browser tests use `.tmp/e2e-data`; screenshots, PDF
examples, traces, and test reports go to `artifacts/`. Scientific tests exercise
transform references/inverses, compensation orientation, gate boundaries,
hierarchies/boolean/quadrant logic, empty/nonfinite populations, FCS round trips,
formulas, concurrent revisions, history branching, malformed imports, and archive
integrity, independent PCA/SVD results, UMAP seed repeats, t-SNE subset identity,
FlowSOM cluster partitions, independent PhenoGraph graph weights and seeded
communities, raw parent preservation, analysis cancellation and stale-result rejection.
Compensation fixtures cover raw control masks, positive/negative separation,
weighted spectral equations, autofluorescence, virtual output formulas/exports,
stale previews and inactive outputs. The frozen engine smoke also calculates a
known spectral mixture and verifies its restored outputs. Packaged desktop smoke
checks selected-workspace persistence across a changed engine port. Interchange
checks include ISAC event-membership truth, XML schema/security failures,
import/export membership round trips, a 200,000-event diamond, and a
283,969-event eight-color panel compared with FlowKit, plus browser review and
drawing workflows.
Tests establish the covered behaviors, not complete scientific parity.
The current Python suite passes 1,086 scientific/API cases with the optional ASGI test
transport. Interface workflows and native installers have separate release evidence.
AutoSpill checks four independently generated author-R matrices and exact cleanup
membership, native lookup/R natural spline evaluations, known-matrix recovery,
raw parent geometry/Boolean copies, scale/trim limits, cancellation, stale inputs,
portable reports and explicit review of unmet tolerance. Default trimming biases
an independent AF fixture; disabling trimming recovers its known matrix. That
finding is preserved, rather than treating convergence as physical accuracy.
DNA fixtures use independently labeled asynchronous/synchronized populations and
adaptive integration, including a quadratic with zero S-phase endpoints.
Saved probabilities, range/source exclusions, large finite intensities, batch
sample deletion, corrupted archives and discrete phase counts are checked.
Watson refinement remains unavailable after an independent recovery failure;
see [CELL_CYCLE.md](docs/CELL_CYCLE.md).
Proliferation fixtures independently sample latent generation labels and compare
background convolution with adaptive quadrature. They cover calibration, absent
generations, undefined responding-precursor statistics, conditional range
exclusions, event identities and rehashed but semantically invalid archives.
Shared model management checks stable replacement, downstream staleness,
transitive removal previews, rename, concurrent dependency changes and undo.
The standalone engine smoke runs all four algorithms with development import
paths removed, adds their parameters and restores a complete analysis archive.

`artifacts/benchmark.json` records a repeatable synthetic million-event,
16-channel benchmark. On this host, cached counts for ten gates took ~1.7 ms,
full-data density ~185 ms, and population statistics ~60 ms. These are engine
timings without HTTP/rendering and are not measurements on real instrument data.

`artifacts/benchmark-autospill.json` records 400,000 full-population events across
eight synthetic controls/detectors in ~1.62 seconds on this host. It includes
hashing, regression, refinement and diagnostics; excludes automatic scatter
cleanup, input generation/writes, HTTP and rendering.

## Current data limits

Imports support up to 1,024 chained FCS datasets with strict offsets, at most
512 acquired channels and 150 million numeric values across one file, 1 GiB per
file and 128 files/4 GiB per selection. Event decoding is streamed in bounded
chunks. Bit-packed/histogram formats and additional instrument compatibility
remain unfinished. Archives are limited to 4 GiB compressed / 8 GiB expanded.
Analysis reads memory-mapped arrays and bounds the calculation cache to 256 MiB.
Analysis jobs currently accept up to 40 million input values,
64 selected features, 100,000 fitted events (30,000 for t-SNE), and 128 input
samples; model input preparation still uses whole-array paths. PhenoGraph also
limits the configured fitted-event count times neighbour count to five million.
Population FCS/CSV
export writes bounded float64 chunks and streams native saves; FCS preserves
correction and exact merged history. See [EVENT_EXPORTS.md](docs/EVENT_EXPORTS.md).
No clinical
validation or full instrument compatibility is claimed.

QC accepts up to 32 stability markers and 1,024 acquired-event intervals per run.
AutoSpill accepts 2–64 acquired detectors with matching controls and at most
64 million regression input values. All finite cleanup events are used unless
an explicit per-control limit is selected; no automatic hidden sampling occurs.
Projects containing QC, cell-cycle or proliferation fits use version-two archives
with separate hashed reports; version-one projects remain readable. Limits are
1,000 saved results per platform per workspace and 32 MiB per report.
Biological fits support 128 sources per batch; output arrays are currently held
in worker memory before writing. See [docs/QC.md](docs/QC.md) for clock
resolution, source population, baseline and pulse-shape limitations.

## Exchange an existing gate strategy

Import the FCS samples, then choose **Import gates** in the workspace toolbar.
Upload a FlowJo 10/11 WSP or GatingML 2.0 XML file and select the target samples.
Matching uses names and channel compatibility; ambiguous names require selection.
The preview leaves the workspace unchanged. Review unsupported definitions before
applying the supported gates. Source paths are never opened by the XML importer.
Original XML and JSON reports remain available from the same dialog and travel
with the portable project. **Export GatingML** exchanges the selected sample's
complete gating strategy after schema validation.

Unsupported FlowJo tables, layouts, plugin/biological analyses, dynamic group rules, unsupported
transforms and gate types are reported and retained in the original XML rather
than converted. GatingML exports reject transforms or matrix options that the
standard cannot represent exactly, including FlowJo lookup-table biexponential
scales and detector-weighted/background-corrected unmixing. Use the portable
project or original source for those definitions. The eight-color reference
matches FlowKit event membership, while some stored FlowJo counts differ; the
report records those discrepancies. Full FlowJo/instrument parity is unfinished.

Graph probability conventions, finite denominators, sampling and report support are
documented in [Graph views](docs/GRAPH_VIEWS.md).
Unsmoothed contours retain the exact boundary of included bins, including holes
and diagonal contacts. Tied peak bins retain visible area in native plot windows
and vector PDFs; smoothing never changes the stored population mask.
