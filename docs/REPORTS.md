# Desktop publication reports

Layout Studio is part of the Electron desktop application. Its scientific pages
are rendered by the bundled local engine. The desktop File menu exports the
reviewed report through a native save dialog. The resulting PDF contains vector
figures and an attached `cytoforge-report-manifest.json`.

## Editing and sources

Add the current population plot, a saved table, a cell-cycle/proliferation/kinetics
fit, a saved plate, live text, or a shape. Pages use physical millimetres and can
mix A4, Letter, landscape and custom dimensions. Move and resize objects directly,
edit their coordinates or rotation, duplicate/delete/order them, and select
multiple objects for grouping, alignment and ordering. Align left/center/right
or top/middle/bottom against rotated bounds. Distribute object centers or equal
gaps horizontally or vertically; each group moves as one unit. Operations affect
the current prototype page. A position lock on any group member protects the
whole group from dragging and arrangement. Local undo/redo applies to the composition;
saved layouts participate in workspace history and portable projects.

Annotations, figure captions and table cells support sans-serif, serif or
monospace text, regular/semibold/bold/extra-bold weights, italic, underline and
line spacing from 1 to 3. Captions also follow the object's text alignment.
Table headings retain their emphasis, and source-error cells retain their error
colour. Font family participates in text wrapping; line spacing participates in
caption height and table row continuation. Changing spacing invalidates the
reviewed page plan. These styles persist in saved layouts, portable compositions,
history and export manifests. Scientific graph axes and legends retain their
separate figure typography.

The publication controls have independent geometry tests and known-value report
tests for typography, overflow, table continuation, cache invalidation, template
import and history. The frozen engine probe also renders the styled captions,
annotations and tables through bundled modules with exact synthetic event counts.
Native font controls, PDF attachment and draft restoration have a prepared check:

```bash
source tools/env.sh
CYTOFORGE_REPORT_PUBLICATION_TEST=1 \
CYTOFORGE_TEST_NO_SANDBOX=1 \
node tools/desktop_report_smoke.mjs
```

This explicitly requests the headless server sandbox exception and selects the
isolated `publication-controls` AppImage. Native interaction remains unverified
until the check actually runs. Its evidence, screenshots and runtime are separate
from previous desktop report checks and the original progress workspace.

The isolated Linux desktop candidate is
`artifacts/candidates/publication-controls/desktop/CytoForge-0.1.0.AppImage`.
Its complete bundled engine, interface assets and desktop code pass
[package integrity checks](../artifacts/publication-controls-desktop-package-integrity.json).
The current source and package checkpoint is
[report-publication-desktop-candidate-validation.json](../artifacts/report-publication-desktop-candidate-validation.json).
For the native progress viewer on `0.0.0.0:8001`, use the named candidate command
in the [README](../README.md).

Unsaved desktop drafts are written atomically under the runtime's
`.config/report-drafts`. Closing the app waits for draft persistence. Drafts
restore against a different local engine port; a changed workspace still requires
a current source review. Existing grid compositions migrate to positioned pages.

Plots support density, scatter, contour, zebra, pseudocolor and one-parameter
histograms/CDFs with graph settings and probability provenance, up to fifteen
overlays, individual colours and legend labels, fixed controls, shared transforms,
explicit axis bounds and population outlines. Scatter keeps the exploration
engine's deterministic 12,000-point display limit. Density and histogram counts
use all eligible events. Legends distinguish the population count, while the
manifest also records finite, visible and outside-view counts. Gating coordinates,
ancestors, Boolean operands, formulas, model artifacts and compensation matrices
are retained in the scientific dependency closure.

Text accepts `{{workspace}}`, `{{sample}}`, `{{iteration}}`,
`{{keyword:Donor}}`, `{{stat:count}}`, `{{stat:median:PE-A}}` and other supported
statistics. Text objects have an acquisition and population context. Zero remains
zero; undefined values are labelled `Undefined`. Missing metadata is visible.
Saved tables retain their own measurement definitions and source/status provenance;
choose their live rows, saved pivot, or saved comparison results. Automatic
continuation exports all remaining selected rows and measure columns, repeating
sample/population labels, pivot dimensions or the comparison measure on every
column page. Start-row and start-column settings intentionally omit preceding
results; row/column limits are per output page. Whole-plate figures stay fixed in
acquisition batches.

