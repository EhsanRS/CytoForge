# Architecture and invariants

`virtual_groups.py` resolves live cohorts and exact population paths over original
samples. Selected coordinates and masks use revision/cohort-specific cache keys;
no merged acquisition file is created. `virtual_group_gates.py` stages reviewed
per-sample gate transactions with one undo, and `virtual_group_exports.py` writes
chunked float64 pooled files with qualified member/event identities. Native view
history includes the cohort context, and report validation walks every pooled
source closure. See [VIRTUAL_GROUPS.md](VIRTUAL_GROUPS.md).

`concatenation.py` stages float64 sample matrices and SHA-256 bound uint64 event
origins under a bounded two-thread coordinator. Full saved population masks feed
16,384-event write slices, with independent correction/formula evaluation. A
review hash binds the source request, output sample definitions and cloned
matrices; publication verifies bytes and commits the complete batch at one
expected workspace revision. `Sample.concatenation` retains immutable scientific
source snapshots; its absent field is omitted for legacy sample serialization.
Project archives include `origins/<sample-id>.npy`. Their hashes, dtype, event
alignment, source ranges and ascending source IDs are validated before restore.
The existing `.npy` event files and new `.origins.npy` files survive history moves.
The desktop flow and current interoperability limits are in
[CONCATENATION.md](CONCATENATION.md).

Biological fits live in `Workspace.cell_cycle_results` and
`Workspace.proliferation_results`. Cell cycle has four float32 outputs;
proliferation has one probability per generation plus its assigned generation.
They use event-aligned analysis storage and computed-parameter bindings.
Discovery and both biological platforms share one result-ID namespace.
Probability rows are entirely finite and normalized with a consistent assignment,
or entirely NaN. SHA-256, event and expected/assigned counts are checked before
applying or restoring results. Inputs are scientific snapshots; names and display
scales do not determine physical DNA/dye fits. Batch sources fit independently.
Proliferation controls are also part of the scientific fingerprint and are
calibrated once per request.

Portable version-two archives store hashed reports at `<platform>/<id>.json`
and probabilities at `<platform>/<id>/<sample-id>.npy`, with `cell-cycle` or
`proliferation` as platform. Existing archive readers retain version-one and QC
version-two compatibility. Scientific details and limits are in
[CELL_CYCLE.md](CELL_CYCLE.md) and [PROLIFERATION.md](PROLIFERATION.md).

`biology.py` shares report drawing, proliferation-array validation and saved-model
dependency management. Replacing a model retains its immutable previous report
and arrays, rebinds active output names, and preserves population IDs. Eligibility
requires identical sample/gate scope, output count and active bindings; inputs
and controls cannot depend on the replaced outputs. Renaming preserves scientific
fingerprints and parameter identifiers. Removing active outputs closes formula,
gate descendant and Boolean dependencies, removes affected layout plots by their
layout/plot pair, and clears affected table selections. Immutable downstream
analyses remain available with input staleness. A preview hash and revision check
protect the reviewed removal closure. Assets stay available to undo snapshots.

## Execution

`Workspace.plates` stores normalized well maps, staged annotation dictionaries,
up to ten shared `TableColumn` measurements and visualization settings. A well
can contain several acquisitions, with each acquisition assigned to one well
per plate. `plates.py` reuses the full-event table evaluator, then aggregates
acquisition statistics with equal weights. Missing/partial replicate states,
metadata source, display clipping and measurement provenance remain explicit.
The same numeric-only face helper draws the interface and standalone SVG.

CSV/dilution/resize/template/mapping endpoints return draft previews. Applying
annotations checks a digest of the exact review and its revision, saves the
plate and writes only reviewed sample keywords in one SQLite transaction.
Saved plate and batch mutations check a captured base revision; foreign
acquisition references and duplicate normalized wells fail validation. Sample
removal clears assignments while retaining empty-well plans. Plate definitions
are in project manifests and undo snapshots, with old archives defaulting to an
empty plate list. Statistical semantics and bounds are in [PLATES.md](PLATES.md).

Unsaved desktop plate sessions are separate from scientific workspace data and
use atomic, flushed files under the runtime directory's `.config/plate-drafts`.
The context-isolated preload exposes only validated workspace IDs and bounded
draft strings; the main process checks the sender frame/origin. Close requests
use a private nonce and await the renderer's latest draft write before destroying
the window. Engine shutdown occurs at `will-quit`, after that handshake. Saved
plates remove their recovery cache. Browser progress previews use session
storage and do not expose native filesystem capabilities.

