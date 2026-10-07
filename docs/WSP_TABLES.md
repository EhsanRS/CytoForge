# Saved FlowJo tables

Import a `.wsp` through **Import gates**, review the sample mappings and the
saved-table preview, then acknowledge the conversion report. Supported tables
appear under **Statistics → Custom tables**. The original workspace XML and
conversion report remain available in the import record and portable project.

Sample-iterated tables retain their declared workspace or group scope and source
sample order. Columns support counts, parent/total percentages, mean, median,
sample standard deviation, CV, minimum/maximum, explicit percentiles and median
absolute deviation. Keyword columns read current target annotations first, then
acquisition metadata, using case-insensitive matching with optional `$` prefix.
Import can add missing non-system annotations from the workspace; existing target
values and acquisition metadata take precedence. Conflicts appear in the report.

Imported population columns bind to the gates created by that import, per sample.
An absent or unsupported source population produces an undefined cell with a
status. Existing populations with similar names do not substitute for it. Intensity
columns retain the source's raw or fixed compensation basis, including distinct
spectral output names. Changing the sample's current compensation does not change
those column bindings. The column editor can explicitly switch a column to the
sample's current compensation.

**Define as control** freezes a column at its first original source sample, for
numeric, keyword and formula columns. An unmapped original control remains
undefined. Hidden helper columns still participate in formulas. Column aliases
bind to stable identifiers; missing or ambiguous aliases are reported.

The restricted formula converter supports numeric arithmetic, comparisons,
logical operators, `Ifthen`, absolute value, rounding up/down, minimum/maximum,
exponential/logarithmic, square root and trigonometric functions. FlowJo `Log`
means base ten, `Ln` means natural log, and its one-argument `pow` means
exponential. `<Cell column="Name"/>` references the current row; `[1]` references
the first input row, `[-1]` the preceding row and `[+1]` the following row.
Out-of-range references remain undefined. Row references use the retained input
sequence before a user applies a native display sort. Expressions cannot execute
Python, JavaScript, file access or workspace scripts.
Unary logical negation retains its source precedence; chained comparisons require
explicit Boolean predicates and are otherwise reported as unsupported.

Calculation differences are explicit. FlowJo documents binned intensity
statistics translated through its display scales. CytoForge calculates supported
location/spread statistics from all retained physical event values, so these
columns require acknowledgement and may differ numerically. Count/frequency
agreement depends on the converted gates. Geometric statistics, FlowJo robust CV,
arbitrary ancestor frequencies, text formulas, ambiguous `Round` behavior,
advanced iteration/discriminators and complete print styling are unconverted.
They remain in the original XML and generate compatibility diagnostics where
encountered. Complete FlowJo workspace and numeric parity remain unfinished.

Validation uses two retained eight-color FlowKit reference workspaces, each with
three tables and 39 columns. Supported count/frequency cells are checked against
independent FlowKit masks; absent paths remain undefined. An owned two-acquisition
fixture supplies literal compensated/raw/control values, keywords, formulas,
fixed/relative row references and hidden inputs. Scientific/API tests cover mapping
conflicts, controls, spectral aliases, restricted formulas, archives and undo/redo.
The native desktop harness exercises file choosers, acknowledgement, JSON/XLSX
downloads, restart and portable project restore; the independent OOXML validator
checks exported numeric/text/missing cells and byte-exact retained XML.

Primary FlowJo references:

- [Workspace XML](https://www.flowjo.com/docs/flowjo10/workspaces-and-samples/ws-ribbons-and-tabs/ws-ribbon-band-debug/workspace-xml)
- [Statistics definitions](https://flowjo.com/docs/flowjo10/workspaces-and-samples/ws-statistics/ws-statdefinitions)
- [Formula syntax](https://docs.flowjo.com/flowjo/tabular-reports/te-columninfo/te-scripting/)
- [Column attributes and controls](https://docs.flowjo.com/flowjo/tabular-reports/te-columndefinition/te-defineattributes/)
- [Keyword columns](https://docs.flowjo.com/flowjo/tabular-reports/te-columninfo/te-keys/)