Table objects added in the desktop editor default to automatic continuation and
retain the saved table's full cohort across acquisition batches. Enable source
iteration explicitly to narrow that cohort to each mapped acquisition/panel.
Pivot cells include finite contributing counts; comparison panels retain group
labels, eligibility/exclusion counts, method, adjustment, confidence level and
full-precision results in the manifest. Selecting fewer displayed comparison
measures preserves the saved comparison's complete multiple-testing family.
Undefined tests keep their reasons and never become zero p-values.
Positive p-values below the displayed decimal precision use scientific notation.
A test-library p-value of zero is labelled below numerical resolution, with a
status notice; the original numeric result remains in the manifest.

Use **Output preview** to inspect generated continuation pages. Pages repeat the
prototype's other objects as context. Multiple continuing tables advance through
their own row/column windows; a finished table leaves its frame empty on later
continuations. Batch tiles may finish on different continuation pages. Headings
and cells wrap; abbreviated text has an explicit ellipsis and a warning. If a
cell has no room for text, a separate warning identifies that limitation. Full
values and statuses remain in SVG titles and the source manifest. Manual excerpts
identify additional undisplayed rows or columns.

Select a table and open **Column and cell layout** to set column widths, heading
height, default or individual row heights, horizontal/vertical padding and
alignment in physical millimetres. Unspecified column widths share the remaining
frame space. Fixed label columns repeat on horizontal continuation pages; each
logical row keeps its height on every column page. A row or column that cannot
fit produces a review error rather than silently dropping its values.

Individual heading and data cells can override alignment, font family, physical
point size, weight, italic, underline, line spacing, text colour and background.
Formatting positions follow the saved table's displayed row and column order,
including pivot/comparison results. Explicit cell point sizes use actual points;
inherited table text retains the existing object-size scaling. Automatic row
heights accommodate larger cell fonts across all column pages. Explicit heights
take precedence and may abbreviate text. Formatting outside the current result
is reported during review. Source errors always retain their visible error colour.

These presentation settings preserve the underlying numerical values and source
snapshot. They participate in review hashes, continuation planning, saved reports,
undo/redo and portable templates. Changing the selected table view clears its
positional formatting. Settings allow up to 4,096 cell overrides, 516 column
positions and 50,000 row positions. Merged cells and full FlowJo table styling
parity remain pending.

## Batch review and normalization

Batch modes iterate acquisitions, ordered panels, or keyword/tag groups. A keyword
discriminator selects distinct acquisitions within an overlay group. Ambiguous
acquisitions require explicit mappings. Populations match their complete ancestry
path exactly; missing or duplicate paths require an explicit population mapping.
There is no automatic substitution of All events. Locked controls retain their
original acquisition and population. Review hashes cover the workspace revision,
definition and resolved bindings; editing those invalidates acceptance.

Histogram normalization is a display operation:

| Mode               | Definition                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Count              | Full-event bin counts                                                                                      |
| Percent population | Bin count / original population count × 100; includes nonfinite and outside-view events in the denominator |
| Unit area          | Bin count / visible event total / width in displayed coordinates                                           |
| Relative peak      | Bin count / largest visible bin count                                                                      |

Undefined normalizations remain undefined. Extreme finite domains are binned in
scaled coordinates to avoid numeric overflow while conserving visible event mass.

## Export and verification

The same vector page renderer supplies the editor, SVG files, zipped SVG batches,
PNG pages and native PDF. PNG exports at 150/300/600 DPI include actual `pHYs`
resolution metadata. Native PDF preserves each page's physical dimensions. Source
manifests record definitions, bindings, counts, raw/model SHA-256 hashes, page
geometry, issues and the rendered SVG hash. Native export rechecks the prepared
DOM, scientific content and artifact bytes before and after the save dialog.

XYZ point clouds retain their orthographic camera and shared coordinate/scalar
scales, draw vector markers, and attach original event-ID provenance; see
[3D desktop plots](THREE_DIMENSIONAL.md).

The default policy blocks missing sources and stale models. Users can explicitly
include labelled historical snapshots or visible missing-source placeholders.
These policies preserve errors in the page and manifest. Biological figures
validate their original event-aligned artifacts; live plots and statistics also
check their dependency freshness. Model-removal review includes report objects.

