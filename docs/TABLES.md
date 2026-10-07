# Custom tables and sample comparisons

Open **Statistics → Custom tables** to build a live report. Definitions belong to
the project, are saved with revision checks, survive portable project restoration,
and support removal and undo. The population overview remains available.

## Rows, populations and values

Choose one row per sample or rows for every sample population. Sample selection
intersects the selected group; the text filter applies before formulas, pivots
and comparisons. A missing saved group produces no rows and an explicit notice.
Missing sample selections are reported rather than substituted.

Each statistic column can use the row population, all events, a complete path of
population names, or explicit per-sample gate IDs. Prototype paths match the full
hierarchy exactly. Missing and ambiguous paths produce blank cells with reasons;
an explicit mapping resolves ambiguity. An explicit all-events mapping is valid.
Model renames and replacements preserve table references to their assigned
populations, including descendants. Missing controls and populations remain
visible in the editor.

Columns support counts, parent/total frequencies, finite/nonfinite counts,
arithmetic mean, median, sample SD/variance, CV, robust CV, MAD, geometric mean/SD,
minimum, maximum and arbitrary percentiles. Intensity statistics use acquired or
compensated parameter values, without applying display transformations. Derived
expressions retain their own mathematical definition. Gate masks retain their
own compensation and transform settings. Frequency counts include all members;
intensity statistics omit nonfinite values. Geometric statistics require every
selected finite value to be positive. Sample SD/variance and geometric SD need
at least two finite values. CV is 100 × sample SD / |mean|; robust CV is
100 × 1.4826 × MAD / |median|. Zero denominators and unrepresentable numerical
results are undefined, not zero. Percentiles use NumPy's linear interpolation.

Keyword columns select user tags or acquisition metadata. Enable numeric keyword
values for arithmetic, pivots and comparison measures. Missing or invalid numeric
keywords have explicit cell status. Display decimals, heatmaps, hidden helper
columns, column ordering and sorting are independent of calculations. Heatmap
ranges cover every selected row, including rows on other pages.

## Formulas and controls

References bind to immutable column IDs once a definition has been evaluated;
renaming a column preserves existing formulas. Names are unique ignoring case.
Cycles, missing references and invalid syntax are rejected.

```text
col("Median signal") / mean(col("Median signal"))
col("Positive events") / col("Total events") * 100
ifelse(col("Treatment") == "Drug", col("Signal"), 0)
coalesce(col("Signal") / col("Baseline"), 0)
col("Signal") / control("Signal", "Untreated sample")
```

The language supports `+ - * / **`, unary signs, comparisons, `and`, `or`, `not`,
quoted strings and finite constants. Functions are `abs`, `sqrt`, `log`, `log10`,
`exp`, `asinh`, elementwise `min`/`max`, `clip`, `ifelse`, `coalesce`, and aggregate
`mean`, `median`, `sum`, `sd`, `n`. Aggregates use finite values across the
**selected, filtered rows**. In population-row mode, different populations of a
sample contribute separate rows. `sd` uses the sample denominator; `n` counts
finite values. Empty aggregates are undefined except `n`, which is zero.
Numeric strings can be converted by arithmetic; other text produces an explicit
formula error. Formula results are numeric; comparisons become zero or one.

A column's **Control value** selects a fixed source sample and can use a control
outside the table's row selection. Its statistic still maps the requested
population in that source sample. `control("column", "sample name or ID")`
instead looks up that column in the selected, filtered table rows, matching the
row's population path. This control must be present in that scope and uniquely
identified. Missing or ambiguous controls produce undefined values.

Formulas use a restricted parser, not Python evaluation. Attribute access,
indexing, imports, comprehensions, files and keyword arguments are unavailable.
Expressions are limited to 2,048 characters and 200 syntax nodes. This is
CytoForge's documented formula language; FlowJo script syntax is not imported.

## Biological model columns

Saved DJF and proliferation fits provide phase/generation fractions, model event
counts, posterior expected counts, assigned counts, fitted event counts,
parameters, fit diagnostics and proliferation indices. Fractions are proportions
from zero to one; **percent divided** uses percent units. A biological column
uses the model's original fitted population for the source sample; a statistical
column on an assigned population measures its hard maximum-probability members.
These definitions are different and are labeled separately.
The convergence diagnostic is numeric: one for converged and zero for not converged.

By default, biological columns follow replacement fits with the same live output
bindings. Saved references update when a replacement is applied, so deleting an
inactive historical report does not break the live reference. Disable **Follow
replacements** to retain the historical model. Removing a referenced report or
population leaves the table definition intact and marks unavailable cells;
removal previews list affected columns and undo restores their values.