React 19 / TypeScript / Vite renders local assets. Electron starts a standalone
FastAPI/NumPy/SciPy engine on `127.0.0.1` with an ephemeral port. There are no remote
scripts, fonts, telemetry, licensing requests, or scientific cloud calls.
Development can use Vite's same-origin proxy or the engine's built UI directly.

`npm start` and `npm run dev` open the Electron desktop application. The separate
`dev:browser` command is available for interface debugging. Remote progress on
port 8001 runs the packaged AppImage and streams screen captures, forwarding
bounded mouse/keyboard events to its desktop window. Its authenticated session
viewer does not serve application assets or proxy the scientific API. Files are
transferred into the preview's own runtime directory and completed native
downloads can be retrieved through the viewer. The headless preview's explicit
sandbox override does not change the production AppRun or desktop preferences.

Linux packaging adds an AppRun launcher through electron-builder's
[afterPack hook](https://www.electron.build/v26/docs/features/hooks/).
It preserves the caller's arguments and bundled library paths. The upstream
launcher's automatic `--no-sandbox` fallback and desktop-entry default are
replaced so a missing host sandbox does not silently change production settings.
Desktop smoke tests explicitly opt into their headless sandbox exception and
verify the effective Electron command-line flags; renderer preferences alone
do not prove that the operating-system sandbox is active.

Electron disables Node integration, enables context isolation and renderer
sandboxing, blocks navigation/new windows/permissions, and keeps its profile,
logs, downloads and engine data in the configured runtime directory. API requests
require a random session token; hostile origins and Host values are rejected.
The engine monitors its desktop parent so a shell crash does not leave it running.
The selected desktop workspace is stored atomically in
`.config/desktop-settings.json`, independently of the ephemeral engine origin.
An asynchronous, origin/main-frame-checked preload bridge exposes only validated
workspace identifiers; renderer filesystem access remains disabled.

## Authoritative state

Native plot windows are registered `BrowserWindow` instances sharing the same
private engine. `desktop/plot-windows.cjs` validates scoped descriptors and checks
the exact originating main frame, known window and private engine origin for all
plot IPC. Popups cannot redirect themselves to another workspace or use the main
window's workspace/draft/PDF persistence capabilities. Arbitrary `window.open`
remains denied. Each renderer has its own query cache and view state; saved
scientific mutations stay in SQLite.

Plot navigation uses a revision-bound metadata planner in `plot_navigation.py`.
The native broker captures each affected view's generation and dirty state,
matches populations by unique ancestry, checks every participant's cohort and
scientific parameter definitions, and validates the revision and view membership
again before committing all native descriptions. Renderer navigation events carry
only validated scoped view state. Copied X/Y and 3D scalar transforms preserve
display coordinates across sample scales; navigation never changes SQLite history.

The main process verifies workspace revisions through a minimal authenticated
SQLite revision endpoint before notifying matching windows. Mutation notifications
trigger a refresh, and a 1.2-second poll of unique open workspaces covers imports
and other writes. Polling does not deserialize event/workspace snapshots. An open
gate editor keeps its base revision, preserves its draft and requires explicit
review after another window changes the workspace. Atomic revision checks remain
the final write guard.

Visual gate editing uses a local draft in the primary plot panel or review dialog
in either native window. Canvas double-click hit testing prioritizes saved borders
over overlapping fills and resolves their gate IDs to original scientific geometry.
The inline editor temporarily displays the complete parent in the gate's saved
coordinates, retaining the original view for Apply or Cancel. It shares drawing's
source snapshots, navigation locks and native close protection. Switching to numeric
settings preserves the draft and its base revision. Removing a source retains the
draft until the source is restored and explicitly reviewed. The
authenticated shape-preview endpoint validates a complete candidate workspace
without writing it and uses an isolated 64 MiB scientific engine per request.
Reusing the saved engine's mask cache would collide on the unchanged workspace
revision and gate identifier. Preview axes retain ordered per-dimension
definitions, including different compensation references for the same parameter;
only the edited gate is projected. Pointer movement updates geometry locally,
and debounced full-event counts are accepted only for the current draft and
coordinate basis. Escape or a viewport change cancels an active drag. Saving
uses the ordinary revision-checked mutation, history and cross-window refresh.
See [GATE_EDITING.md](GATE_EDITING.md) for the supported interaction scope.

`freehand.ts` records fixed-interval CSS-pixel samples in the captured plot basis,
with an explicit vertex limit and no subsequent simplification. The plot retains
its drawing snapshot through concurrent revisions and freezes tracing on resize.
Freehand saves an ordinary scientific polygon with drawing provenance. Dense
float64 polygon masks conservatively index edges by Y intervals and preserve the
original even-odd/boundary predicate; numeric and overlap guards keep the original
vectorized path where indexing is unsuitable. See [FREEHAND.md](FREEHAND.md).

`partitions.py` expands linked bisectors/quadrants into ordinary half-open native
hyperrectangles. Workspace validation requires complete families sharing their
parent and scientific coordinate definitions. Editing any member replaces the
whole family's geometry in one revision while preserving IDs and individual
labels. Read-only previews use the same family update in the isolated draft engine
and return every member's full-event count. Propagation and interchange remap
family IDs per target; deletion includes the entire family and its dependencies.
Spider families retain one center, four ordered angles and a saved X/Y scale.
`spider.py` classifies their unbounded sectors with exponent-aligned cross
products and exact rational signs near cancellation. Every saved member shares
one immutable uint8 classification array in the bounded scientific cache; draft
previews classify with an isolated engine. Rational ray clipping changes only
the displayed viewport, including nonlinear projections and vector reports.
Portable projects preserve the native geometry. Standard GatingML export reports
unsupported spider geometry explicitly rather than cropping scientific gates.
See [SPIDER_GATES.md](SPIDER_GATES.md) for conventions and remaining validation.
Curly families retain a transformed center, nonnegative raw-unit coefficients
and a versioned positive-intensity square-root convention. `curly.py` classifies
original scientific columns before display transforms, with exact radical
comparisons near boundaries and one immutable label cache per family. Sampled
curves affect display and reports only; their pieces preserve adjacency even
when the positive noise limits cross. Axis clipping and WSP lookup scales are
rejected for their ambiguous center inverses. Native editors share center drags,
numeric coefficients, isolated previews and atomic family updates. Portable
projects and event CSV/FCS retain scientific behavior; unsupported GatingML
export fails explicitly. See [CURLY_GATES.md](CURLY_GATES.md).
`gatePartitions.ts` captures ordered native axes for histogram/CDF and quadrant
drawing. GatingML retains standard geometry and stores linking in schema-supported
custom information. See [PARTITIONS.md](PARTITIONS.md).

`plot_coordinates.py` resolves native dimensions by their ordered axis before
falling back to a unique named definition. Channel-keyed dictionaries cannot
represent repeated parameters with different compensation, scales or ratios.
Exploration, 3D streams, report prototype scales and navigation share this rule;
overlays match distinct compatible axes. Frontend axis controls and gate drawing
preserve the corresponding dimension. Ambiguous scalar or rearranged requests
are rejected rather than silently choosing another basis. Fixed-matrix cohort
checks include detector/output order, matrix kind, coefficients, background and
weights. See [PLOT_COORDINATES.md](PLOT_COORDINATES.md) for remaining controls.

Session descriptions and native bounds use bounded, flushed atomic files under
`.config/plot-windows.json`. Individual close removes a view; application exit
saves the remaining views and destroys all children before the engine exits.
Unsaved gate editors require close confirmation. Missing sources retain their
identifiers and display unavailable state until restored or explicitly replaced.
The progress viewer registers only the actual main/plot windows, serializes window
selection with input delivery and captures the selected window's pixels. Its
selection endpoint uses the existing session and origin checks.

`data/workspaces.sqlite` is authoritative for workspaces. Each edit validates the
entire state and creates an atomic snapshot under a SQLite immediate transaction.
Revisions are monotonic even across undo/redo. Every mutation carries an expected
revision; a conflict returns 409 without applying an edit. Undo/redo move a cursor;
editing after undo drops future snapshots. SQLite WAL and FULL synchronous mode
protect completed edits. Scientific data is retained when undo removes a sample.

Raw, preprocessed event arrays live in `data/events/<workspace>/<sample>.npy`.
They are float64, immutable, memory-mapped, shape checked, and written via a synced
temporary file followed by rename. They contain only acquisition channels.
Derived channels are appended in metadata and computed from validated formulas.
Spectral output names are declared in `Sample.unmixed_parameters` and appended
as virtual channels. They do not change the acquired array shape/order. Assignment
creates distinct output names atomically; changing a matrix updates every sample
already using it. Inactive output definitions remain valid references but evaluate
to NaN. Raw export does not invent values for unmeasured spectral outputs.
Computed analysis channels reference immutable arrays in
`data/analyses/<workspace>/<analysis>/<sample>.npy`. Rows correspond exactly to
acquisition event IDs, including NaN rows for excluded/unmapped events. Integer
fitted row IDs live in adjacent `.fit.npy` arrays. Source input definitions,
array hashes and software versions are stored in the result manifest.
Portable archives contain the current workspace, event/analysis arrays and fitted
event IDs; they do not
currently contain local undo history. Restoring creates a distinct workspace.
ZIP members are read directly without extracting paths, with size/hash/schema
validation before committing metadata.

## Scientific invariants

WSP saved tables are converted as data in `wsp_tables.py`. Imported columns keep
per-sample population IDs, explicit unavailable markers, parameter aliases and
fixed matrix IDs from their source import. Missing bindings remain undefined.
Input sample order is retained for fixed/relative row references and first-source
controls. Keyword migration adds only missing non-system annotations, preserving
existing target values and acquisition metadata. XML is flushed and synced before
the workspace transaction commits. Original bytes, table attributes and binding
reports travel with the portable project; imported formulas use the restricted
table AST and never execute workspace scripts. Supported native intensity
statistics differ from FlowJo's binned/scale-based conventions; import requires
acknowledgement. See [WSP_TABLES.md](WSP_TABLES.md).

- Measured events are rows. Spillover rows are sources, columns measured detectors.
  Conventional correction solves `S.T @ true.T = measured.T`. Spectral correction
  solves weighted least squares after subtracting electronic background, with
  weights interpreted as inverse detector variances. Rank/conditioning checks use
  the weighted reference matrix. Nonfinite acquired rows have undefined outputs.
- Control calculation uses positive-minus-negative detector medians. Conventional
  rows are normalized by primary separation, spectral rows by peak response;
  negative coefficients are retained. Optional autofluorescence is the normalized
  unstained median spectrum minus background.
- AutoSpill separately fits Huber regression rows from acquired controls and
  refines compensated residuals with matrix updates, linear then native-biex
  coordinates, natural cubic interpolation and linear extrapolation. Its exact
  settings, raw hashes, gate graph, captured event IDs and diagnostics travel in
  matrix provenance. Optional unstained AF occupies an empty measured detector
  in the square matrix. Residual convergence does not establish physical truth;
  correlated signals and tail conditioning can bias coefficients. See
  [AUTOSPILL.md](AUTOSPILL.md) for the independent R comparison and boundaries.
- Control cleanup masks use acquired columns and reject virtual gate dimensions.
  Raw and compensated masks have separate cache keys. Threshold bounds are lower
  inclusive and upper exclusive. Same-sample positive/negative overlap and too few
  finite events are errors; weak separation/saturation/negative coefficients and
  similar signatures are diagnostics. Preview is read-only, and stale previews
  cannot be saved as new matrices. Original raw hashes, populations, medians,
  coefficients and subsequent edit fields remain in matrix provenance.
- Gain, stored log scale and `$TIMESTEP` preprocessing follow FlowIO.
- Gating is exact for the full event matrix and always intersects its parent mask.
  Boolean NOT complements within its parent, not outside that population.
- Rectangles/ranges use inclusive lower and exclusive upper limits. Polygons and
  ellipses include boundaries. Quadrants partition finite events exactly; upper
  and right include threshold events.
- Gate coordinates carry the transform in effect when drawn. Changing a display
  scale preserves membership; changing compensation deliberately recalculates it.
- Zero events remain valid populations. Undefined statistics are null, not zero.
  Nonfinite values are retained in data and excluded only from calculations that
  require finite values. Counts always describe population membership.
- CV uses sample SD divided by absolute mean. Robust CV is 100×1.4826×MAD/|median|.
  These named formulas are explicit; all FlowJo statistic conventions still need
  a comprehensive reference comparison.
- Derived formulas use a parsed allowlist of AST nodes and elementwise operations;
  there is no `eval`, attribute access, Python import, file access, indexing or
  comprehension. Dependencies are checked for missing channels and cycles.

## Performance

Scientific computations use native NumPy/SciPy/FlowUtils loops. Calculated columns
and masks share an immutable LRU array cache capped at 256 MiB. Cache keys include
workspace revision, sample, compensation state, channel and transform. Plot data
is aggregated to a bounded histogram; probability views retain full-event mass,
CDF counts use all finite events at display edges, and capped scatter/backgate/outlier points are
sent to the renderer. CSV/FCS parse and array writes run outside the async loop.
Import decodes at most 262,144 values per event chunk (2 MiB of float64),
checks every dataset's declared DATA length and forward relative link, and
hashes/writes arrays in one pass. CSV uses a counting pass and a bounded numeric
pass. Metadata segments have separate size limits. Per-file chain rollback,
batch cancellation and durable move intents precede one revision-checked
workspace commit. Startup recovery consults all undo/redo snapshots before
removing uncommitted event files. See [IMPORTS.md](IMPORTS.md). Downstream model
input preparation still has whole-array paths. `event_exports.py` stages population
FCS 3.1 double-precision DATA and numeric CSV in 16,384-event chunks, binds source
snapshots and completed files to their revision and SHA-256 digests, and leases
prepared files during downloads. Native main-process saving streams authenticated
downloads and verifies the saved file. FCS import restores checked display/correction
metadata and exact uint64 merged origins within the existing import transaction.
See [EVENT_EXPORTS.md](EVENT_EXPORTS.md).

`channel_aliases.py` prepares revision-bound, SHA-256 reviewed cohort mappings.
`Sample.aliases` maps virtual channel names directly to acquired or unmixed
parameters; empty mappings are omitted from legacy serialization. `Engine.column`
and `ChunkColumns` resolve bindings before correction or transformation, without
copying acquired arrays or adding alias-specific correction caches. Fitted-input
fingerprints and report source closures follow the actual source definitions.
Apply validates the complete candidate workspace under the store transaction,
protects saved consumers from alias removal, and records one undoable revision.
Stored FCS metadata reconstructs alias display definitions over original detector
columns; materialized exports keep bindings in scientific history. Gating-ML
exports use resolved detector names. See [CHANNEL_ALIASES.md](CHANNEL_ALIASES.md).

## Custom tables

Saved definitions own stable column IDs and validated formula dependencies.
Sample/group/filter scope is resolved before aggregates and comparisons. Exact
population-name paths are mapped per sample; missing/ambiguous paths are explicit
cell states. Statistical columns use full-event masks and acquired/compensated
parameter values. Model columns read immutable reports and follow replacement
bindings when configured. Model replacement/rename preserves mapped population
references. Staleness traverses derived parameters and the population dependency
graph, including QC; historical values require an explicit column setting.

Table formulas use a separate restricted AST language for row arrays and finite
aggregates. Numeric reductions and t-tests scale/center values to avoid avoidable
overflow and cancellation; rank tests preserve original observations/differences
so scaling cannot change ties. Comparisons require sample rows, validate pairing,
and report exclusions, undefined tests and the selected multiplicity family.

Read-only evaluations capture the workspace revision, paginate only the response,
and compute global heatmap ranges and full-scope pivots/comparisons. Exports capture
the same validated definition/revision and retain all selected rows. XLSX streams
rows to project-local temporary files, preserves oversized text in ordered chunks,
splits status sheets at the Excel row limit and disables formula/URL conversion.
Response completion removes temporary downloads. CSV quotes formula-like strings.
JSON/XLSX retain a source/hash/software manifest. See [TABLES.md](TABLES.md).

## Background analyses

A durable job manager uses Python's spawn context on every platform and permits
at most two workers and 20 queued/running jobs. Workers read an immutable input
snapshot and event files, calculate outside the HTTP process, then atomically
write checksummed outputs. Cancellation terminates/joins the process; closing the
engine terminates workers and checkpoints interrupted status. A worker also
monitors its parent so a hard engine crash cannot leave it computing indefinitely.
Jobs do not auto-resume on restart or mutate workspaces implicitly.

AutoSpill uses the same queue but stores a matrix report, without derived event
arrays. Its fingerprint excludes assigned compensation, display transforms and
cosmetic names because regression and parent gates use acquired coordinates.
The final transform implementation is included in that fingerprint. Apply checks
the current revision and scientific inputs; unmet tolerance requires a recorded
acknowledgement. Matrix creation/assignment and optional scatter polygons/raw
parent copies form one atomic edit. Saved reports reconstruct the original
calculated matrix even after manual editing, retain the edit fields and work
without the local job journal after archive restoration. Preview verifies input
array hashes and uses the same captured event IDs for each selected pair.

Successful results require explicit addition with the original workspace
revision. The scientific fingerprint follows input array hashes, acquisition
channel order, compensation, transform/formula/computed dependencies and the
selected gate dependency graph. New analysis parameters do not change that
fingerprint. Input changes mark saved parameters stale; frozen results are still
available as documented snapshots, and rerunning uses current settings/data.
Hash/shape, mapped row counts and sorted unique fitted IDs are checked when adding
or restoring results. Output arrays are retained when undo removes an analysis.

PCA uses scikit-learn full SVD. Optional standardization uses fitted-set population
SD; displayed component weights are the eigenvector coefficients. UMAP is seeded
and single-threaded within each worker, and maps remaining eligible events through
the fitted transform. t-SNE deliberately assigns coordinates only to fitted event
IDs. FlowSOM uses the published SOM implementation plus a seeded co-assignment
consensus adapter, with 100 node resamples and average-linkage metaclustering.
PhenoGraph builds an exact Euclidean nearest-neighbour graph with Jaccard weights,
averages reciprocal edge weights, and retains the best modularity from seeded
igraph Louvain restarts. It labels fitted event IDs only; discarded small
communities receive 0. Community IDs, sizes and per-sample memberships are checked
before application. See [docs/PHENOGRAPH.md](PHENOGRAPH.md).
All algorithms retain exact sample/event identity. Balanced sampling distributes
quotas equally and reallocates unused slots from small populations; proportional
sampling samples uniformly from the pooled eligible event IDs. Preprocessing
currently limits jobs to 40 million input values rather than streaming them.
Raw analysis evaluates populations on acquired values and retains acquired copies
of their parent graphs when applied. Later compensation assignment changes the
original population without changing the saved raw analysis population. Existing
compensated analyses and shared fingerprints keep their previous default behavior.

## Compatibility still to prove

The manifests and launchers target Windows, macOS and Linux. Native installers,
signing/notarization, ARM builds and native OS-specific tests are distinct release
gates, not implied by a Linux build. The existing tests use synthetic numeric data
and generated FCS files. Real instrument, FlowJo workspace and GatingML reference
fixtures are required to establish broad compatibility and scientific parity.

## Gate interchange

XML is size/depth/element bounded, rejects DTD/entities/external references, and
GatingML is validated with unmodified local ISAC schemas before conversion.
Preview plans and original XML are scoped to workspace and revision; application
validates the entire dependency graph and commits one snapshot. Candidate count
calculations use a separate cache so a failed import cannot poison the next
committed revision. Unsupported
parents and Boolean operands also exclude dependent gates; they are never
silently detached. Partial conversion requires explicit review in the product.

Explicit dimensions carry channel/ratio definitions, inclusive lower and
exclusive upper gate bounds, transforms with optional output clamping, and an
independent compensation reference (`uncompensated`, `FCS`, sample, or a fixed
matrix ID). Ratio bounds apply before the channel scaling transform. Raw source
arrays and matrices remain separate from transformed gate coordinates. General
invertible quadratic forms from the ISAC corpus are preserved and reported.
Ellipsoids and open rectangles can have more than two dimensions; a 2D plot does
not display a complete higher-dimensional boundary.

Original XML is immutable in `data/interchanges/<workspace>/<record>.xml`, hashed
in the import record, and checked during download/archive export/restoration.
Reports retain mappings, skipped definitions, original counts and computed
counts. GatingML export encodes native XOR/outside/container gates with standard
Boolean helper expressions and validates its output. Unsupported transform or
matrix semantics fail explicitly rather than changing the event selection.
Imported coordinate contexts also persist in saved layout plots. Scientific
fingerprints include fixed gate matrices and ratio/scale definitions while
preserving older fingerprints whose newly added fields have default values.

## Native publication reports

`plotting.py` shares scientific plot payloads and gate projection between desktop
exploration and the publication renderer. `reports.py` migrates grid layouts,
resolves exact batch source/population paths, evaluates positioned page objects
and creates reproducible vector pages. `report_sources.py` captures dependency
closures and checks staleness and artifact hashes. `report_cache.py` retains
bounded panel results independently of their page positions. `report_svg.py`
sanitizes SVG and rewrites local references using SVG2 href, including glyph paths
that Electron's HTML parser must recognize.

`report_tables.py` reuses complete, bounded table evaluations across raw, pivot
and comparison panels. Page planning resolves row/column windows per prototype,
batch tile and table; snapshot hashes and continuation geometry enter the source
review. Native export renders those output descriptors directly, retaining the
saved comparison's full test family even when fewer measures are displayed.
Independent prototype selection locates its first output page, so preceding
table continuations cannot redirect editor previews or single-page exports.

The isolated preload exposes bounded report-draft and PDF descriptors. Main
process IPC requires the trusted top-level window and verifies prepared DOM,
current source proofs and hashes around the native save dialog and PDF render.
A worker attaches the scientific manifest and writes the selected PDF atomically.
The report renderer also supplies SVG batches and DPI-tagged PNG pages. See
REPORTS.md for limits and the remaining publication scope.

`graph_views.py` supplies probability thresholds, mass-preserving smoothing and
CDF edge counts to the shared exploration/report payload. Probability contour
paths use ContourPy in normalized density coordinates and have a reported vertex
limit. Native descriptors validate and persist graph options, resolution and the
two/four-bound axis shape independently for every plot window.

The authenticated plot route returns a strict `JSONResponse` of the shared
JSON-compatible payload, encoded inside its worker. This avoids FastAPI's generic
recursive conversion of every bin, contour point and overlay on the response
path. The complete wire format, scientific values, request validation and local
access/header guards are retained; workspace/model responses continue their
existing conversion. Tests compare complete response bytes against the previous
encoder for every graph mode, empty/constant/extreme data and large fragmented
regions. A preserved-application ASGI benchmark measures the full request path;
native renderer timings and vector output are checked separately. See
GRAPH_VIEWS.md for scope and measured results.

`three_dimensional.py` supplies full-event XYZ coordinate bases, finite/viewport
counts, scalar scales, original uint64 event identities and bounded 32-byte binary
records. Metadata and chunks are authenticated and bound to the workspace revision
and geometry hash. Camera/glyph changes reuse geometry. Native descriptors also
validate six-limit 3D axes and a bounded camera/settings schema. Three.js consumes
interleaved WebGL2 buffers; a full-event software path uses the same orthographic
camera when GPU contexts are unavailable. `report_three_dimensional.py` projects
the same normalized coordinates into vector figures and records event-ID hashes,
camera clipping and shared overlay transforms/scales. Scientific gate masks retain
the full event coordinates and are independent of display precision or sampling.

`magnetic.py` resolves bounded translations in each gate's own compensated,
transformed coordinate basis, including explicit ratios. A full-parent count
grid identifies the nearest local maximum; exact membership refines and verifies
the result against the original anchor. The stored gate retains that anchor and
the versioned numerical convention. `Engine.resolve_gate` supplies the same
geometry to membership, planar/XYZ overlays, reports and GatingML snapshots.
Its separate cache holds at most 256 small resolution records, keyed by workspace,
revision, sample, compensation mode and gate fingerprint. Draft preview validates
the full dependency graph without writing workspace state or history. Inactive
tracking fields are omitted to preserve legacy serialization. Vector report
provenance records original and resolved geometry; portable projects retain
tracking settings. See MAGNETIC_GATES.md for scientific and exchange limits.
Automatic density gating uses a read-only, revision-checked full-parent preview.
The bounded scientific array cache retains histogram/smoothed fields; draft
polygons never populate saved gate-mask caches. A selected contour retains its
excluded rings and ordered scientific dimensions. Native main and popup plots
review the exact full-event polygon count before an ordinary gate mutation.
GatingML encodes excluded rings as standard Boolean intersections/complements.
See [Automatic gates](AUTOGATING.md).

Plot definitions can carry independent `PlotDimension` values for X/Y and
3D Z/color/size. `plot_coordinates.resolve_dimension` checks ratio inputs,
acquisition detectors and fixed matrix references without modifying the
workspace. Gate-native editing rejects view overrides. Native descriptors,
navigation, typed 3D stream identities and report provenance retain these
definitions; absent optional fields preserve legacy serialization. See
[Independent axes](AXIS_DEFINITIONS.md).
