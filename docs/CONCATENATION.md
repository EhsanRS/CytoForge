# Merged samples and event origins

In the desktop Samples panel, select acquisitions and choose **Concatenate
populations**. Select an individual saved population for each source, map
parameters explicitly, and choose one output, batches of N sources, or outputs
grouped by a keyword. Prepare merge writes a draft with progress and cancellation.
Review its event counts and matrix assignments before Create samples. The entire
batch is one undoable workspace change; acquired samples and saved gates remain
intact. Merged samples support ordinary analysis, gating, statistics, reports,
and independent native plot windows.

Names match parameters by default, independently of their acquired column order.
The mapping table supports renamed detectors and custom subsets. A selected
source parameter can appear only once. No rows are sampled: membership uses the
engine's complete saved population mask, including its transforms, ratios,
compensation references, parent gates, Boolean gates and applicable fitted models.
Stale model inputs are rejected. Nonfinite values in selected raw measurements are
retained; a population's own coordinate validity rules still determine membership.

## Scientific value space

- **Raw acquired values** copy measured detector values. Preserving compensation
  requires the same mapped spillover coefficients for every source in an output,
  all matrix detectors, and compatible source assignments. Each output receives
  its own matrix snapshot. Later source-matrix edits cannot change that snapshot.
  Explicitly discarding the assignment keeps raw measurements without a matrix.
  Raw spectral acquisition data requires that explicit choice.
- **Compensated / unmixed values** materialize each source's assigned correction,
  including spectral background and detector weights. Formula and fitted output
  parameters are available. The output has no matrix assignment, so these values
  are not corrected a second time. Its display transform defaults to the first
  source's parameter transform and can subsequently be edited.
- **Compensated display scale values** additionally materialize each source
  parameter's display transform. The output channels use a linear display scale.
  Different source transforms can therefore produce different numeric scales;
  the scientific snapshots retain each transform for review.

Compensation works on at most 16,384 source events per write chunk, rather than
allocating the entire corrected event matrix. Elementwise formulas use the same
slice. Population masks and the existing engine's model/gating calculations keep
their usual memory and cancellation characteristics; cancellation is cooperative
between those calculations, integrity checks and write chunks. Two event writers
can run simultaneously and at most four jobs can be active. Entirely empty
outputs are rejected, while empty sources inside a nonempty output remain in its
lineage with a zero retained count.

## Exact lineage and persistence

The merged matrix is float64. CF_Source is the source's one-based index in the
selection; CF_EventID is its original zero-based event ID. Additional keyword
columns use one-based category codes, with a codebook including missing values
in the sample's provenance. Workspace tags take precedence over file keywords.
Grouping requires the requested keyword on every selected source.

An independent hashed uint64 array stores each row's exact source index and event
ID. **Origins** opens the native inspector, with a paged event list and streamed
CSV export containing exact integer IDs and original source names and IDs. The
provenance includes source hashes, selected population definitions, parameter
mappings, correction definitions, fitted dependencies, annotations, original
event counts, retained counts and output offsets. It remains valid after original
samples are removed. If a merged sample is merged again, its source snapshot also
retains the earlier lineage.

Saving verifies source bytes, staged event bytes and the origin array, checks the
reviewed definitions, and requires the original workspace revision. A stale or
damaged draft cannot overwrite another window's edits. Copy failures clean up
uncommitted files. Undo/redo retain immutable output data. Native restart restores
saved samples and plot windows. Project archives include hashed origins and the
full snapshots; restoring a damaged or missing origin array rejects the archive
without creating a partial workspace. Completed, cancelled and interrupted drafts
are recovered and cleaned when the engine starts again.

**Export events** saves population FCS with float64 measurements, exact origins,
complete source history and the assigned raw correction snapshot. Reopening it
restores those definitions and verifies DATA, metadata and rebuilt uint64 origins.
CSV exports numeric rows with full precision. See [EVENT_EXPORTS.md](EVENT_EXPORTS.md)
for native saving, value spaces, cancellation, integrity and memory limits.
FlowJo 11 virtual group concatenation remains unfinished. Physical concatenation
does not establish complete FlowJo feature, instrument or platform parity.

The workflow follows the export/value-space distinctions described in the
[FlowJo export documentation](https://flowjo.com/docs/flowjo10/workspaces-and-samples/samples-and-file-types/ws-export)
and its explanation of
[raw versus compensated concatenation](https://www.flowjo.com/learn/flowjo-university/flowjo/advanced-topics/concatenating-compensated-parameters-vs-uncompensated).
The separately scoped virtual workflow is described in the
[FlowJo 11 analysis tree](https://flowjo.com/docs/flowjo11/analysis-tree-2).
