# AutoSpill compensation

CytoForge implements the published AutoSpill algorithm for conventional spillover
and a rectangular spectral extension, including optional autofluorescence unmixing. It uses Huber
regressions, automatic scatter cleanup and iterative residual correction. The
positive/negative median estimator remains available in the same control wizard.
The saved-matrix [AutoSpread review](AUTOSPREAD.md) estimates spillover spreading separately.

The primary references are [Roca et al., Nature Communications 12, 2890
(2021)](https://doi.org/10.1038/s41467-021-23126-8), the
[author's R implementation](https://github.com/carlosproca/autospill/tree/1e60e86337b297f1dd9ffec6701d8010dd06175b),
and the FlowJo documentation for
[AutoSpill](https://docs.flowjo.com/flowjo/experiment-based-platforms/plat-comp-overview/autospill-compensation/)
and [autofluorescence subtraction](https://docs.flowjo.com/flowjo/experiment-based-platforms/plat-comp-overview/autofluorescence-subtraction/).
Agreement with the open R reference does not establish closed-source FlowJo or
real-instrument accuracy.

## Controls and workflow

Open **Compensation → Control wizard** and select **AutoSpill**. Choose acquired
fluorescence detectors and assign exactly one control per primary detector.
Use the entire cleaned cell or bead population, including its continuous
fluorescence distribution. Separate positive/negative fluorescence gates are
unnecessary. All controls need the selected acquired detectors; their acquisition
column order may differ.

Parent cleanup populations are evaluated on acquired values before any assigned
compensation, using their saved coordinate transforms. Acquired values include
the importer's gain/log/time preprocessing. Inline ratios use their acquired
numerator and denominator, saved coefficients, clipping and transform. Gates
based on virtual parameters are rejected.

Reviewed acquisition-QC populations use their saved event flags and excluded
acquisition bins inside the acquired-coordinate parent. A new calculation
requires current QC. The calculation captures the reviewed flags' identity,
so applying its matrix does not change its own input selection. Flag files are
checked before calculation, preview and saving, even with a warm event cache.
Changing the selected bins, gate definitions, acquired data or captured flags
invalidates the result. A changed QC producer must be reviewed before use in
a new calculation; saved captured populations retain their reviewed flags.

Automatic scatter cleanup uses configurable FSC/SSC channels and defaults to
the densest non-corner population. Inspect the scatter polygon before applying:
beads, several cell populations, poor separation or acquisition artifacts can
require a different peak, threshold or manually selected raw parent. Disable
automatic cleanup when scatter is missing or unsuitable.

For autofluorescence subtraction, select an otherwise unused measured detector
with a strong autofluorescence signal and assign representative unstained cells
to that detector's control row. The empty detector is part of the square matrix;
its corrected output represents autofluorescence in that detector's units.
Single stains, unstained cells and experimental samples must share the relevant
autofluorescence spectrum. Different cell types may need separate matrices.

Run the calculation and review the final matrix, convergence history,
condition/rank, every residual detector pair, control counts and acquired versus
compensated scatter. Both scatter previews use the same 300 captured event IDs;
their linear/arcsinh display settings do not alter the calculation. The cleanup
preview contains at most 600 scatter points. Pairwise fitted counts are recorded
separately because fluorescence-tail trimming occurs after selecting the
regression input population.

Download the JSON calculation report or matrix CSV before saving. Choose target
samples with compatible acquired detectors, then **Save and apply matrix**.
Saving the control populations is enabled by default and optional. Cleanup
polygons and raw copies of their parent/Boolean/ratio dependencies retain acquired
coordinate references. Copies of QC gates pin the reviewed flags. Controls can
be saved with automatic scatter cleanup disabled, and their per-output mapping
prefills spreading review. Assigning a matrix does not change their membership.
Matrix creation, assignments and helper populations form one undoable edit.

A stopped calculation that misses the requested tolerance is labeled
**Tolerance unmet**. Saving requires an explicit acknowledgement retained in
provenance. Singular/nonfinite matrices and insufficient controls fail without
changing the workspace. Previous jobs and saved matrix reports remain accessible
after closing the wizard, restarting the engine or restoring a portable project.

## Numerical definition

Events are rows; spillover rows are sources and columns are measured detectors.
Every primary diagonal is one. Corrected acquired detector intensities are
`measured @ inverse(spillover)`, in primary-detector equivalent intensity units.
The biexponential coordinates are used for refinement only; they do not replace
the corrected intensity outputs.

### Rectangular spectral controls

In the AutoSpill wizard, choose **Matrix type → Spectral unmixing**. Select all
measured fluorescence detectors, then add or remove source rows to match the
fluorophores in the experiment. There must be 2–64 independent sources and at
least as many detectors (up to 512). Each source needs a whole single-stained
control population, an output name distinct from acquired parameters, and a
selected peak detector. Several sources can share a peak detector when their
full spectra are linearly independent. Choose the actual peak; outputs are in
that detector's equivalent acquired intensity units.

Include matching unstained controls as spectral sources and mark each AF output
in the **Autofluorescence references** selector. Multiple AF references are
supported by both spectral AutoSpill and the median spectral estimator. Each
reference consumes one source row and needs a distinct name and an independent
spectrum. Select populations representing the AF spectra in the controls and
experimental cells. Correlated staining and autofluorescence can produce biased
signatures even when regression residuals are small.

The AF review measures each reference's angle from the joint span of every other
source in detector space, using the selected detector weights. It also shows the
closest source and signed weighted cosine. An orthogonal fraction below 0.1
(about 5.74°) prompts review for weak separation. Pairwise similarity alone can
miss a reference explained by a mixture of several sources. These diagnostics
describe numerical identifiability; they do not classify cells or quantify their
biological AF fractions. They do not certify the choice of reference populations.

Distinct AF reference spectra for heterogeneous samples are discussed in
[Roet et al. (2024)](https://pure.eur.nl/ws/portalfiles/portal/154751097/Unbiased_method_for_spectral_analysis_of_cells_with_great_diversity_of_autofluorescence_spectra.pdf).
This implementation accepts manually selected references; automatic extraction,
clustering and biological validation of such classes remain open work.

Median calculation can save acquired copies of positive, negative and AF control
populations with the matrix. Raw inline thresholds become explicit acquired
gates, parent and Boolean dependencies are copied, and reviewed QC selections
retain pinned event flags. Reusing these populations after matrix assignment
retains their original acquired coordinates. Spreading review prefers the saved
populations. If populations were not saved, recreate inline thresholds as gates
before selecting them for spreading; thresholds are never silently discarded.

New median calculations and population saves verify acquired file hashes and
reviewed QC flag hashes, including when numerical caches are warm. Live QC must
be current when calculated; captured reviewed flags remain reusable if their
producing result becomes stale after matrix assignment.

The optional detector settings accept fixed acquired background offsets and
positive inverse variance weights, in measured-detector order. Background is
subtracted before weighted least-squares unmixing. The initial robust regressions
include intercepts; offsets do not change the initial slope definition. Use
noise measurements to choose weights. Changing selected detectors retains the
settings by detector name. Biex overrides instead belong to unmixed source
outputs, whose ranges determine the refinement coordinates.

Initial robust regressions produce a source-by-detector matrix `U`. Refinement
uses weighted least squares to unmix each control, regresses every secondary
source against its primary source, forms a source-by-source off-diagonal error
matrix `E`, applies `U += damping * (E @ U)`, and renormalizes each source's
selected primary coefficient to one. It retains the conventional algorithm's
linear/biex switching, damping, plateau detection and two-step target check.
Assigned compensation does not enter control extraction.

**Cross-source convergence and detector reconstruction are separate checks.**
The correction `E @ U` preserves the initial spectral row space. An omitted
source, nonlinear detector response or a poorly estimated spectrum can leave
measured detector signal unexplained even after source residual slopes converge.
Each control therefore reports reconstructed detector residuals, their robust
slopes versus its acquired primary signal, RMS residuals, the largest
absolute residual slope and relative reconstruction RMS. Relative RMS is
`sqrt(sum(residual²) / sum(background-corrected acquired signal²))` over the
control's selected events and detectors; it detects unexplained nonlinear
signal even when residual slopes are near zero. Either diagnostic exceeding
the configured linear
tolerance (default `0.01`) triggers a warning; source convergence uses the final tolerance (default
`0.0001`). Saving requires explicit acknowledgement if either tolerance is
unmet. A small reconstruction slope does not prove the spectra are biologically
correct; random detector noise and correlated sources need separate review.

The matrix review is source-by-detector; the convergence residual table is
source-by-source. Control previews show acquired peak-detector axes alongside
unmixed source axes for the same captured event IDs. Shared peaks can make the
raw preview diagonal while its unmixed counterpart separates sources. The JSON
report retains weights, background, output transforms, regression counts and
both diagnostic spaces. Saving appends virtual outputs to target samples;
acquired arrays and acquired parameter order remain unchanged. Control-file
checksums are rechecked before applying the calculation.

The spectral path is independently implemented from equations 2–10 of the
published paper. Seven synthetic cases are compared with an independent R
adaptation using the pinned author's Huber routine and the existing native biex
lookup fixtures. They cover shared peaks, independent/correlated autofluorescence,
outliers, detector noise, an unexplained component, detector reordering and
4096-bin biex coordinates. Known latent sources separately test recovery and
the correlated-autofluorescence limitation. This is not a comparison with
FlowJo's closed-source implementation or external biological controls.

1. Optional cleanup uses a 100×100 separable Gaussian KDE, normal-reference
   bandwidths, local maxima and nearest-peak Voronoi regions. A corner exclusion,
   median ± three scaled-MAD region and density-threshold convex hull select the
   population. Convex-hull boundaries are included.
2. Each primary/secondary pair trims rounded tail order statistics and fits an
   intercept and slope with Huber IRLS (`k=1.345`, MAD scale, relative residual
   convergence `1e-4`). Centered/scaled arithmetic protects large/small signals.
   A failed robust fit uses the reference's OLS fallback and reports it explicitly.
3. Initial slopes define the spillover rows. Refinement regresses the compensated
   controls, forms an off-diagonal residual matrix `E`, updates
   `S += damping * (E @ S)`, and renormalizes the primary diagonals.
4. Refinement starts in linear coordinates and switches to biexponential
   coordinates below the coarse tolerance. Fitted line endpoints are transformed
   back to intensity to express residual slopes in compatible units. Plateau
   history reduces damping; target tolerance, plateau or the iteration bound
   determines the stopping reason. A final pass verifies the resulting matrix.

The native refinement lookup matches flowWorkspace/cytolib: single-precision
lookup positions, legacy integer stopping behavior, natural cubic splines and
linear extrapolation outside the table. CytoForge implements interpolation with
SciPy; it does not ship R or cytolib. Workspace display/interchange transforms
retain their existing lookup behavior. Native compatibility resets widths
outside 0.5–3 decades to 0.5 decades and reports that reset. Extrapolated events,
saturation, regression fallbacks and poor conditioning are reported.

| Setting                                     |                         Default | Accepted range                                           |
| ------------------------------------------- | ------------------------------: | -------------------------------------------------------- |
| Detectors and matching controls             |                  Selected panel | 2–64                                                     |
| Minimum finite cleanup events               |                             100 | 20–100,000                                               |
| Regression event limit per control          |                             All | Explicit 100–2,000,000, at least the minimum             |
| Scatter density threshold                   |                            0.33 | Strictly between 0 and 1                                 |
| Scatter target peak                         |                               1 | 1–64, if present                                         |
| Tail fraction per boundary                  |                            0.01 | 0–0.1                                                    |
| Maximum refinements / regression iterations |                       100 / 100 | 1–200 each                                               |
| Coarse linear tolerance                     |                            0.01 | Greater than zero, at most 0.1                           |
| Final residual tolerance                    |                          0.0001 | 1e-8–0.01                                                |
| Plateau tolerance                           |                            1e-6 | 1e-10–0.001                                              |
| Reduced damping                             |                             0.1 | Strictly between 0 and 1                                 |
| Biex lookup range                           |                             256 | 256 or 4096                                              |
| Biex positive / negative / width / top      | 4.418539922 / 0 / −100 / 262144 | Valid transform parameters; detector overrides available |

The default uses every finite event remaining after cleanup. An explicit limit
selects uniformly spaced original event IDs and records the number omitted.
Regression inputs are capped at 64 million float64 values (512 MB for those
arrays, not a process-memory guarantee). Exceeding that bound fails with an
instruction to choose a limit or smaller populations. Preparation, compensated
copies and KDE buffers add memory. The shared job manager permits two workers
and 20 queued/running jobs; calculations support progress, cancellation and
interrupted-job recovery without automatically applying a result.

## Validation and limitations

`artifacts/benchmark-autospill.json` records eight synthetic controls with 50,000
events each and eight detectors. All 400,000 events were used. On this host the
calculation took 1.62 seconds, including input hashing, preparation, regression,
refinement and diagnostics; it excludes generation, file writes, scatter cleanup,
HTTP and rendering. The largest physical coefficient error was `2.10e-5`.
This timing covers one host and this synthetic distribution only.

`tests/fixtures/autospill` contains synthetic acquisitions and outputs generated
by unmodified numerical functions from author commit
`1e60e86337b297f1dd9ffec6701d8010dd06175b`. The validation driver supplies thin
matrix/compensation adapters and R's convex hull in place of tripack. It compiles
the native lookup methods from cytolib commit
`1f886d99b51c520b7adf19d1cc570bbfdc61c315` in an ignored validation cache and uses
R's natural spline implementation. Neither adapter uses CytoForge's numerical
functions to generate expected results.

Four full fixtures cover secondary outliers, correlated autofluorescence,
automatic debris/doublet cleanup and independent bounded autofluorescence sources.
Tests compare initial/final coefficients, residual slopes and iteration counts
with the R results at `2e-12` tolerances, and cleanup membership event by event.
Four transform configurations cover 256/4096 tables, negative decades, width
reset and extrapolation against native lookup and independent R spline results.
Additional tests cover raw parents, geometry/Boolean copies, constant secondary
signals, negative coefficients, extreme scales, nonfinite events, explicit
sampling, cancellation, atomic saves, revisions, stale inputs, manual edits,
undo/redo and archived reports.

The fixtures also preserve a scientific limitation. Correlated stain and
autofluorescence signals yield biased coefficients in both implementations despite
small residual slopes. In a noise-free independent Cartesian fixture, the default
1% tail conditioning shifts a known coefficient by about 0.0057; the final
residual is below `1e-6`. Disabling trimming recovers that fixture's known matrix
to floating-point precision. This is evidence to inspect distribution/trimming
sensitivity, not a recommendation to disable trimming for every acquisition.

Two explicit extensions differ from the R implementation: tied secondary trim
boundaries are retained and reported when strict trimming would remove useful
quantized/constant data; a plateau is never called converged unless the requested
residual tolerance is reached. Results remain available for acknowledged review.

Saved provenance captures raw event hashes, acquisition order/ranges, selected
gate dependency graphs, exact settings, method/reference versions, original
matrix, cleanup geometry, regression diagnostics, warnings and preview event IDs.
Scientific changes invalidate an unapplied result and mark saved reports stale;
names, colors, display scales and assigned compensation do not change the raw
calculation fingerprint. A changed workspace revision must still be refreshed.
Manual matrix edits retain the original calculation and list edited fields;
original diagnostics do not certify the edited matrix.

Real instrument/biological truth and closed-source FlowJo equivalence remain
unverified. Rectangular spectral AutoSpill, AutoSpread and acquired QC/ratio
parents have synthetic implementation checks; full FlowJo and instrument parity
remain unverified. Multiple AF reference sources have independent R and known
latent-source numerical checks; automatic identification and extraction of AF
classes, external biological references and broader instrument presets remain
open requirements. The median spectral estimator has a separate definition and
validation boundary.
