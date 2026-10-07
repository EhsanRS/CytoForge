# CytoForge implementation and validation ledger

The requested end state is a full, fast, modern, self-hosted desktop alternative
to FlowJo for advanced users on Windows, macOS, and Linux. A partial implementation
or a passing unit suite does not establish parity. This file records the actual
remaining scope and is updated against working code and verification evidence.

## Source baseline

- FlowJo workspace and core experiment flow: https://flowjo.com/docs/flowjo10/getting-acquainted
- Platforms: https://flowjo.com/docs/flowjo10/experiment-based-platforms
- Workspace XML: https://flowjo.com/docs/flowjo10/workspaces-and-samples/ws-ribbons-and-tabs/ws-ribbon-band-debug/workspace-xml
- FlowJo v11 overview: https://info.flowjo.com/hubfs/FlowJo%20v11/BD-152556_FlowJov11_Overview_Brochure_DIGITAL.pdf

## Scientific and workflow requirements

All entries begin unverified. Mark implemented and verified separately; preserve
remaining differences, algorithm limitations, and untested platforms explicitly.

| Area                 | Required end state                                                                                                                                                                                               | Current evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local storage        | All installation, dependencies, caches, temp files, scientific data and builds in this directory                                                                                                                 | Implemented environment/launchers; uv/npm/browser/GUI/packager files observed here, 3.6 GiB at initial audit; long-run storage audit pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Desktop distribution | Self-contained signed installers, launch/shutdown, offline operation on Windows/Linux/macOS, x64/arm64                                                                                                           | Updated Linux AppImage and unpacked builds produced; all four analysis workers and spectral/AF control outputs passed standalone engine smoke/archive restoration; the actual AppImage ran/plotted PCA, QC, DJF, proliferation, AutoSpill and plates, exported native XLSX/JSON/SVG and recovered saved work plus an unsaved plate draft across engine port changes. Headless tests verify their explicit sandbox exception; a custom AppRun removes the upstream implicit override. Normal sandbox launch fails on this host pending helper setup; Windows/macOS/ARM/signing remain unverified                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Import               | FCS 2/3/3.1, integer/float/double, byte orders, metadata, spillover, malformed/empty files, multiple datasets; CSV and large batch imports                                                                       | Bounded float/double/byte-aligned integer/ASCII and little-endian packed 1–64-bit integer FCS 2/3/3.1 import, plus numeric CSV; chained datasets, exact gain/log/time units, literal/supplemental metadata, duplicate sibling restoration, desktop progress/cancellation, file rollback and history-safe recovery implemented. Four reference arrays match FlowIO exactly; science/API regression and native chooser/import/export/restart/archive checks pass. Source 8M×8 FCS import takes 2.287 s with 6.75 MiB RSS growth. Instrument expansion, packed/histogram formats, original-file retention, resumable transfer and huge downstream streaming remain pending.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Workspace            | Durable projects, groups, keywords, custom metadata, annotations, recovery, undo/redo, portable archive, missing-data relinking, templates and cross-workspace transfer                                          | Atomic SQLite snapshots, persistent undo/redo, revision conflict, metadata, groups and validated archives implemented/tested; archive history, project removal/archival, recovery UI, relinking and reusable templates still pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Transforms           | Linear, log, logicle, biexponential/hyperlog, arcsinh, per-channel editable settings with proven forward/inverse behavior                                                                                        | Five scales and display editor implemented; default GatingML zero/top anchors, monotonicity and inverse tests pass; comprehensive FlowJo transform comparison pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Gating               | Rectangle, range, polygon, ellipse, quadrant/bisector, spider/curly quadrants, boolean, hierarchical gates, editing, gate tree propagation, backgating, magnetic following, automatic gating and gate validation | Shape/boolean/hierarchy engine, direct plot-panel and reviewed visual shape editing in native windows, propagation, backgating and magnetic translations implemented; boundaries/quadrant partition/reference rejection and full-event draft counts tested; freehand tracing and dense-mask indexing implemented; linked bisectors/quadrants, atomic family edits and GatingML relationships implemented; density autogating with excluded rings and full-event counts implemented; native spider arms and curly noise-boundary families implemented; vendor/interchange comparisons and exhaustive UI shapes pending; native unbounded spider sectors with shared center/arm controls, exact finite-parent partition and ten actual-image workflows verified.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Compensation         | Acquisition matrices, manual matrix editor, single-stain control wizard, positive/negative population selection, AutoSpill, spectral unmixing and autofluorescence controls                                      | Acquisition/manual/median control matrices, weighted spectral outputs, background and AF extraction implemented. Conventional AutoSpill adds Huber fitting, tessellation/density cleanup, native-biex iterative refinement, empty-detector AF, all-pair diagnostics, portable reports and raw cleanup populations. Four author-R cases, exact cleanup membership, four native transform configurations and physical-matrix/trimming sensitivity pass; regression source/API/browser and frozen-engine workflows verified. Rectangular spectral AutoSpill, AutoSpread, automatic control identification, QC/ratio parents and real instrument/FlowJo comparisons remain pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Plotting             | Scatter/density/contour/zebra/histogram/CDF/pseudocolor/overlays, transforms, zoom, backgating, 3D and high-dimensional views, full-data counts and transparent downsampling                                     | Native canvas and vector report graph modes implemented; finite CDF, probability coverage, tied bins, full-event smoothing, palettes, resolution, domain/zoom and sampling provenance tested. Interactive full-event XYZ clouds add independent cameras, scalar color/size, volume gates, six-limit/restart persistence and vector reports with event-ID provenance. Source/Linux AppImage 3D and original windows pass. Hardware GPU rendering, planar all-event markers, broader graph/3D parity and large-scale/native platform verification remain pending. See docs/GRAPH_VIEWS.md and docs/THREE_DIMENSIONAL.md.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Native plot windows  | Independent native plot windows, interactive gating, synchronized edits, scoped views and session recovery                                                                                                       | Source and Linux AppImage support axes/mode/options/resolution/zoom, duplicated views, shared gates, stale-draft review, removal/undo recovery, close guards and restart recovery. XYZ views retain their camera, scalar settings and six limits, including across display mode changes; unavailable parameters have explicit replacement controls. Previous/next/direct cohort navigation and parent controls retain views; Shift-click moves matching source windows together, with atomic revision/draft/view guards. Port 8001 selects actual native windows. Windows/macOS/ARM and remaining graph styles are unverified; see docs/PLOT_WINDOWS.md and docs/PLOT_NAVIGATION.md.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Statistics           | Count, parent/total frequencies, location/spread/percentiles, robust CV, geometric stats with validity, ratios and formula columns, consistent batching and exact exports                                        | Counts/frequencies, mean/median/sample SD/variance/CV/MAD/robust CV/geometric mean/SD and arbitrary percentiles implemented; population mapping, safe formulas/controls, huge finite values and exact exports tested against independent references; comprehensive FlowJo conventions remain pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Tables               | Saved definitions, sample/group/population columns, parameter statistics, formulas, pivots, comparison tests, CSV/XLSX export, live updates                                                                      | Configurable live tables, exact hierarchical/explicit population mappings, keywords, controls, stable column and fixed/relative row formulas, hidden helpers, formatting/heatmaps, biology metrics, pivots, sample comparison tests, multiplicity and full-row CSV/XLSX/JSON implemented. Saved FlowJo sample tables retain source gate/compensation bindings, controls, scope, keyword conflicts and conversion reports; science/API and native source desktop checks cover independent values, exports, restart and archives. Full FlowJo binned conventions, advanced table iteration/formulas/styling, cross-project templates and broader statistical models remain pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Layouts/reports      | Editable publication layouts, annotations, overlays, legends, batch reports, SVG/PNG/PDF exports, page sizing and reproducible export                                                                            | Native positioned pages, grouping/alignment/order, shared-axis overlays/locked controls, live data/pivot/comparison table panels with row/column continuation, model/plate figures, live annotations, reviewed sample/panel/keyword mappings, vector SVG/native PDF with attached source manifests, DPI PNG and local draft recovery implemented. Known table cells and full-cohort comparison corrections pass independently; source native desktop exports 16 mixed A4/Letter pages and restores controls across engine ports. CytoForge portable report compositions now provide destination-owned binding review, prototype previews, atomic import/undo and archive/manifest provenance in current source; scientific/API checks pass, with native interaction and installer promotion pending; see [REPORT_TEMPLATES.md](REPORT_TEMPLATES.md). Complete FlowJo layout/template import, additional editing/style controls and Windows/macOS native release checks remain pending                                                                                                                                                                                                                                                                                                                                                                                                          |
| Plate tools          | Experiment maps, annotation CSV, titration, metadata operations, live population measurements and visualizations                                                                                                 | 96/384/1536-well maps, explicit replicate matching, staged CSV/dilution plans, reviewed atomic keyword application, full-event table measurements/formulas/biology metrics, heatmap/split/category/face views, groups, templates and SVG/CSV/JSON implemented. 38 plate scientific/API cases, two interface workflows and source Electron checks pass. The rebuilt frozen engine and actual AppImage verify measurements, native SVG legends, archive restoration and immediate-close unsaved-draft recovery; extreme display bounds preserve raw values. FlowJo XML/WSPT/WSP plate conversion, custom geometry, plots/report integration, titration models and external instrument validation remain open                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Derived parameters   | Safe formulas, channel arithmetic, ratios, time reconstruction and reusable definitions                                                                                                                          | Safe vector arithmetic/ratios/clip/logs and reusable sample definitions implemented; dependencies/cycles, plotting/statistics/FCS/CSV and archive restore tested; time reconstruction and expanded formula tools pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| High dimensional     | PCA, UMAP, t-SNE, FlowSOM, PhenoGraph, clustering, plugin extensions, reproducibility, balanced downsampling and cross-sample mapping                                                                            | Two-dimensional PCA/UMAP/t-SNE and FlowSOM/consensus metaclusters plus fitted-only PhenoGraph communities implemented with pooled multi-sample models, seed/provenance/fitted-ID records, balanced/proportional sampling and exact event mapping; raw analyses retain acquired parent populations after matrix assignment; independent PCA, local UMAP/FlowSOM repeats, cluster partition and browser/native package workflows pass; broader biological/reference validation, Opt-SNE and AF extraction, model extensions, GPU and additional views pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Biological models    | Cell-cycle Watson and DJF fits, proliferation generations/indexes, kinetics response models with diagnostics and constraints                                                                                     | DJF and control-anchored dye-dilution models implemented with independent constrained batch fits, full-event phase/generation probabilities and assigned populations, diagnostics, JSON/CSV/SVG exports and archives/undo. Proliferation supports lognormal/Gaussian dye plus normal background, calibration, precursor frequency and division/proliferation/expansion/replication indices. Latent-label recovery, independent convolution quadrature and published metric definitions verified. Both platforms support rename, stable replacement, reviewed transitive removal and undo, verified through API and browser workflows. Watson's experimental refinement failed an independent recovery check and is unavailable; Watson, biological truth/FlowJo comparisons, tail/bivariate models and deeper table/layout integration remain pending Kinetics now implements calibrated/event-index clocks, explicit reset policies, exact full-event time bins, baseline thresholds, responder statistics, gap-preserving smoothing, manual/suggested ranges, peaks/slopes/area, batch overlays, native exports and stable replacement gates. Tables/plates and portable reports are integrated; 38 new science/API cases and two interface workflows pass. Instrument/FlowJo numerical comparisons, automatic missing-TIMESTEP calibration and specialized layout embedding remain pending. |
| QC                   | Time/event-rate and signal stability, anomaly removal previews, saturation/doublets, acquisition quality reporting                                                                                               | Reviewable robust-bin method, full-data time/rate/marker traces, optional raw saturation and pulse-ratio checks, exact cleanup/rejected populations, draft JSON/event CSV, stale-input protection, archives/review revision and undo/redo implemented; known anomaly masks, independent real-instrument quantiles, coarse/reset timestamps and browser workflows verified; 1M × 16 + Time synthetic benchmark about 5.3s on this host; external QC truth panels, peak/isolation algorithms, validated singlet models and batch heatmaps pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Interoperability     | FlowJo WSP import, GatingML import/export, FCS/CSV population export, compatibility fixtures and unsupported-feature reports                                                                                     | WSP gates and supported saved sample tables plus schema-validated GatingML exchange implemented; independent ISAC/FlowKit masks, safe formulas, explicit unsupported/calculation reports, source XML retention, revision conflicts, archives and native desktop review tested. Eight-color masks agree with FlowKit while stored FlowJo counts differ and are reported. Complete WSP layouts, advanced tables, plugins, biological platforms, uncommon versions/transforms and broader instrument/FlowJo parity remain pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Advanced management  | Concatenation with provenance, panel/channel aliases, group comparisons, batch normalization, population comparison, plugin/API system                                                                           | Physical concatenation implemented with selected populations, explicit channel mappings, three value spaces, matrix snapshots, batch/keyword grouping, progress/cancel, one-step publication/undo and portable exact event origins. Native streamed FCS/CSV export now preserves float64 measurements, correction and exact merged history through reimport; see [EVENT_EXPORTS.md](EVENT_EXPORTS.md). Panel/channel aliases now provide reviewed cohort mapping, unchanged acquired columns/matrix bindings, scientific dependency tracking, native plot windows and checked export/reimport; see [CHANNEL_ALIASES.md](CHANNEL_ALIASES.md). Virtual group views now support native pooled plots, reviewed shared gates, joint discovery inputs, exact pooled event files and saved source-checked reports; see [VIRTUAL_GROUPS.md](VIRTUAL_GROUPS.md). Group comparisons, batch normalization, population comparison and plugins remain pending; see [CONCATENATION.md](CONCATENATION.md).                                                                                                                                                                                                                                                                                                                                                                                                    |
| Performance          | Benchmarks on million-event/high-channel datasets, bounded caches, cancellation, progress, multithreading, large-workspace behavior                                                                              | 256 MiB bounded calculation cache, mmap arrays, native vector operations and full-data aggregation; two-process analysis queue/progress/cancellation/checkpoints. Import event chunks now bounded to 262,144 values; source 1M/8M×8 FCS and 0.5M/2M×3 CSV hashes verify every value with nearly constant RSS. Real panels, larger workspaces, streaming downstream model preparation/export and GPU work remain pending.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Accessibility        | Keyboard workflows, focus handling, screen readers, contrast, responsive multi-monitor layouts, motion preferences                                                                                               | Pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Reliability          | Race/concurrency protection, atomic writes, validation, corruption/permission/disk-full handling, migration, import/export/undo consistency                                                                      | SQLite snapshots/revisions, event/analysis/XML hash checks, persistent undo/redo, XML safety limits and import rollback/cache isolation tested; disk-full/permissions/power-failure injection, migrations and broad large-workspace stress remain pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

