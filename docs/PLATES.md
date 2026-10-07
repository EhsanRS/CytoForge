# Plate analysis

The desktop **Plates** view stores experiment maps, annotation plans, live
measurements and display settings in the workspace. Numbered formats include
6 (2 × 3), 12 (3 × 4), 24 (4 × 6), 48 (6 × 8), 96 (8 × 12), 384 (16 × 24)
and 1536 (32 × 48). **Custom plate dimensions** sets a rectangular grid with
1–96 rows and columns, bounded to 1,536 positions. Rows after Z use Excel-style
letters, ending at AF in the 1536-well format and CR in a 96-row custom grid.
Well IDs normalize to `A01`: `a1`, `A_01`, `A:001`
and surrounding whitespace are accepted. Numeric-only wells are ambiguous and
are rejected.

## Map acquisitions

Open **Import & titration**, choose a sample group and preview well/plate keyword
mapping. The default WELL ID / PLATE ID lookup ignores punctuation and case;
`$WELLID`, `Well ID` and `WELL_ID` match. Applied sample annotations take precedence
over immutable acquisition metadata in automatic mode. Contradictory normalized
keys in a source are reported instead of choosing one. A source can also be
selected explicitly.

Unmatched/invalid wells and wells outside an explicitly selected format are
reported. Automatic inference retains the smallest matching 96/384/1536 format;
smaller numbered formats and custom dimensions can be chosen explicitly.
Custom discovery uses the selected row/column geometry, including addresses beyond
the standard 1536-well envelope. Duplicate acquisitions for one well are omitted unless **Include
multiple acquisitions in the same well** is selected. The preview requires an
acknowledgement of unresolved items before saving discovered plates or using a
mapping. It can save several discovered plates in one transaction, or replace
the assignments in the current draft. Existing annotation plans outside a new
mapped format are reported before staging that replacement.

Custom resizing follows the same review as changing a numbered format. The review
lists every outside position and the acquisition assignments and annotation keys
that the new grid excludes, even if the old and new grids have equal well counts.
Cancelling preserves the draft. Staging changes only that draft; saving commits
the plate through workspace history. Acquisition data and previously applied
sample annotations remain available. Templates, CSV/JSON/SVG exports, report
figures and portable projects preserve the actual grid and valid well positions.

Select a well to inspect its assigned acquisitions. Each acquisition retains its
main-analysis link and has a **New plot window** button in the desktop app. Native
windows start with that acquisition's complete population and compatible channels:
one-channel data opens a histogram; two or more channels open a density plot.
Replicates open independently with their own counts and display settings. Existing
window gating, navigation, synchronization and recovery apply to these windows.

Each acquisition occupies one well per plate. A well can carry up to 64
acquisitions, and a plate up to 4096 acquisitions. The same acquisition may be
referenced by different plate definitions. Manual assignment previews acquisition
moves from their previous well. Removing an acquisition from a workspace removes
its plate assignments while retaining planned annotations for those wells.
Workspace history and project archives preserve the definitions.

## Select and annotate

Click one well, Ctrl/Command click to toggle wells, or Shift click to select a
rectangle from the selection anchor. Row/column headers select a band. Arrow keys
move the active well; Shift extends the rectangle. Home/End select the row ends,
and Control + Home/End select plate ends. Select all, acquired wells, inverse or
none with the selection controls. Empty wells can be selected and annotated.

The inspector separates three sources:

- **Staged plan**: planned well annotations, including explicit keyword removal.
- **Applied annotations**: current editable sample keywords used in measurements.
- **Acquisition metadata**: original read-only keywords retained with the sample.

**Stage annotation**, CSV import and dilution staging modify the draft plan.
**Save plate** persists that plan; it does not write the plan into samples.
**Review annotation changes** lists the exact sample/key/before/after changes,
unchanged values, affected acquisitions and empty planned wells. **Apply reviewed
annotations** commits the plate and the displayed sample keyword changes together.
Other keywords and acquisition metadata are preserved. Empty wells keep their
plans until an acquisition is assigned; applying again requires another preview.
Choose replacement or fill missing/empty keys only. WELL ID and PLATE ID can be
included explicitly as sample annotations.

The review hash binds the draft, selected wells, mode, identity setting and
changes. Applying checks that hash and the preview's workspace revision. Saving
also checks the draft's base revision. Unrelated workspace edits can be rebased
when the saved plate is unchanged; a conflicting saved plate keeps the local draft
and requires export/reload before it can be saved. A group created from selected
wells contains the unique assigned acquisition IDs and saves the plate atomically.