Stale biological models, computed parameters, derived parameters that depend on
them, and populations that depend on stale parameters or QC are excluded by
default. **Include stale model values with status** allows review of retained
snapshots and preserves status in exports. A fit's convergence/overlap warnings
still require scientific review; the table does not establish biological validity.

## Pivots and comparisons

Pivots accept up to four row dimensions, three column dimensions, and 32 numeric
measures. Aggregations are mean, median, sum, finite count, sample SD, min and max.
Each pivot cell records its finite contributing row count. Missing combinations
are blank; a count of zero is defined. Headers identify dimension names and quote
their values to distinguish missing values from text such as `(missing)`.

Comparisons require one row per sample. Choose a group column, two exact group
values and numeric measures. Available two-sided tests are Welch's independent
t-test, Mann–Whitney U, paired t-test, and Wilcoxon signed-rank. Paired tests match
unique pair IDs, omit incomplete/nonfinite pairs, and reject duplicate pair IDs
within either group. Each group or paired comparison needs at least two eligible
observations. Direct columns sourced from a fixed control cannot be treated as
independent sample observations.

Results include eligible/excluded counts, group means, mean and median differences
(A minus B), the test statistic, raw/adjusted p-values, and a configurable t-based
confidence interval for t-tests. Undefined degenerate tests have reasons. Holm
familywise correction is the default; Benjamini–Hochberg or no correction can be
selected. Adjustment covers only valid tests among the selected measures in that
comparison. Technical replicates, shared controls, repeated measures and other
experimental dependencies require an appropriate design; the application cannot
infer independence from sample filenames or metadata.

Mann–Whitney uses SciPy's automatic exact/asymptotic selection with two-sided
testing. Wilcoxon tests the pairwise differences with `zero_method="wilcox"` and
automatic method selection; all-zero differences return statistic zero and p=1.
Library versions are included in export provenance.
Rank tests use original observations or computed pair differences so rescaling
does not introduce new ties or break existing ones. T-tests center and scale
inputs, preserving small differences on top of a large common offset.

## Exports and bounds

The display uses 100-row pages. CSV, XLSX and JSON export **all selected rows**,
regardless of the current page, up to 50,000 rows and two million cells. A table
supports 128 columns; a project supports 1,000 saved definitions. Pivots are
limited to 512 result columns and two million result cells.

- CSV contains sample/population IDs and visible columns by default, with raw
  numeric values and blank undefined cells. Strings that can be interpreted as
  spreadsheet formulas receive an apostrophe prefix.
- XLSX keeps numeric cells and display formats, frozen headers, filtering and
  conditional heatmaps. It includes Data, Cell status, optional Pivot and
  Comparisons, and Provenance sheets. Strings remain literal, including URLs.
  Cell-status sheets split at Excel's row limit. Text exceeding 32,767 UTF-16
  units is preserved in ordered **Long text** chunks; the source cell identifies
  its location. Concatenate chunks by source sheet, cell and part to recover the
  full value, including a large definition or source manifest.
- JSON includes hidden columns, cell status, full precision values, definition,
  pivot counts, comparison diagnostics and source manifest.

The source manifest identifies raw sample hashes, relevant acquired channel
order, compensation matrices, gate definitions, parameter dependencies, used
keywords, model input/output fingerprints, staleness and software versions.
Retain the portable project when the event arrays and complete historical model
reports are needed to reproduce the analysis. XLSX generation streams rows and
uses the configured project-local temporary directory.

## Validation and remaining scope

Tests compare population statistics to independent NumPy calculations, each test
to separately constructed SciPy references, and p-value adjustment to known
examples. API and browser checks exercise rename bindings, ambiguity, sample
scope, hidden helpers, pivots, comparisons, exports, archives and undo. Both
biological platforms are checked through actual fits, replacements, renames,
stale dependencies, removal and restoration. Spreadsheet XML is inspected for
numeric cells, literal strings, complete Unicode chunks and sheet rollover.

These checks do not establish every FlowJo convention or import FlowJo table
definitions. Mixed models, repeated-measure ANOVA, resampling tests, graph/report
integration, cross-project table templates and exhaustive instrument/FlowJo
reference comparisons remain open. Native Windows/macOS and manual
Excel/LibreOffice application checks remain unverified.

Primary references: [FlowJo table columns](https://docs.flowjo.com/flowjo/tabular-reports/te-columndefinition/),
[FlowJo scripting](https://docs.flowjo.com/flowjo/tabular-reports/te-scripting/),
[SciPy Welch test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind.html),
[SciPy Mann–Whitney](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.mannwhitneyu.html),
and [XlsxWriter memory behavior](https://xlsxwriter.readthedocs.io/working_with_memory.html).