## User stories and edge cases to exercise

1. Launch without a network connection, open an existing project and restore its state.
2. Import real instruments' FCS files; inspect channel metadata and acquisition spillover.
3. Reimport duplicates or files with matching names; choose intentionally whether to retain them.
4. Import malformed FCS/CSV; show a useful error, retain good files in a mixed batch.
5. Draw and edit cleanup gates, drill through descendants and backgate selected cells.
6. Change compensation or transforms; recalculate relevant populations and derived results.
7. Propagate trees to groups with matching panels; reject incompatible channels atomically.
8. Combine gates with boolean operations; reject missing references, cycles and wrong-sample parents.
9. Preserve zero-event populations and distinguish undefined statistics from zero.
10. Derive ratios without evaluating arbitrary code; handle zero denominators and nonfinite values.
11. Fit high-dimensional models reproducibly, cancel long jobs and reject stale results.
12. Save and reopen portable projects, undo/redo edits after restart, recover from interrupted writes.
13. Generate live statistics across groups; export exact gated events with traceable metadata.
14. Build print-ready reports with equal axes, editable legends and batch sample substitution.
15. Annotate a plate from a CSV including invalid, missing and duplicate well IDs.
16. Calculate compensation from positive/negative single stains, detect ill-conditioned matrices.
17. Import existing FlowJo workspaces without silently discarding unsupported analyses.
18. Analyze millions of events without UI blocking or data-dependent count approximations.
19. Run two windows without losing edits; enforce revisions and a clear conflict recovery flow.
20. Close during import or analysis; resume/recover cleanly and leave no zombie engine.

## Work log

- 2026-10-02: Empty directory verified. Selected a React/TypeScript desktop UI,
  Electron shell and Python/NumPy scientific engine. Added volume-local cache and
  temp configuration and dependency manifests. Scientific workflow implementation
  and platform validation remain in progress.
- Initial implementation: 43 scientific/API tests and 5 end-to-end browser
  workflows pass. Source Electron shell rendered and analyzed the demo in an
  isolated headless test. Linux engine and unpacked desktop artifacts created.
  A report export race was found by inspecting rendered artifacts despite an
  earlier green browser run; readiness checks and a stricter regression were
  added. Derived expressions and metadata/statistics isolation were added and
  tested. A Slack notification requesting the actual sandbox setup was delivered
  successfully. Normal sandboxed desktop launch remains unverified.
- 2026-10-03: Added process-backed PCA/UMAP/t-SNE/FlowSOM analyses and a complete
  Discovery workflow. All 59 scientific/API tests and 6 browser workflows pass,
  including independent PCA/SVD, seeded local UMAP/FlowSOM repeats, t-SNE event-ID
  subsets, group sampling, cancellation, stale-result rejection, computed
  parameter FCS export and archives retaining fitted IDs. Standalone packaged
  workers ran all four algorithms and restored their archive. A frozen Scanpy
  import required source collection; a targeted packaging hook fixes that
  actual failure. Packaged Electron ran PCA and displayed the resulting density.
  Normal sandboxed interactive launch and other target operating systems remain
  unverified. Full feature parity remains an active objective.
- 2026-10-03: Added the single-stain compensation wizard, raw cleanup/threshold
  selection, median-difference matrices, diagnostic scatter/spectral plots and
  control provenance. Spectral outputs now support autofluorescence extraction,
  electronic background and weighted least squares, and are usable in gates,
  formulas, plots, FCS/CSV and restored archives. Inactive outputs remain undefined.
  All 69 scientific/API tests and 8 browser workflows pass. Independent weighted
  normal equations and known fluorophore/AF mixtures verify the solver; tests also
  cover assigned-compensation-independent control masks, overlap/rank rejection,
  negative coefficients, stale previews, matrix edits, archives and undo.
  Preprocessed FCS detector ranges now match gain/time units. A fixed-port rapid
  restart issue was corrected. Source Electron ran PCA and preserved its selected
  workspace across an engine port change using an asynchronous preload setting.
  The rebuilt standalone engine ran all four analyses, recovered known spectral
  outputs and restored their archive. Updated Linux AppImage/unpacked builds
  passed the packaged desktop PCA and workspace reopening check. Release checksums
  and evidence are in `artifacts/installers/SHA256SUMS` and
  `artifacts/release-validation.json`. AutoSpill and real instrument comparisons
  remain pending; full feature parity remains active.