Draft undo/redo keeps twenty local states. Workspace undo/redo covers committed
plate definitions and annotation changes. Unsaved drafts survive switching views
and browser refreshes when the session cache has capacity. The desktop additionally
stores one bounded draft per workspace in `.config/plate-drafts` under its runtime
directory. Its isolated preload exposes validated IDs and bounded draft content,
without general filesystem access. Successful saves remove the draft. Normal
desktop close flushes the current edit and awaits the write before destroying the
window; a failed write offers keeping the application open. This is verified with
an immediate close after an edit and reopening on a different engine port. A hard
process kill can lose edits since the last 150 ms autosave. Native draft writes use
an atomic rename and a flushed file; their maximum encoded size is sixteen MiB.

## CSV plans and dilution series

CSV import supports UTF-8 BOM, quoted commas and newlines. Select the well column
and optionally a plate column. For a file with multiple plates, set the current
plate identifier and select its plate column; rows for other identifiers are
skipped and counted. A multi-plate file without a destination identifier is
rejected. Blank cells skip annotation changes by default, or explicitly plan
keyword removal when that option is enabled. An empty text value staged manually
is distinct from keyword removal.

Malformed/empty CSV, duplicate headings, NUL, absent required columns and oversized
input fail explicitly. Invalid/outside/empty well addresses and rows of the wrong
width are reported. Every row for a duplicate normalized well is omitted; none is
chosen silently. Valid rows can be staged after acknowledging the report. A CSV
supports four MiB, 20,000 records and at most 64 annotation columns. A staged well
supports 64 keys, 160-character keys and 2048-character values. Staged annotations
in a plate have a four-MiB UTF-8 bound. CSV provenance includes its SHA-256,
filename, settings and omitted-row report.

Dilution/titration specifies a starting well, steps, replicate count, row/column
direction, initial value, multiplicative factor or additive increment, and an
optional unit keyword. The entire rectangle is previewed before staging. Decimal
arithmetic avoids artifacts such as `0.30000000000000004` for a 0.1 increment.
Negative, overflowing, underflowing and outside-plate series are rejected. Units
are stored separately from numeric concentration values.

Resizing previews every outside well and the number of acquisition assignments
and planned annotation keys it removes. Staging a resize changes only the plate
definition; raw acquisitions and applied annotations remain available.

## Live measurements and display

Up to ten columns reuse the full-event table evaluator: population counts,
parent/total frequencies, location/spread/percentiles, raw or compensated
parameters, editable or acquired keywords, safe formulas, control acquisition
values and saved biological-model statistics. Population paths match exact
hierarchies; explicit acquisition/population overrides can resolve ambiguities.
Copying a saved table retains its entire column set and stable formula bindings,
including helper columns, and is available for tables with one to ten columns.
Plate views display those helpers too. Formula aggregate functions use the
assigned acquisition rows, before per-well aggregation. See [TABLES.md](TABLES.md)
for formulas, population resolution, biological staleness and statistical limits.

Well values are the median, mean or sum of **acquisition statistics with equal
weights**. They are not statistics of pooled events. For example, acquisition
medians 2.5 (four events) and 150 (two events) give a well median of 76.25, whereas
pooling their events would give 3.5. The median of their event counts is 3; select
sum to obtain a total count of 6. In strict mode every acquisition must have a
defined measurement. Available mode aggregates the defined acquisitions and
reports the missing count. Missing channels/populations/keywords/models and
undefined formulas remain missing; zero remains a measured value. Text values
that differ across acquisitions show `<mixed>` with a status reason.

Heatmaps use the actual finite well range by default. Manual finite increasing
bounds clip display colors only; values and exported statistics remain exact and
clipped wells have a tooltip. Constant ranges use the midpoint color. Legends
use scientific notation for very small or large legend bounds. Subnormal finite
bounds clip out-of-range colors without changing the underlying measurements.
Split wells show two measurements with independent ranges. Keyword colors use either applied
annotations or immutable acquisition metadata; colors are deterministic by label
and can be overridden. The default twelve-color palette can repeat for different
labels, so use the legend and overrides when labels need distinct colors.

Face views map up to ten numeric measurements, in their column order, to head
width/height, eye size/separation, eyebrow slope, nose length, mouth curve/width,
ear size and head color. The legend states every feature/measure/domain. A yellow
dot marks partial missingness and all-missing wells have a cross. This is a
CytoForge mapping, not a claim of identical FlowJo face geometry. The interface
and exported SVG use the same numeric-only face drawing helper.

## Exports and compatibility

Export the current draft as staged annotation CSV, a reusable JSON template,
full live JSON report, full-well measurement CSV or standalone SVG figure.
Measurement JSON retains acquisition-level values, statuses, ranges, clipping,
mapping and provenance. CSV contains raw aggregated values and status columns;
formula-like text and headings are neutralized for spreadsheets. Annotation CSV
includes mapped and planned wells. If a staged key collides with Well ID or Plate
ID, its structural mapping header receives a numbered mapping suffix, which can
be selected when importing that CSV. CSV empty cells cannot retain the distinction
between explicit removal and empty text; use a JSON template for exact plans.

