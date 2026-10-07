# Virtual group populations

Use **Pool group** in the sample list or plot controls to view the current group
and search filter as one population. Both controls describe the same plot window.
Every detached native window has its own group, pooling state, population, axes
and camera. These views survive a clean desktop restart.

The approach follows the temporary pooled view described in the
[FlowJo virtual population guide](https://flowjo.com/docs/flowjo11/analysis-tree-2/virtually-concatenated-populations).
It creates no acquired event file or new workspace sample. Coordinates come from
the original samples, using each one's assigned correction and the plot's shared
axis definitions. The current implementation assembles selected one-dimensional
coordinates in a bounded cache; it does not promise constant memory for arbitrarily
large cohorts.

Common parameters must exist and have compatible definitions in every member.
Panel aliases can provide shared marker names across different detector panels.
Formula definitions must agree; fitted outputs must belong to the same joint fit.
Categorical source and keyword codebooks must agree. Already scaled measurements
cannot silently share an acquisition-space coordinate.

Populations match their exact ancestor-name paths. Each member retains its own
geometry, parent, model and correction. Missing or ambiguous paths produce an
error and an unavailable count. Stale fitted parameters and populations require a
refit. Events from an incompatible member are never silently omitted.

Drawing, editing or deleting in a pooled plot opens a review of every affected
sample and population count. Application checks the revision and review hash
again and commits all changes in one undoable transaction. Linked partitions,
children and dependent boolean populations retain their ordinary per-sample
relationships. Magnetic positions follow each sample separately. Canceling returns
to the draft; Escape closes only the top review. A changed workspace disables the
proposal. Stale populations can be removed, with their counts explicitly unavailable.

**Analyze pooled group** configures the existing discovery workflow with the actual
member and population IDs. PCA, UMAP, t-SNE and FlowSOM retain their existing fit,
sampling and projection rules. Adding an unrelated gate does not change a fit;
editing a fitted input population reports the models that will become stale.

**Export events** writes the pooled population through the existing staged native
save workflow. FCS uses float64 measurements, and both FCS and CSV include the
original member and local event index. FCS reopening restores this lineage and its
source history. Compensated export applies each original sample's correction once.
Stored export requires a compatible shared spillover basis and deduplicates aliases
of the same acquired detector. Differing raw matrices require compensated export.
Pooled event files currently support up to 128 original members.

Saved layout plots retain the group and filter. Two-dimensional and 3D reports use
the pooled events and record every source dependency. Export validation checks all
source event and fitted-model bytes, including sources previously read into caches.
The 3D stream uses global indices plus explicit source offsets; these resolve to
distinct original sample and local event identities.

Verification includes independent detector fixtures, selected populations, all
seven planar modes, 3D streams, FCS/CSV round trips, linked partitions, shared edits
and removals, stale joint fits, report integrity, empty members, native windows and
restart. The million-event check covers eight distinct original correction matrices,
two million measurement values and the absence of a merged acquisition file.
Timings describe this Linux CPU test with a warm filesystem. Native platform,
instrument, hardware-GPU and broader FlowJo parity requirements remain active.