- 2026-10-03: Gate interchange added with readonly mapping previews, retained
  source XML, per-dimension compensation/transforms, exact ratio/bounded-scale
  behavior and explicit unsupported reports. ISAC reference masks, FlowKit
  eight-color masks, reviewed ellipse plotting/drawing, rollback cache isolation
  and frozen schema/export/archive behavior pass. Some stored FlowJo counts
  differ from both implementations and remain visible in reports. Full WSP
  platform/layout/table conversion and FlowJo parity are still open requirements.
- 2026-10-03: Added robust-bin acquisition QC with full-event rate/signal traces,
  reviewable intervals, raw saturation and pulse-ratio diagnostics, exact
  retained/rejected populations, draft report/identity exports, stale protection,
  cancellation, review revision, archives and undo/redo. Known anomalies and
  independent real-instrument quantiles are verified. A million-event, 16-marker
  plus time synthetic acquisition took about 5.3s on this host, with 100% recall
  and 97.48% precision for inserted disturbances; this is not biological QC
  accuracy. All 152 scientific/API tests and 12 browser workflows pass.
  Version-two project archives store
  hashed QC reports separately and read version-one archives. Frozen workers
  reran all four Discovery methods, spectral controls, 49 GatingML reference
  populations and QC archive restoration. Packaged Linux Electron ran/plotted
  PCA, reviewed/plotted QC populations and preserved its workspace across an
  engine port change under the explicit headless test sandbox exception. Normal
  sandbox launch, Windows/macOS/ARM, signing, external QC truth panels and full
  FlowJo feature parity remain unverified. Evidence and checksums are retained
  in `artifacts/`; the full objective remains active.

- 2026-10-03: Added DJF DNA cell-cycle modeling with a broadened nonnegative
  quadratic, including exactly zero S-phase endpoints, and an optional
  synchronized S wave. Independent per-sample batch fits support fixed/bounded
  means, CVs, linked CVs and peak ratios. Full-event histograms, residuals,
  conditional phase probabilities, assigned populations, diagnostics, JSON/CSV
  and SVG exports, stale protection, archives and undo/redo are implemented.
  Nineteen new scientific/API cases cover independently labeled mixtures,
  adaptive convolution integration, exact constraints, exclusions, large finite
  values, batch sample removal and malformed reports/probabilities. The complete
  source suite passes 171 scientific/API tests and 13 browser workflows. A
  million-event synthetic DNA fit took 0.891s and recovered known fractions to
  within 0.02 percentage points on this host; this is not biological accuracy.
  The rebuilt standalone engine ran DJF, preserved its event identities and
  restored its archive alongside all earlier native checks. Packaged Linux
  Electron reviewed/ran/plotted DJF and reopened its workspace across an engine
  port change under the explicit headless sandbox exception. The updated
  AppImage and checksums are in `artifacts/installers/`. Watson's experimental
  CDF/moment refinement failed independent latent-label recovery and is
  unavailable while that interpretation is investigated. Full Watson,
  biological/FlowJo reference validation, tail/bivariate models, proliferation,
  kinetics, saved-model replacement/management and table/layout integration
  remain open, together with the full cross-platform and feature objective.

- 2026-10-03: Added control-anchored proliferation with lognormal/Gaussian dye
  and normal background convolution, independent constrained batch fits,
  generation probability/assignment outputs and explicit precursor frequency,
  division, proliferation, expansion and replication statistics. JSON, batch
  statistics/event CSV, interactive histograms/residuals and standalone SVG
  support review before saving. Both biological platforms now support rename,
  replacement preserving output names/population IDs, historical reports,
  dependency-reviewed transitive removal and undo. Tests cover latent labels,
  independent adaptive integration, absent generations, source/range exclusions,
  corruption, stable downstream references and concurrent removal-review changes.
  The source suite passes 196 scientific/API cases and 14 browser workflows.
  A million-event synthetic fit took 0.380s without background spread and 8.223s
  with it on this host; these are model recovery/performance measurements,
  not biological accuracy. Frozen-worker validation exposed integer default
  background settings changing their fingerprint after serialization; typed
  defaults and an omitted-settings regression resolve that failure. The rebuilt
  standalone engine calibrated controls, recovered generations and restored
  renamed fit archives alongside all previous native checks. The AppImage itself
  ran PCA, QC, DJF and proliferation, displayed their populations and retained
  both biological models across an engine port change under the explicit headless
  sandbox exception. Checksums and release evidence are updated in `artifacts/`.
  Normal sandbox launch, Windows/macOS/ARM/signing, Watson, external biological
  comparisons, kinetics and the remaining full feature scope stay active.

- 2026-10-03: Added configurable live tables with per-column exact population
  mappings, keywords, control values, stable formula references, hidden helpers,
  formatting and global heatmap ranges. Saved biological metrics follow
  replacement models and preserve assigned-population paths across renames;
  historical references remain selectable, and missing/stale dependencies are
  explicit cell states. Pivots and Welch/Mann–Whitney/paired t/Wilcoxon comparisons
  include pairing, exclusions, confidence intervals and Holm/BH adjustment.
  Full-row CSV/XLSX/JSON exports retain numeric values and source provenance;
  large Unicode values/definitions and status-sheet rollover are tested.
  Twenty-five table cases include independent numerical references, large common
  offsets, original rank/tie preservation, model management, archives and undo.
  The full source suite passes 221 scientific/API tests and 15 browser workflows.
  An eight-column table over four synthetic samples totaling one million events,
  including formulas, pivot and comparisons, took 0.200s initially and about
  0.124s warm on this host, excluding HTTP/rendering and allowing warm OS pages.
  The rebuilt standalone engine validated XLSX and restored tables alongside
  all earlier native scientific checks. The AppImage itself displayed/exported
  biological tables and restored their definitions/models after its engine port
  changed, under the explicit headless sandbox exception and redirected download
  dialog. Native Windows/macOS/ARM, normal sandbox launch, manual spreadsheet
  application checks, complete FlowJo conventions/table import, graph/report
  integration and the full remaining feature scope stay active. Exact semantics
  and bounds are in `docs/TABLES.md`; release evidence is retained in `artifacts/`.

- 2026-10-03: Added conventional AutoSpill with Huber robust regressions,
  tessellation/density scatter cleanup, iterative linear/native-biex correction,
  optional unstained/empty-detector autofluorescence and full review/export/apply
  workflows. Biex refinement matches native lookup, natural cubic interpolation
  and linear extrapolation while existing workspace display transforms retain
  their behavior. Four independent cases generated with pinned author R code
  agree in matrix/residual/iteration results; cleanup masks match event by event.
  Native transform fixtures cover 256/4096 tables, negative decades, width reset
  and extrapolation. Quantized secondary ties are retained with diagnostics;
  plateau results require explicit acknowledgement if tolerance remains unmet.
  Raw parent geometry/Boolean clones keep saved cleanup membership stable under
  matrix assignment. Jobs support cancellation, revisions, scientific staleness,
  reports after restart/archive restore and original provenance after manual edits.
  Thirty-two AutoSpill cases bring the full passing source suite to 253
  scientific/API checks and 16 browser workflows. The rebuilt frozen engine
  matches all four R cases (maximum coefficient difference below 2e-15), verifies
  saved cleanup counts and restores portable matrix reports. Synthetic physical
  truth also demonstrates that default tail conditioning and correlated AF can
  bias coefficients despite low residuals; a separate independent-source fixture
  recovers its known matrix with trimming disabled. Assumptions and bounds are in
  `docs/AUTOSPILL.md`. A synthetic eight-control/eight-detector benchmark used all
  400,000 selected events in 1.62 seconds on this host, including hashing,
  regression, refinement and diagnostics; scatter cleanup and rendering were
  excluded. The rebuilt AppImage reviews automatic cleanup, exports its native
  JSON report, assigns the matrix and reopens that review after its engine port
  changes. Mixed-panel scatter defaults now follow the selected sample, verified
  in the browser and AppImage. Native headless tests retain an explicit sandbox
  exception; normal launch and other OS/architectures remain unverified.
  Closed-source FlowJo/real-instrument validation, rectangular
  spectral AutoSpill, spreading and additional AF components remain open.