Templates carry plans, columns and display settings, omit acquisition assignments
and receive a new plate ID when imported. Sample/control/model-specific bindings
are reported for review; they are not silently mapped onto other samples.
Plate SVG has row/column labels, exact evaluated colors, per-well tooltips,
measurement/face/category legends, aggregation and workspace revision. Very long
legend labels are abbreviated visually and retained in their tooltip. The
desktop download opens the native file-save flow. Annotation/template exports
remain available when an event-dependent evaluation fails, so a draft can be
recovered. Project archives preserve complete saved plates; they do not include
unsaved desktop draft caches or local undo history. The archive manifest's existing
size bound also applies to large collections of plates.

FlowJo XML/WSPT plate-template conversion, WSP plate import, irregular plate
geometry, further report-layout integration and titration/kinetics modeling remain
open requirements. Custom rectangular grids and individual native acquisition
windows are implemented in current source; packaged native interaction remains
unverified. This increment does not establish complete
FlowJo plate parity or real-instrument/instrument-specific mapping compatibility.

## Evidence and primary references

`tests/test_plate_geometry.py` adds known-grid and event-count checks for all seven
numbered formats and custom dimensions, same-count resizing, strict validation,
rows beyond Z, template/history/project recovery, annotation review hashes and
destination-owned report figures. `tools/test_plate_geometry.sh` compiles and
checks the actual desktop geometry and native window-state helpers.
`tools/desktop_custom_plates_smoke.mjs` is prepared to check the isolated
`custom-plates` AppImage's controls, separate native replicate windows, resize
review/cancellation and draft recovery:

```bash
source tools/env.sh
CYTOFORGE_TEST_NO_SANDBOX=1 node tools/desktop_custom_plates_smoke.mjs
```

This explicitly requests the headless server sandbox exception. The new native
check has not run because this execution environment denies listening sockets;
its prepared source and syntax checks do not prove native interaction.

`tests/test_plates.py` checks normalized dimensions, independent event truth,
unequal replicates, missing/partial values, formula bindings, extreme finite
arithmetic, category source/colors, quoted CSV, duplicate rows, explicit clearing,
decimal dilution, hashing/revisions, batch rollback, groups, deletion/history,
templates, safe exports and archives. `tests/browser/plates.spec.ts` covers the
interactive workflow, 1536-well selection, formats, group creation and conflict
recovery. Frozen-engine and actual Electron/AppImage checks live in
`tools/engine_smoke.py` and `tools/desktop_plate_smoke.mjs`.

The synthetic benchmark in `tools/benchmark_plates.py` uses 384 acquisitions with
one million events and sixteen acquired parameters, eight plate measurements,
population paths and independently checked medians/counts. On this host the
384-well evaluation took 0.655 seconds initially and a 0.437-second warm median;
the 1536-well display definition took 0.619 seconds initially and 0.495 seconds
warm for the same acquired data. JSON serialization is measured separately.
These timings exclude HTTP, rendering and input generation/writing; OS pages may
already be warm. They are performance evidence, not biological accuracy or a
FlowJo comparison. Machine details and measurements are in
`artifacts/plate-benchmark.json`.

The separate custom-grid benchmark in `tools/benchmark_custom_plates.py` checks
all 1,536 positions in a 48 × 32 grid with 131,072 acquired events and known
counts/medians. On this host full-event evaluation took 0.022s and SVG serialization
0.010s. Counts match exactly; medians are checked within one IEEE-754 ULP, including
the finite-arithmetic rounding observed in the numerical diagnostic. Timings
exclude input generation/writing, HTTP and native interaction and depend on host
load. Inputs are removed after source-byte and workspace checks. Evidence:
`artifacts/custom-plate-benchmark.json`.

FlowJo's documented workflows informed the feature scope:

- [Plate Editor overview](https://docs.flowjo.com/flowjo/experiment-based-platforms/plate-editor/plat-plate-overview/)
- [Importing metadata from CSV](https://docs.flowjo.com/flowjo/experiment-based-platforms/plate-editor/importing-metadata-from-a-csv-file/)
- [Titration](https://docs.flowjo.com/flowjo/experiment-based-platforms/plate-editor/plate-editor-titration/)
- [Visualizing plate-based data](https://www.flowjo.com/docs/flowjo10/experiment-based-platforms/plate-editor/visualizing-plate-based-data)

Desktop close handling follows Electron's [application lifecycle](https://www.electronjs.org/docs/latest/api/app).
