# Portable report templates

Layout Studio can save a composition as a `.cytoforge-report.json` file and load
it into another experiment. This feature is implemented in the current desktop
source. Its scientific/API tests pass; native interaction and installer promotion
are still pending. The promoted AppImage in `artifacts/installers` does not
contain these controls. An isolated candidate at
`artifacts/candidates/report-templates/desktop/CytoForge-0.1.0.AppImage` contains
the current interface, native window handlers and checked frozen engine. Its
full engine tree, desktop files and UI assets pass content verification; see
`artifacts/report-template-desktop-package-integrity.json`.

In Layout Studio, use **Export template** to save the current composition,
including an unsaved draft. Open the destination experiment, choose **Load
template**, and select the file through the desktop file chooser. Set the new
report name, select destination sources, and choose **Review bindings**. Review
the rendered prototype pages before importing. Changing a parent source clears
its manually selected children so that populations, table columns and comparison
coordinates can be reviewed against their current owners.

The review suggests a match only when it is unique: sample and saved-source
names, full population ancestry, and table-column/comparison-coordinate
descriptors. References to objects in the same experiment retain their existing
IDs where available. Duplicate matches require a choice. Population and channel
choices belong to their selected acquisition; table columns and comparison
coordinates belong to their selected saved definition or result. A population
may be explicitly mapped to **All events** where the report permits it. Review
does not silently replace a missing population with the acquisition root.

Supported bindings include acquisitions, hierarchical populations, raw/derived
parameter names, pooled groups, fixed compensation definitions, tables and
selected columns, plates, cell-cycle/proliferation/kinetics models, and population
comparison results and their coordinates. Ratios retain their virtual parameter
names while their underlying parameters are rebound. Three-dimensional
coordinate/color/size parameters, locked overlay controls and live statistic
tokens also retain their explicit bindings. Captions, ordinary text, appearance,
page geometry and object-group relationships remain part of the composition.

Templates contain composition definitions and source labels/IDs needed for
binding review. They exclude acquired events, fitted curves and probabilities,
matrix coefficients, table measurement values, and acquisition metadata. An
imported biological figure uses a model already saved in the destination; it
does not fit a new model or copy an old one. Destination tables, compensation
matrices and plate assignments also remain unchanged.

For a batched report, **Use destination group or experiment** rebuilds its
acquisition selections and overrides against the destination cohort. **Rebind
the template's exact sample selection and overrides** preserves those settings
through explicit destination mappings. Keyword/panel override keys remain
literal; review warns when a saved override has no matching destination
iteration. The prototype can be imported while a future batch still needs
review, but normal batch/export checks continue to enforce its source policy.

Import creates a new saved report with fresh object IDs in one workspace history
step. Undo removes it and redo restores it. Existing saved reports are not
replaced. If the active composition has an unsaved draft, importing preserves
that draft and adds the new saved report to the report picker. A reviewed hash
binds the operation to the destination revision, selected sources and report
plan; changes require another review. Saved reports, page manifests and portable
project archives retain the template SHA-256, source experiment/layout IDs,
resolved binding choices and batch policy.

Files use the version-one `cytoforge-report-template` JSON schema and a canonical
SHA-256 integrity check, with an eight-MiB limit. Missing/extra binding records,
conflicting ownership, duplicate JSON keys and malformed files are rejected.
The digest detects content changes; it is not a publisher signature.

Validation includes real destination event counts and live statistics, fixed
compensation and 3D/ratio coordinates, ambiguous populations and foreign owners,
simultaneous parameter renaming, batch override key/value remapping, pooled
plots and plates, actual saved artifacts for all three biological platforms and
population comparison, authenticated file parsing, stale-review rejection,
undo/redo, and provenance through project archive restoration. Current evidence
is recorded in `artifacts/report-template-source-validation.json`.
The desktop candidate and preview checks are recorded in
`artifacts/report-template-desktop-candidate-validation.json`.
`tools/desktop_report_templates_smoke.mjs` is prepared for
native export/import, destination prototype values, draft preservation,
undo/redo and an independent plot window in its own test profile. It has not
been run in this environment. Socket restrictions still prevent a new native desktop
interaction run in the agent environment.

On the server, the prepared native check uses a separate owned profile:

```bash
source tools/env.sh
CYTOFORGE_TEST_NO_SANDBOX=1 node tools/desktop_report_templates_smoke.mjs
```

The override is specific to this headless test. A completed run writes
`artifacts/desktop-report-templates-appimage.json` with the tested AppImage hash,
individual checks, renderer errors and the actual result. The named desktop
preview command in [the README](../README.md) lets you inspect the same candidate
through its native screen viewer.

This format covers reusable CytoForge report compositions. Full FlowJo WSP/WSPT
layout/template import, template transfer of gating/analysis definitions, and the
remaining publication editing controls stay within the active full application
scope. See [reports](REPORTS.md) and the [scope ledger](ROADMAP.md).