- 2026-10-03: Added desktop plate analysis with 96/384/1536-well formats,
  normalized keyword mapping, explicit acquisition replicates, CSV plans,
  decimal dilution rectangles, exact review hashes and atomic annotation writes.
  Empty wells retain plans; missing measurements remain distinct from zeros.
  Live population/keyword/formula/biology columns aggregate acquisition statistics
  with equal weights. Heatmap, split, category and numeric face views share
  full legends and export SVG/CSV/JSON. Templates, selected-well groups,
  resize-loss previews, history, conflicts and archives are covered. Thirty-eight
  plate cases bring the full source suite to 291 passing scientific/API tests;
  18 interface workflows pass, with the two plate workflows rerun after the
  final legend change. Source Electron and the rebuilt AppImage both exported
  plates and recovered an unsaved edit after immediate close and an engine port
  change. The frozen engine verifies all earlier scientific workflows, plates
  and subnormal display bounds. Packaged engine hashes match the validated
  sidecar and frontend files match the latest build. One earlier packaged PCA
  check timed out while queued; a direct check and two complete desktop runs
  passed afterward. Its cause remains unresolved; failure diagnostics are now
  retained. Playwright and upstream AppRun both injected sandbox overrides;
  tests now verify actual flags, and the packaged launcher preserves caller
  arguments. Actual sandboxed startup on this host fails with the missing SUID
  helper configuration; the successful desktop run explicitly records its
  headless exception. The synthetic million-event/384-acquisition/eight-column
  plate benchmark took 0.655s initially and 0.437s warm; its limitations are in
  `docs/PLATES.md`. FlowJo template/platform conversion, custom geometry,
  per-well plots/report integration, titration models, instrument comparisons,
  other OS releases and the full objective remain active. Release evidence and
  checksums are retained in `artifacts/`.

- 2026-10-03: Added the desktop kinetics platform with explicit acquisition clocks,
  calibration and alignment, event-index estimates, reviewed reset policies,
  full-event time-bin statistics, strict responder thresholds, baseline references
  outside cropped displays and smoothing that preserves missing segments. Manual
  and reviewable suggested intervals expose exact event counts, peaks, mean,
  least-squares slope, clipped piecewise-linear area and integration coverage.
  Frozen time/signal/source outputs create ordinary gates with preserved original
  identities. Native overlays, exports, live table/plate metrics, stable refits,
  historical reports, reviewed removal and portable restoration are implemented.
  A removal review now includes affected plate measurements and prevents silent
  fallback to a historical model after live model removal; undo restores them.
  Thirty-eight new numerical/workflow cases bring the science/API suite to 329;
  20 interface workflows pass, with kinetics rerun after the final presentation
  and missing-source changes. Source Electron, the frozen engine and actual
  AppImage verify analytic curves, strict gate counts, native JSON/SVG/CSV and
  restoration across engine port changes. Native tests retain the explicit
  headless sandbox exception; production sandbox startup on this host still needs
  the privileged helper setup. One concurrent AppImage startup check closed early;
  the sequential native rerun passed. Shared extraction is suspected, not established,
  and startup checks are now sequential. The million-event/256-bin Gaussian benchmark
  took 0.144s for one acquisition and 0.341s across 16; excluded work and scientific
  limits are documented in `docs/KINETICS.md`. Instrument/FlowJo validation,
  automatic missing-TIMESTEP calibration, specialized layout embedding,
  other OS releases and the entire original scope remain active.

- Desktop delivery correction: the default start/development entry points open
  Electron. The progress server on `0.0.0.0:8001` now launches the actual packaged
  AppImage and streams its desktop window. Mouse, keyboard, clipboard text, file
  import, native CSV retrieval and cross-site session links pass against the
  bundled scientific engine. Browser development is an explicit separate command;
  it is stopped on this server. The headless preview uses an explicit sandbox
  exception; ordinary sandboxed Linux startup here and Windows/macOS releases
  remain pending. Evidence: `artifacts/desktop-preview-validation.json`.

- 2026-10-03: Verified native publication reports with positioned physical pages,
  shared-axis overlays and locked controls, saved table/model/plate figures,
  live statistics, reviewed acquisition/population bindings and native PDF output
  with an attached scientific manifest. The actual AppImage exported four mixed
  A4/Letter pages; independent source checks recovered population counts 2 and 7
  and mean 1.5. Native dragging/undo, a 2480 × 3508 PNG with 300-DPI metadata,
  rejected invalid IPC descriptors and unsaved draft recovery across engine ports
  pass. The full science/API run passed 353 cases before the final report
  refinements; all 27 focused report cases pass afterward, with overlapping
  coverage. All 20 interface workflows pass. Source Electron, the frozen engine,
  packaged desktop workflows and engine/frontend package integrity pass. The
  viewer on `0.0.0.0:8001` passes mouse, keyboard, file import and CSV/PDF retrieval
  against this AppImage, including the PDF's embedded source manifest. A synthetic
  million-event density page with 128 bins rendered in 1.143s; position changes
  reused its figures in 0.039–0.043s while rechecking source hashes. Those timings
  are from this host and include independent count assertions; input generation,
  file writes, HTTP, desktop drawing and PDF/PNG output are excluded. Production
  sandbox startup still fails pending the host's privileged helper configuration.
  Automatic table continuation, dedicated comparison/pivot report panels,
  template rebinding, complete FlowJo layout import and Windows/macOS release
  validation remain active. Current evidence: `artifacts/release-validation.json`,
  `artifacts/desktop-report-appimage-smoke.json` and `docs/REPORTS.md`.

- 2026-10-03: Verified automatic native table-report continuation, including
  horizontal measure pages with repeated row context, raw data, saved pivots and
  corrected comparisons. Full-cohort snapshots, finite pivot counts, missing/test
  status and the saved comparison's complete adjustment family remain intact
  across page windows. Explicit prototype selection handles preceding table
  continuations. Native source and the actual AppImage exported 16 mixed A4/Letter
  pages, checked all 24 raw and 24 pivot cells, recovered comparison n=4 per group,
  mean difference −4 and Holm-adjusted p=0.009318429887987869, and restored the new
  controls across engine port changes. Independent inspection of the printed PDF
  checks all acquisition labels, repeated headings, counts and physical sizes.
  The science/API regression passed 370 cases before a final p-value presentation
  fix; all 67 focused table/report cases passed afterward, including a positive
  p-value that previously rounded to zero. These counts overlap. All 20 interface
  workflows and the complete source/AppImage desktop workflows pass. Frozen
  engine, frontend/bundle integrity and launcher checks pass. The synthetic
  64-acquisition/1,048,576-event report benchmark prepares all 12 pages in 0.420s
  initially and 0.209–0.221s after rearrangement, with one table calculation,
  per-page source hashes and 192 independent cell assertions. Its exclusions and
  one-host scope are documented in `docs/REPORTS.md`. Candidates are now built and
  checked in a separate artifact directory before replacing the live viewer's
  installer. Production sandbox startup on this host still requires privileged
  helper setup. Native Windows/macOS releases, complete FlowJo layout import,
  cross-workspace templates, individual cell/column geometry and additional
  report editing/style controls remain active. Evidence:
  `artifacts/desktop-table-report-appimage-smoke.json`,
  `artifacts/table-report-pdf-validation.json` and `artifacts/release-validation.json`.

- 2026-10-04: Verified bounded desktop acquisition import with independent FCS
  2.0/3.0/3.1 decoding, mixed byte-aligned integers, float/double and legacy ASCII,
  chained datasets, supplemental metadata and gain/log/Time preprocessing.
  Prepared arrays stream directly to the immutable store with incremental hashes;
  malformed chains roll back by file, cancellation rolls back the complete batch,
  and recovery preserves arrays referenced by undo/redo snapshots. The desktop
  provides live file/dataset/event progress, per-file results and intentional
  reimport. All 432 science/API tests and 20 interface workflows pass. Actual
  source Electron and the rebuilt AppImage imported three independent panels,
  handled mixed errors and duplicates, cancelled a three-million-row event pass,
  exported independently checked FCS events and restored portable identities
  across engine ports. A zero-event ASCII offset failure reproduced in the first
  candidate AppImage is corrected and passes in the final AppImage. Existing
  native analysis workflows and the independently inspected 16-page PDF also
  pass. The source-import benchmark decoded, hashed, wrote and synced eight
  million events by eight parameters in 2.287s with 6.75 MiB peak-RSS growth;
  every stored value matches an independently generated hash. This measures one
  Linux host and excludes transfer, workspace commit, plots, startup and the
  verification reread. The validated installer now runs behind the live desktop
  viewer on 0.0.0.0:8001. Production sandbox startup still needs the host's
  privileged helper setup. Instrument compatibility, packed/histogram formats,
  streaming downstream analysis/export, native Windows/macOS releases and the
  full original feature objective remain active. Evidence and limits:
  `docs/IMPORTS.md`, `artifacts/import-benchmark.json`,
  `artifacts/desktop-import-appimage-smoke.json` and
  `artifacts/release-validation.json`.

- 2026-10-04: Saved FlowJo sample tables now convert through the desktop importer.
  Both retained eight-color workspaces produce their three tables and 39 columns;
  supported count/frequency values are checked against independent FlowKit masks,
  while missing population paths remain undefined. Source gate IDs, raw/fixed
  compensation, spectral aliases, group/sample scope and first-source controls
  are retained. Keyword conflicts preserve target annotations and acquisition
  metadata. Restricted formulas support stable columns and bounded fixed/relative
  row references, with hidden inputs and explicit unsupported diagnostics.
  FlowJo's binned intensity conventions differ from native full-event values and
  require acknowledgement; complete table/workspace parity remains pending.
  All 468 science/API checks and 20 interface workflows pass. Actual source
  Electron and the rebuilt AppImage imported the owned two-acquisition fixture,
  exported JSON/XLSX, reopened across engine ports and restored a portable project.
  Independent OOXML checks verified 28 cell positions in both export formats,
  missing edge references, hidden-helper omission and byte-exact retained XML.
  XML is synced before the workspace transaction commits. The packaged acquisition,
  core desktop and 16-page native report workflows also pass, with independent
  FCS/PDF checks and engine/interface package integrity. The refreshed viewer on
  `0.0.0.0:8001` passes controls/transfers/access checks in a fresh synthetic
  workspace, preserves the existing project's data and restores its selection.
  Its public AppImage download hashes to the installed, validated package.
  The first independent XLSX checker omitted inline strings; the corrected
  checker passes both source and packaged exports. Production sandbox startup on
  this host still fails for the missing privileged helper; native Windows/macOS/ARM,
  signing and the complete original FlowJo scope remain active. Evidence is in
  `docs/WSP_TABLES.md`, `artifacts/desktop-wsp-table-appimage-smoke.json`,
  `artifacts/desktop-wsp-table-appimage-smoke-export-validation.json` and
  `artifacts/release-validation.json`.