Panel caching is bounded to 64 MiB, invalidates on scientific definitions and file
changes, and reuses figures when their position changes. Source verification still
checks file bytes on export, including data already held in engine caches.
Full table evaluations have a separate 64-MiB cache measured by serialized result
size. Row/column windows, figure positions and display fields reuse that cohort
calculation; scientific definitions and artifact changes invalidate it. Page plans
include table snapshot hashes and pagination geometry in their review hash.
Physical table plans have a separate 8-MiB cache. Position-only changes reuse a
plan; cell formatting, frame dimensions and source-result changes invalidate it.

Current limits: 32 prototype pages, 256 objects, 16 plot layers, 1,024 selected
batch acquisitions, 1,024 pages per export, 128 MiB of SVG/PDF output, 16 MiB of
embedded PDF manifest, and 64 million PNG pixels. Table evaluation retains its
50,000-row/two-million-cell bounds; pivots allow up to 512 result measure columns.
Portable cross-workspace compositions now have explicit source binding review,
prototype previews, one-step import/undo and source provenance; see
[report templates](REPORT_TEMPLATES.md). This is implemented in current source;
native interaction and installer promotion remain pending.
Additional table geometry and merging, font and scientific graph typography
controls, full FlowJo layout import and larger streamed exports remain part of
the active scope. This work does not establish
complete FlowJo publication parity or Windows/macOS release validation.

The independent report tests cover known boundaries/counts, locked controls,
ambiguous/missing populations, metadata escaping, normalization, cached geometry,
finite numeric extremes, stale models, corrupted artifacts and dependency removal.
`tools/desktop_report_smoke.mjs` exercises native dragging, a reviewed four-page
PDF with A4/Letter sizes, attached source truth, a 300-DPI PNG, IPC validation and
draft recovery across engine ports. Evidence is written under `artifacts`.
`tools/desktop_table_report_smoke.mjs` adds native editor controls and a 16-page
mixed A4/Letter PDF with every selected raw/pivot cell, full-cohort comparisons,
correction-family retention and recovery of the new pagination controls.
Its optional `CYTOFORGE_REPORT_TABLE_GEOMETRY_TEST=1` mode selects the isolated
table-geometry AppImage and additionally checks native width/height/font controls,
actual cell SVG, PDF geometry and saved draft recovery. This new native mode is
prepared but has not run because this execution environment denies listening
sockets. Source tests cover 35 geometry/formatting cases, including real ASGI
exports, stale review rejection and destination-workspace template values. Actual
frozen workers separately verify bundled table geometry and styled cell values.

A synthetic geometry-only benchmark plans 50,000 rows, 32 measures and 4,096 cell
overrides into 666 continuation frames in 0.395s initially and 0.115s from cache,
with a 1.91-MB traced allocation peak. Tracing was enabled during timing. This
measures pagination only, excluding scientific evaluation, SVG, HTTP and desktop
rendering. Evidence: `artifacts/benchmark-report-table-geometry.json`.

The synthetic million-event, 128-bin density benchmark rendered its first vector
page in 1.143s and reused figures after position changes in 0.039–0.043s on this
Linux host. Each run rechecked source hashes and independently asserted the
498,201-event population and visible bin totals. Timings include those assertions
and exclude input generation, file writes, HTTP, desktop drawing and PDF/PNG
export. They do not establish performance on other operating systems or biological
accuracy. Evidence: `artifacts/report-benchmark.json`.

A separate synthetic 64-acquisition cohort with 1,048,576 events produced 12
continued vector table pages in 0.420s initially and 0.209–0.221s after position
changes on this host. Each preparation checks all 192 selected cells and hashes
the scientific sources on every page. The cohort is evaluated once across those
preparations. Timings exclude generation/file writes, HTTP, desktop drawing and
PDF/PNG output. Evidence: `artifacts/table-report-benchmark.json`.

Workflow references: [FlowJo batch overlays](https://docs.flowjo.com/flowjo/graphical-reports/le-iteration/le-batchoverlay/),
[page sizing](https://docs.flowjo.com/flowjo/graphical-reports/export-output-and-printing/le-pagesizing/),
and [publication output](https://docs.flowjo.com/flowjo/graphical-reports/export-output-and-printing/le-publication/).
The workflow scope also follows [tables in layouts](https://docs.flowjo.com/flowjo/graphical-reports/equations-statistics-and-tables/le-tableinlayouts/)
and [multiple-page layout controls](https://docs.flowjo.com/flowjo/graphical-reports/le-controls/).

[Graph views](GRAPH_VIEWS.md) defines CDF denominators, probability contours,
smoothed density, shared settings and native/vector output verification.