- 2026-10-04: Independent native plot windows now open from sample/population
  double-clicks or the current plot's duplicate action. Each keeps its workspace,
  axes, preferred Y axis in histogram mode, display mode, zoom, coordinate basis
  and backgate; saved gates and transforms synchronize through verified workspace
  revisions. Missing sources retain their identifiers and display unavailable
  state until explicitly replaced or restored. Stale gate drafts require review;
  native close guards protect unsaved gates in the main and popup windows.
  The Linux AppImage passes ten native scenarios, including an independently
  counted popup gate, foreign-workspace and main-only capability rejection, PNG
  export, undo/redo, source deletion, port-independent session recovery and
  engine exit. All 471 science/API checks and 20 interface workflows pass. The
  standard pytest entry now resolves the shared WSP fixtures as well.
  Existing native analysis/import/WSP-table workflows and the independently
  inspected 16-page PDF pass against the final package. Package integrity checks
  the engine, all frontend files and all native shell assets, including the new
  window controller and preload. Port 8001's authenticated viewer selects actual
  native windows, routes input to the selected window and waits for its pixels
  before accepting input. Its popup interactions and complete installer download
  pass; existing workspace contents and main selection are preserved by the test.
  Graph parity, native Windows/macOS/ARM and signing remain open. Normal startup
  on this host still needs its privileged sandbox helper; headless tests use
  their explicit override. Evidence is in `artifacts/release-validation.json`,
  `artifacts/desktop-plot-windows-appimage.json` and
  `artifacts/desktop-preview-validation.json`; behavior and limits are in
  `docs/PLOT_WINDOWS.md`.

## Next implementation priorities (full objective still active)

The latest verified increments add advanced tables, conventional AutoSpill,
desktop plate analysis, kinetics, native publication reports and bounded
acquisition import, followed by saved WSP sample tables, native plot windows and
scientific graph displays and interactive XYZ clouds. The biological
reference/platform scope and all remaining requirements in the original
objective remain active.

1. Expand FlowJo WSP platform/table/layout import and uncommon versions/transforms,
   reference instrument compatibility and exact FlowJo population parity.
2. Rectangular spectral AutoSpill, automated control selection, spreading diagnostics and
   real instrument compensation/spectral reference comparisons.
3. Cell cycle, proliferation and kinetics models with independent reference
   validation; extend QC with external anomaly truth panels and advanced methods.
4. Expand tables/layouts/plate tools, instrument fixtures, streaming analysis/export and
   platform build automation; complete native release gates on every requested OS.
5. Extend Discovery with model/cluster visualizations and diagnostics, reference
   biological panels, more dimensions, stored-model reuse and scalable/GPU jobs.

- 2026-10-04: Native main/popup exploration and vector reports now include CDF,
  probability contour, zebra and pseudocolor views, graph settings and a repair
  for histogram/CDF zoom persistence. CDF counts include ties and events outside
  the viewport in the finite denominator; probability coverage exposes tied bins
  and missing domain mass. Zoom inside the automatic domain preserves density
  estimates. Population gates still use complete arrays. Full/robust extents,
  smoothing, probability spacing, palettes, bins and marker limits persist in
  duplicated/reopened native plots and saved report definitions. Exact graph
  conventions and sampling/drawing limits are recorded in docs/GRAPH_VIEWS.md.
  All 500 scientific/API and 20 interface workflows pass. Twelve source and
  packaged-native window scenarios pass; actual four-page graph PDFs contain
  vectors and attached source/count provenance, independently checked with
  PyMuPDF. The current AppImage also passes core native, saved WSP table and
  16-page continued report checks plus source/bundle/engine integrity. On a
  synthetic million-event input, 160/384-bin source graph payload calculations
  take 0.05–0.42 seconds here, excluding rendering/serialization. The port 8001
  viewer exercises native CDF/zebra, palette controls and window selection,
  preserves existing projects, and serves the exact current 344,122,087-byte
  AppImage (e9487bf7150618bd09ce7c35508770063ccb4b01317ff5f48b0f2daa7e99f180).
  Three-dimensional views, automatic/magnetic gates, broader graph controls,
  all-event/GPU markers and Windows/macOS/ARM validation remain in the full
  active objective. Normal sandbox startup on this host remains unverified
  pending the existing root-owned helper setup; the explicit headless preview
  exception is recorded rather than added to the production launcher.

- 2026-10-04: Interactive XYZ clouds now run in real main and independent native
  plot windows. Rotation, pan, camera zoom, parameter color/size, cube/labels,
  uncompensated coordinates and explicit full-event/sampled display persist in
  duplicated views and after restart. Revision/geometry-bound binary chunks
  preserve original uint64 event IDs and do not truncate events at the marker
  limit when full display is selected. Volume gates evaluate all original events;
  literal XYZ truth contains exactly eight events. Missing XYZ/scalar references
  remain explicit and have replacement controls; returning from another graph
  mode retains the 3D view. Vector reports use shared XYZ/scalar transforms and
  scales, retained cameras, camera-viewport counts and original-ID hashes.
  Portable report/project round trips pass. All 521 science/API checks and 20
  interface workflows pass. Nine 3D and twelve original graph-window scenarios
  pass in source/actual AppImage checks; native five-page graph PDFs contain only
  vectors with independently verified manifests. The rebuilt frozen engine,
  core AppImage workflow, saved WSP tables, continued 16-page report, launcher
  and source/bundle integrity checks pass. A million-event synthetic 3D dataset
  takes 0.1453s for source preparation, 0.0021s with cached indices and 0.1300s
  for bounded encoding plus literal ID checks here; these timings exclude network,
  canvas and PDF rendering. The native desktop draws all 65,553 events across two
  chunks, and the explicit sampled option retains full population counts.
  This host uses the software renderer even with default GPU settings; hardware
  WebGL2 performance/shader rendering, very large clouds, additional
  3D gate surfaces/legends, saved backgate report layers,
  richer planar rendering and Windows/macOS/ARM verification remain in the full
  active objective. See docs/THREE_DIMENSIONAL.md. Normal sandbox setup remains
  unresolved; production has no automatic exception.

  Port 8001 now verifies native XYZ/camera input, preserves the existing revision-14
  workspace and all 12 acquisitions, and serves the exact 344,872,100-byte final
  AppImage (ea1296dd1c3ffb97392b4e419cebc8f1e328277d21ac00b613d143f28ed71e24).
  Wheel zoom is verified without scrolling the surrounding desktop document.

- 2026-10-04: Source and the actual Linux AppImage now support independent and
  coordinated plot sample navigation. Previous/next arrows and direct selection
  respect group/search cohorts and uniquely matched population ancestry; Shift
  coordinates windows with the same source. Parent navigation uses the defining
  gate coordinates. X/Y and active XYZ/color/size transforms, zoom and cameras
  survive moves, duplication and restart. Missing/ambiguous populations, changed
  parameter meanings and compensation bases, unavailable groups, stale revisions,
  dirty editors and concurrent view/membership changes have explicit guards.
  Navigation leaves scientific snapshots and undo history unchanged. The native
  evidence checks ten literal original event IDs and every XYZ position.
  Twenty-seven focused cases, eight new source/AppImage scenarios, the existing
  twelve native window scenarios and nine XYZ scenarios pass. The complete source
  suite passes 548 cases in 272.90s; all 20 interface workflows pass in 2.7 minutes.
  Current frozen-engine, native model/core, WSP tables, five-mode vector reports,
  16-page continued reports, launcher and source/bundle integrity checks pass.
  The public viewer verifies independent and Shift-coordinated 3D navigation,
  file/PDF/CSV transfer and access controls while retaining the original experiment
  at revision 14. The full 344,896,627-byte public download matches SHA-256
  f7553e38ebd39d4ed4b53b14c092e4fd8724b0c9c107218a173762672c20d1ab.
  A synthetic metadata-only 2,500-sample/7,500-population/33-window benchmark takes
  0.2312s on its first plan and a 0.0242s median over seven plans; separately,
  snapshot loading takes 0.3052s. No event buffers, network or drawing are measured.
  Hardware GPU, Windows/macOS/ARM, normal Linux sandbox setup, complete population
  view history/iteration, magnetic gates and the remaining full FlowJo scope remain
  active. See docs/PLOT_NAVIGATION.md.

- 2026-10-04: Native population breadcrumbs, ordered child/sibling navigation and
  per-window remembered population views now restore axes, graph settings, zoom
  and 3D cameras. Histories are bounded to 96 views per popup and 192 in the main
  desktop; duplication copies history independently. Main views resume across
  workspace switching, reload and restart. Partial polygon/drag and 3D bounds
  drafts protect native close and navigation, retain geometry through deleted
  sources or revisions, and block stale completion. A native drawing-layout
  shift and an asynchronous panel-navigation race were found and corrected.
  All 563 science/API tests and 21 browser workflows pass; source and actual Linux
  AppImage checks cover 11 population-history, 8 coordinated-navigation,
  12 popup-window and 9 3D workflows. Literal volume counts and six original event
  identities agree with the CSV. Native core workflows, WSP table exports,
  five-mode vector PDF and 16-page continued table PDF also pass. A metadata-only
  2,500-sample/7,500-population benchmark restores a view from 192 remembered
  populations in a 0.0187s median, excluding snapshot I/O, event data and rendering.
  Normal Linux sandbox setup, hardware GPU, Windows/macOS/ARM validation,
  magnetic/tinted gates and the remaining full FlowJo objective remain active.
  Investigate the sparse unsmoothed contour display in
  artifacts/screenshots/desktop-population-breadcrumbs.png: its 25-event fixture
  shows gate outlines but no visible probability contour. This observation needs
  independent reproduction and a scientific rendering check.

- 2026-10-04: Sparse unsmoothed contours no longer disappear at tied peak bins.
  The 25-event reproduction previously had path widths below 6e-17; it now traces
  the exact included bin cells, including holes and separate corner contacts.
  Smoothed peak regions also retain bin area; other smoothed levels keep their
  interpolated paths. Scientific thresholds, coverage, outliers and gate masks
  remain unchanged. The coverage panel and vector export provenance identify
  the geometry method. Literal oriented edges verify every 3×3 occupancy pattern
  and rectangular fields; 16/32/160/384-bin sparse cases, single-event peaks and
  a 150,000-vertex drawing limit retain complete probability/count audits.
  All 576 scientific/API tests, 42 focused graph cases and 21 interface workflows
  pass. Source and actual AppImage native pixels plus an independently inspected
  four-page vector PDF show the 25 boundaries, an eight-cell hole and a smoothed
  peak. The current Linux candidate also passes native view history, coordinated
  navigation, 12 plot-window and nine 3D checks, core analysis/restart, saved WSP
  tables, a five-mode vector graph PDF and continued 16-page table PDF. Integrity
  checks match the frozen engine, source native shell and frontend build.
  A million-event unsmoothed contour calculation takes 0.2166s/0.4507s at 160/384
  bins on this host, excluding loading, rendering and IPC; serialization is
  measured separately. A 32,768-component checkerboard takes 2.431s and reports
  its drawing limit while preserving all counts. Optimize this fragmented
  geometry path as further performance work. One AppImage restart failed during
  an overlapping sandbox probe; the sequential repeat passes, original logs are
  retained, and the probe now uses its own workspace temporary directory.
  Evidence: artifacts/contour-release-proofs.json and
  artifacts/benchmark-contour-boundaries.json. Normal Linux sandbox setup,
  hardware GPU, Windows/macOS/ARM validation and every remaining requirement in
  the full FlowJo objective remain active.

- 2026-10-04: Fragmented exact-bin contour calculation now uses combined
  coordinates and vector operations over batches of at most 4,096 complete
  rings. Independent literal cell edges and signed area verify isolated and
  mixed fields across multiple batches. Seven synthetic cases compared over
  five interleaved repetitions retain identical complete payload hashes,
  including every scalar, path order and the same drawing-limit prefix.
  The 32,768-region/256-bin median improves from 2.3475s to 0.1572s; the
  73,728-region/384-bin case improves from 2.3639s to 0.1850s. These source
  timings exclude loading, serialization, IPC, rendering and PDF. Separate
  384-bin processes report approximately 161.9/152.7 MiB peak RSS before/after;
  Linux memory accounting is approximate and this is no general memory bound.
  All 578 science/API tests, 44 focused graph cases and 21 interface workflows
  pass. Actual AppImage checks also pass for 12 native plot-window behaviors,
  11 population-history behaviors, eight navigation and nine 3D behaviors,
  core analysis/restart, saved WSP tables, a five-mode vector graph PDF and a
  continued 16-page table PDF. Seven contour-specific source/AppImage checks
  verify native pixels, exact rare-sample badges/full-count tooltips, complete
  32,768-event audits at the declared drawing limit and unchanged scientific
  history; an independent PDF parser verifies the four-page contour export.
  The native AppImage stress window is ready in 2.10s and its full response
  arrives/parses in 1.74s inside the renderer. Returning that large payload
  through the test driver adds substantial overhead; these observations do
  not establish an end-to-end speedup. Profile native serialization and
  rendering as further performance work. Package integrity matches all 14
  frontend files, seven native-shell files and the validated frozen engine.
  Evidence: artifacts/contour-performance-release-proofs.json and
  artifacts/contour-performance-comparison.json; the exact baseline source is
  retained in artifacts/contour-performance-baseline-graph-views.py. Normal
  sandboxed Linux launch still fails SUID-helper setup on this host; hardware
  GPU, Windows/macOS/ARM, complete graph/gate styling and every remaining
  requirement in the full FlowJo objective remain active.

- 2026-10-04: Native plot transport no longer makes a second recursive copy of
  every scientific payload value before JSON encoding. The shared, already
  JSON-compatible payload is encoded strictly once inside its worker; complete
  scientific fields, float text, Unicode, nulls, sampling/geometry limits and
  local request/header guards remain intact. No dependency was added. New
  source checks compare full previous/current response bytes across all eight
  graph modes, mixed finite/nonfinite, empty, constant and extreme acquisitions,
  Unicode overlays, backgates and 3D tuple settings. Large contour/zebra cases
  retain 32,768 events, all bins/probability audits and the 150,000-vertex limit;
  plotting leaves scientific snapshots and history unchanged. All 613 science/API
  tests, 35 transport cases and 21 interface workflows pass.
  Sixteen source ASGI scenarios with separate stores/caches and five interleaved
  warmed repetitions retain identical complete raw response hashes. The
  384-bin fragmented request median falls from 1.4670s to 0.5310s; 100,000
  scatter markers from 200,000 events fall from 0.5103s to 0.1918s. Million-event
  contours take 0.3767s/0.5657s at 160/384 bins, compared with 0.5393s/1.0127s.
  These include complete calculation, middleware, encoding and TestClient
  response consumption; sockets, native rendering and test-driver transfer are
  excluded. Tiny requests show variable benefit; the small CDF case shows none.
  The actual Linux AppImage stress response arrives/parses in 0.466s inside the
  renderer and its window is ready in 1.073s, compared with the prior single
  native observations of 1.745s/2.097s. These native observations are separate
  headless runs on this host, not an interleaved native performance benchmark.
  Seven native contour checks and the independently inspected four-page vector
  export pass, alongside 12 popup, 11 history, eight navigation, nine 3D checks,
  core analysis/restart, saved WSP tables, five-mode vector graph PDF and continued
  16-page table PDF. Package integrity matches the validated frozen engine and
  unchanged frontend/native-shell sources. Evidence:
  artifacts/plot-transport-release-proofs.json and
  artifacts/plot-transport-comparison.json; the exact previous application source
  is retained in artifacts/plot-transport-baseline-app.py. Normal Linux sandbox
  setup, hardware rendering, real-instrument/very-large-workspace performance,
  Windows/macOS/ARM and every remaining full FlowJo requirement remain active.

- 2026-10-04: Added versioned magnetic population following with preserved gate
  anchors, bounded nearest-population search, full-parent exact counts, native
  preview/arrows/fit/freezing, independent copied acquisitions, parent/matrix
  recalculation, undo/restart and stale-draft review. Supported geometric shapes
  retain transforms, compensation references, ratios and every polygon vertex.
  CSV/FCS membership, GatingML static snapshots, portable restoration and vector
  report provenance are verified; planar magnetic boxes resolve in XYZ views.
  The current source suite passes 668 science/API cases and 21 interface flows;
  native installer evidence is recorded separately. FlowJo's undisclosed
  optimizer, WSP magnetic-anchor conversion, real acquisitions, exhaustive
  scientific edge cases, bisectors/direct dragging/automatic gates, normal Linux
  sandbox startup, Windows/macOS/ARM and the complete FlowJo objective remain
  unverified. See MAGNETIC_GATES.md and release evidence for scope.

- 2026-10-05: Added reviewed visual gate editing in the main native desktop
  window and independent plot windows. Pointer and keyboard controls move,
  resize and rotate supported one/two-dimensional shapes, retain all polygon
  vertices, imported covariance, open boundaries and magnetic anchors, and
  preview exact full-event counts without workspace/history writes. Save
  recalculates children, synchronizes windows and supports undo/redo. Stale
  revisions require explicit review; Escape and viewport changes cancel an
  active drag. Ordered axes preserve distinct compensation references even when
  both use the same measured parameter. Fifteen independent source cases and
  fourteen native workflows cover these behaviors; the full scientific/API
  suite passes 683 cases, and all 21 interface workflows pass. Five repeated
  million-event ASGI previews per shape
  retain identical response bytes and exactly 100,003 labelled target events;
  host medians are 55/176/185/239 ms for range/rectangle/ellipse/polygon.
  Direct exploration-view editing, higher-dimensional surfaces, general
  nonsymmetric quadratics, bisectors/freehand/automatic gating, broader real
  acquisitions and extreme coordinates, normal sandboxed Linux startup,
  Windows/macOS/ARM and complete FlowJo parity remain part of the full objective.
  See GATE_EDITING.md and separate installer evidence for verified scope.

- 2026-10-05: Repaired ordered native coordinate resolution across ordinary
  desktop plots, histograms/CDFs, 3D streams, vector reports, sample navigation
  and new rectangle/volume gates. Repeated measured parameters retain distinct
  compensation, transform and ratio definitions; overlays use separate compatible
  axes. Navigation checks every definition and compares fixed matrix coefficients,
  detector/output order, kind, background and weights, fixing an invalid legacy
  matrix-property lookup. Ambiguous scalar/rearranged requests report their missing
  choice instead of silently substituting a definition. Acquisition labels remain
  separate from ratio definitions. Thirteen independent cases pass within the
  696-case scientific/API suite; installer evidence is recorded separately.
  Richer arbitrary coordinate/scalar reference selection, direct exploration-view
  editing, broader real acquisitions and all remaining full FlowJo, platform,
  sandbox and performance requirements remain active. See PLOT_COORDINATES.md.

- 2026-10-05: Added direct gate shape editing in the primary plot panel of the
  main native desktop and independent plot windows. A toolbar action or double-click
  on a visible saved gate opens its full parent in its exact native coordinate basis.
  Hit testing prioritizes gate borders over overlapping filled children. Apply/Cancel
  retain the original plot view; numeric-settings handoff retains geometry and its
  base revision. Drawing source snapshots and native dirty-state protection retain
  drafts when other windows remove populations or samples. Saving requires restoring
  the source and explicit revision review. Nine isolated native workflows verify
  independently labelled membership, dependent counts, synchronized undo/redo,
  canceled gestures, close guards, handoff, missing-source recovery and restart.
  The 40 scientific backend sources are unchanged from the recorded 696-case suite.
  Higher-dimensional surfaces, bisectors, automatic gates, broader acquisitions and
  all remaining full feature, platform, sandbox and performance requirements remain
  active. See GATE_EDITING.md and PLOT_WINDOWS.md.

- 2026-10-05: Added native freehand drawing with fixed-interval captured vertices,
  start-ring/click/Enter and drag/release closure, full-parent review, exact imported
  coordinate definitions, cancellation, native close/navigation guards, retained
  stale/resized traces and explicit 2,000-vertex limit rejection. Repaired the legacy
  signed-area rejection of valid self-crossing outlines. A million-event check
  exposed roughly 30-second masks at 2,000 vertices; conservative edge-interval
  indexing now applies the original scientific predicate only where relevant.
  Independent populations and boundary/dtype/shape regressions cover this path.
  Ten isolated native workflows check main/popup interaction and restart; full
  source and actual-installer verification are recorded separately. See FREEHAND.md.
  Repeated-scribble performance, bisectors, spider/curly quadrants, automatic gating
  and all remaining full feature, instrument, physical-device, platform and sandbox
  requirements remain active.

- 2026-10-05: Added linked native histogram/CDF bisectors and quadrant families,
  full-parent counts for every member, real threshold/corner handles, atomic
  geometry and parent updates, independent popup synchronization, numeric handoff,
  stale-draft guards, family delete/undo and target-independent propagation.
  Standard half-open GatingML geometry retains family relationships in custom
  information. Sixteen new independent science/API cases and thirteen isolated
  native workflows cover these paths; the full source suite passes 721 cases and
  21 interface workflows. Other imported shared-divider strategies, spider/curly
  quadrants, automatic gates and every remaining full feature, instrument, device,
  platform, sandbox and performance requirement remain active. See PARTITIONS.md.

- 2026-10-05: Added native main/popup automatic density gating with seed-selected
  full-parent contours, adjustable coverage/smoothing/resolution/domain, exact
  polygon counts and explicit excluded rings. Hole geometry remains editable and
  survives standard GatingML Boolean strategies, native history and restart. Twenty
  source/API cases and nine isolated native checks cover the implemented behavior;
  the source suite passes 741 cases and 21 interface workflows. Numerical vendor
  parity, real-acquisition validation, exhaustive automatic controls and all retained
  full feature, instrument, platform, device, sandbox and performance requirements
  remain active. See AUTOGATING.md.

- 2026-10-05: Added native spider populations with shared center/arm controls,
  unbounded scientific sectors, complete finite-parent coverage, exact boundary
  ownership and cancellation handling, linked previews, popup synchronization,
  numeric handoff and restart recovery. Twenty science/API cases and ten native
  workflows cover the implementation, including ordered fixed compensation/ratio
  coordinates, portable projects and exact population event exports. Spider WSP
  import, GatingML interoperability, vendor/real-acquisition numerical comparison,
  3D boundary surfaces, curly quadrants and all retained platform, instrument,
  sandbox, physical-input and performance requirements remain active. See
  SPIDER_GATES.md.

- 2026-10-05: Added native curly quadrants with a shared transformed center,
  reviewed raw-unit coefficients and an explicit positive-intensity square-root
  convention. Independent labels and precise boundary comparisons, isolated
  previews, atomic family edits, popup synchronization, propagation/delete/undo
  and normal restart are covered by source and native checks. Curves and reports
  preserve crossing adjacency while counts use unbounded scientific predicates.
  Faithful WSP/GatingML interchange, instrument/FMO calibration, vendor numerical
  comparison and all retained platform, sandbox, device, feature and performance
  requirements remain active. See CURLY_GATES.md.

- 2026-10-05: Table exports now wait while a workspace mutation is in progress,
  preventing requests against the previous revision during a save. The interface
  regression holds a table save open and checks that CSV, XLSX and JSON remain
  unavailable until it settles, then verifies the resulting export contents.

- 2026-10-05: Native plot windows expose independent raw, fixed-compensation and
  ratio definitions for X/Y/Z and 3D color/size. New gates and automatic outlines
  retain their definitions; reports record input and matrix provenance. Literal
  event tests and actual native dialogs cover independent views, full-event
  counts, undo/redo, reports, IPC validation and restart. See AXIS_DEFINITIONS.md.
  All retained vendor, platform, instrument, sandbox, feature and performance
  requirements remain active.

- 2026-10-05: Virtual group populations reuse original events and per-sample corrections.
  Native windows retain independent pooled state, reviewed group gating and removals,
  shared discovery inputs, event export and report dependencies. Independent values,
  population paths, revision guards, undo, restart, exact origins and a million-event
  CPU check are covered. Source verification passed 941 science/API cases and 31
  interface workflows; the Linux x64 package passed 27 headless validation reports.
  All retained vendor, platform, instrument, sandbox and full feature requirements
  remain active.

## Population comparison source increment — 2026-10-06

Saved population comparisons now integrate with actual gate-mapped table columns,
CSV/XLSX/formulas, publication layouts and explicitly reviewed refit history.
Comparison SVG exports retain native presentation settings. Individual-control CDFs
use linear accumulation. Native comparison titles follow cosmetic renames. Reviewed
removal enumerates refit branches and dependent figures/cells, with one-step undo.
See [POPULATION_COMPARISON.md](POPULATION_COMPARISON.md).

This source increment passed 92 focused tests, 680 non-network regression tests,
type checking and the UI build. An explicitly synthetic publication example was
rendered and inspected. The original progress workspace still matches its recorded
release hash, and the released AppImage is unchanged. The current environment denies
local network sockets: 362 API-fixture tests, native regression, the port-8001 progress
viewer and new installer validation remain pending. The full objective and all 20
previously retained unverified requirements remain active. Large joint-comparison
memory use and broader real-instrument/vendor references also require further work.

## Joint comparison memory and HTTP source validation — 2026-10-06

Joint comparisons now share temporary transformed columns by original acquisition,
including overlapping gates. Probability partitioning reads one coordinate at a time
and retains all selected jointly finite events. Raw mappings and completed coordinate
vectors are released promptly; a bounded cache reuses baseline partitions when several
control gates exclude the same acquisition. Real worker cancellation/shutdown checks
verify cleanup after open mappings close.

The million-event, 64-coordinate full calculation reduced peak RSS from 735 MiB to
392 MiB with exact joint results. Its dense-versus-column joint-storage comparison
measured 1,626 MiB and 392 MiB; temporary staging adds time and remains a measured
tradeoff. Full 16/64-coordinate timing, original byte hashes and cleanup evidence are
in `artifacts/benchmark-population-comparison.json`. These measurements exclude native
rendering, IPC, vendor equivalence and performance on other operating systems.

An opt-in HTTPX ASGI test client runs the real routes, middleware, lifespan and workers
on each caller's event loop, preserving concurrent import progress/cancellation. It
enabled the previously deferred HTTP workflows without a listening socket. All 1,064
Python cases pass. A minimal-coordinate request regression exposed mismatched typed
numeric defaults across worker serialization; comparison settings now normalize those
defaults before hashing. Saved-table fixtures persist gates before calculating, matching
the actual application workflow.

The isolated Linux engine candidate also passes actual frozen comparison workers at
2 and 64 coordinates with bundled models, NumPy/SciPy and metadata, source/Python
import paths removed, exact dense-reference scores, known ENS and original-byte checks.
Evidence is in `artifacts/frozen-population-comparison-validation.json`. The current
released AppImage and original revision-14, 12-sample progress workspace are unchanged.
Source API contracts and frozen child jobs now have evidence; listening-server startup,
the native renderer, installer regression/promotion and port-8001 progress validation
remain pending because this managed session denies local TCP sockets. The full objective
and every previously retained vendor, instrument, platform, sandbox and feature gap
remain active. Larger-cohort resource use and real filesystem/quota behavior still need
checks on supported platforms.

## Comparison storage failures — 2026-10-06

Joint coordinate staging now uses checked ordinary writes and durable flushes before
opening read-only mappings. It checks available space with 256 MiB headroom, reserves
physical blocks where supported and accounts for combined unwritten acquisitions when
allocation falls back to ordinary writes. Failures do not publish incomplete results.

After verified worker exit, cancellation, shutdown and detected crashes remove owned
joint scratch and unpublished `.npz.partial` result files. Published `.npz` artifacts
remain available for scientific records and undo. Cleanup validates job/store ownership,
reports failures and still attempts independent owned temporary paths. Startup alone
does not prove an older worker has exited.

All 43 focused storage cases pass, including injected disk/quota failures, unsupported
allocation, short writes, flush failures, actual compressed writes interrupted by
cancel/shutdown/crash, retained original data and artifacts, unrelated-job preservation
and rejected paths outside the owning store. The full Python suite passes 1,086 cases:
363 HTTP and 723 other cases. Evidence is in
`artifacts/pytest-comparison-storage-failures.xml` and
`artifacts/pytest-population-comparison-disk-safety-full-asgi.xml`.

The rebuilt isolated Linux engine passes actual frozen workers for the known ENS
reference, 64-coordinate dense equivalence and two disk/quota failure cases. Original
events and workspaces remain intact and no failure publishes a result. Bundled module
paths exclude the project's Python environment. The source scheduler driver, native
parent/listener, desktop renderer and released installer have separate validation scope.

Fresh million-event runs at 16/64 coordinates retain every joint count, statistic and
partition from the previous storage implementation. Full calculation peaks are
232/392 MiB; single-run times are 23.97/91.96 seconds. Original synthetic acquisition
bytes and workspaces remain unchanged. Evidence is in
`artifacts/population-comparison-disk-safety-benchmark-equivalence.json` and
`artifacts/population-comparison-source-validation.json`.

Real full-filesystem/quota conditions, Windows/macOS cleanup, larger cohorts and every
previously retained full-goal requirement remain active.

2026-10-06: Report tables now support explicit column widths, heading/default/
individual row heights, padding and per-cell fonts, colours and alignment. Actual
physical geometry drives both continuation axes; repeated labels and every
selected numerical result remain intact. Cell positions and formatting survive
portable-template remapping and undo/redo, while stale export reviews are rejected.
Thirty-five independent source/API cases and all 1,170 scientific/API tests pass.
Actual frozen workers verify styled table cells and geometry through bundled
modules. A bounded cache reuses pagination after position-only changes. Native
editor/PDF/draft checks are prepared but unexecuted because listening sockets are
denied here. Release promotion, full table/publication parity and every retained
full-goal requirement remain pending; see [REPORTS.md](REPORTS.md).

2026-10-06: Layout Studio now aligns rotated bounds, distributes centers and
equal gaps on either axis, and moves groups together while respecting member
position locks and prototype-page boundaries. Annotations, captions and table
cells expose font family, weight, italic, underline and line spacing, with table
continuation and source-review hashes following the resulting geometry. Portable
compositions and history preserve the styles. Nineteen independent geometry
checks, 91 focused report/template checks and all 1,135 scientific/API tests pass.
Actual frozen children render styled captions, annotations and table counts
through bundled modules. Native interaction and release promotion remain pending;
see [REPORTS.md](REPORTS.md). Complete FlowJo publication parity and every
previously retained full-goal requirement remain active.

2026-10-06: Plate analysis now includes 6/12/24/48-well formats and custom
rectangular dimensions through mapping, annotations, dilution plans, templates,
SVG/CSV/JSON, workspace history, archives and report figures. Reviewed resizing
lists each excluded position even when total well counts match. Individual well
acquisitions open separate native plot windows with compatible parameters and
independent full-population sources. All 1,205 scientific/API tests, 123 focused
plate/report checks and 13 actual compiled desktop helper checks pass. Actual
frozen workers verify custom plate report geometry and source values. Original
user data remains unchanged. Native controls/window/recovery QA is prepared but
unexecuted because listening sockets are denied here. Irregular plate geometry,
FlowJo instrument/template conversion, titration/kinetics models, platform release
gates and every retained full-goal requirement remain active; see [PLATES.md](PLATES.md).

2026-10-06: Main/native plots and publication figures now share validated font
roles for axis labels/numbers, gate names, statistics, legends and titles.
Bundled typefaces, physical point sizes, weights, italics and colors persist in
plot sessions and report definitions/templates. Font edits avoid scientific
requests and preserve 3D buffers. Measured title descenders fit the SVG frame,
and the editor accepts normal keyboard entry and fractional sizes. All 1,246
scientific/API tests, 41 focused font checks and 23 compiled helper/native IPC
checks pass. Frozen workers render histogram/3D glyphs and round-trip graph
templates; package integrity matches the complete checked engine, shell and UI.
A separate 65,536-event grid retains exact counts and buffers. The original
user workspace remains unchanged. Actual native controls and every retained
full-goal/platform requirement remain pending; see [GRAPH_VIEWS.md](GRAPH_VIEWS.md).

2026-10-06: Gate appearance now supports border width, labels and closed 2D fills
in main plots, native popup state and vector publication figures. Polygon fills
subtract overlapping/nested excluded rings using independent vector clips.
3D boxes retain border/name settings, and selected fonts survive resizing. All
1,286 scientific/API tests, 81 focused checks and 27 compiled gate/IPC checks
pass. The isolated Linux AppImage matches its validated engine, shell and UI;
native GUI and all retained platform/full-feature requirements remain pending.

2026-10-06: Saved report layers now retain backgates and create across/down
ancestry pages using each gate's own axes, ratios, compensation and transforms.
Sample iteration, locked controls, pooled source closure, magnetic provenance,
biological dependency removal, portable templates, projects and history preserve
the highlighted population. Missing or ambiguous backgates require review rather
than disappearing. Planar highlights now sample inside the viewport and expose
complete/visible/displayed counts; univariate plots show a rug and 3D reports
retain flagged event identities. All 1,319 scientific/API tests, 33 focused
checks and 21 compiled interface checks pass, alongside 27 gate/IPC and 23 font
regressions. Actual frozen workers verify backgate reports/template round trips.
A 1,048,576-event grid retains the known 131,072 highlighted events, unchanged
base payloads and complete streamed identities. Source tests and frozen workers
do not establish native GUI, GPU or cross-platform release readiness; those
checks and every previously retained full-goal requirement stay active. See
[BACKGATING.md](BACKGATING.md).

- 2026-10-06: Added bounded little-endian packed FCS integer decoding with mixed
  1–64-bit fields, continuous event/parameter boundaries and a one-byte chunk
  carry. Every original DATA byte is consumed once, retaining scientific hash
  verification. Range masks precede exact float64 precision checks; gain/log/time
  preprocessing and immutable event identities are retained. Ninety new cases
  cover all field widths and bit offsets, literal bytes, unaligned uint64 values,
  corrupt/chained rollback, cancellation, gating, double export and portable
  restoration. The focused import suite passes 151 checks. Eight actual frozen
  child cases verify the packaged decoder, rejection cleanup and population
  identities after SQLite restart; four existing frozen report/comparison cases
  also pass. A source benchmark independently verifies every output value at
  131,075, 1,048,579 and 4,194,307 events. The largest decode/hash/write/fsync
  took 2.687s with 17.67 MiB additional peak RSS on this Linux host; transfer,
  commit, Electron, plotting and other platforms are excluded. All 1,409 source regression tests pass. The separate Linux AppImage and its complete engine/interface/desktop contents are verified; duplicate build copies can be recovered from that retained installer. The native file chooser/popup check
  is prepared but not executed because socket creation still returns EPERM.
  The original workspace and all 12 event files are unchanged. All earlier
  AppImages are retained; only byte-verified duplicate build copies were removed
  to make room. External packed instrument truth, big-endian packed layout, FCS
  3.2 mixed types, histogram modes, resumable import, downstream streaming and
  the full desktop/cross-platform/FlowJo feature objective remain open. Evidence:
  `artifacts/fcs-packed-benchmark.json`,
  `artifacts/frozen-fcs-packed-engine-validation.json`,
  `artifacts/frozen-fcs-packed-graph-regression.json`, and `docs/IMPORTS.md`.

- 2026-10-07: Added captured populations bound to immutable acquired event
  identities. The desktop inspector can capture a population before editing
  gates or assigning compensation. Captures survive source edits/deletion,
  project export/import and undo, and can supply median spectral/AF controls
  from PhenoGraph communities. Integrity checks reject changed acquisition or
  membership files, including cached child/Boolean selections. All 1,561 source
  tests pass. Five packaged-engine cases verify captures, actual analysis workers
  and reuse of three captured controls. Native GUI review, cross-platform builds,
  distinct AF spectrum discovery, complete Opt-SNE, biological reference truth
  and the retained full FlowJo feature objective remain pending. See
  [POPULATION_SNAPSHOTS.md](POPULATION_SNAPSHOTS.md).
