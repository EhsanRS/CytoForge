# Panel harmonization in the desktop app

Select samples in **Samples → Harmonize panel**. Add a shared parameter name,
then choose its original source in each sample. For example, `CD3` can point to
`FL1` in one panel and `B1` in another. **Suggest from marker labels** proposes
only exact labels with one matching source in every selected sample. Suggestions
remain editable and require review; ambiguous labels are never guessed.

**Review mapping** shows additions, retained definitions, changed bindings and
removals. A changed binding lists its saved consumers and any fitted analyses that
will become stale. **Apply channel aliases** publishes the complete selected
mapping in one revision and one undo step. A concurrent edit invalidates the
review. Cancelling, reviewing and plotting do not modify scientific state.

## Measurement identity

An alias is a virtual name over an existing measurement. The immutable acquired
array, detector order, event IDs, SHA-256 digest and compensation detector names
stay intact. Alias reads resolve the source before applying compensation, a fixed
gate matrix, a ratio or a requested transform. They work in independent native
plot windows, saved populations, formulas, statistics, tables, layouts, joint
analyses and elementwise concatenation.

New aliases copy their source's range and display transform. Rebinding an existing
alias preserves its own display transform. The mapping is explicit and case
sensitive; it does not calibrate or normalize intensities between instruments.
Only acquired channels and active spectral outputs are offered as new sources.
Alias chains and aliases over formula or fitted-output parameters are rejected.
Those outputs already have explicit names and model dependencies.

Existing aliases over an unmixed output remain declared if its matrix is later
unassigned. Their values follow the existing inactive-output behavior (NaN), and
the editor marks that source inactive. Stored FCS export requires the matrix to
reconstruct that output; materialized export can retain its actual values.

## Saved work and exchange

Changing a binding updates live gates and reports using that alias. Scientific
fingerprints follow the source binding, including aliases used by formulas and
gate ratios. Dependent fitted models become stale; their immutable result arrays
and historical snapshots stay available. Adding an unrelated alias leaves old
scientific fingerprints unchanged.

Removing an alias referenced by a saved population, formula, fitted analysis,
table, plate or layout is rejected with the consumer's name. Update or remove that
definition first. Portable projects, undo/redo and desktop restart preserve the
mapping and its display definitions. Empty alias fields are omitted from legacy
workspace serialization.

Stored FCS export retains the original physical `$PnN` names and matrix detectors.
The checked CytoForge metadata carries aliases and their display definitions;
reimport restores them without adding acquired columns or applying correction
twice. Other FCS readers can read the original detectors. Compensated or scale FCS
materializes alias columns and keeps the source mapping in history. The desktop
export review reports retained alias definitions. Stored numeric CSV contains the
acquired columns; compensated/scale CSV includes alias columns. The existing
quick CSV API also includes virtual parameters for compatibility.

Raw concatenation can map canonical aliases to acquired columns. A preserved
spillover matrix is mapped through the original detector names into the merged
output names. Selecting both an alias and its original acquired column in the
same raw merge is rejected. Aliases of unmixed outputs require compensated or
scale concatenation. Every selected alias binding enters the immutable source
snapshot. Gating-ML export resolves aliases back to actual detector or unmixed
output names, including both operands of ratios.

## Verification

`tests/test_channel_aliases.py` checks reordered panels against independent raw
and corrected values, ratios and fixed matrices, atomic apply, revision/review
guards, saved consumers, fitted-model staleness, weighted spectral reconstruction,
portable projects, FCS precision and invalid metadata, and Gating-ML exchange.
`tests/browser/channel_aliases.spec.ts` covers the review/apply interaction,
editable conflicts and concurrent revisions.

`tools/desktop_aliases_smoke.mjs` imports independently written double-precision
FCS panels through the native file picker, exercises mapping review, separate
native windows, shared gating, native file saving and reopening, joint PCA and
real desktop restart. Packaged proof is recorded in
`artifacts/desktop-aliases-aliases-appimage.json`.
`tools/benchmark_channel_aliases.py` verifies four million raw/corrected alias
measurements for one million events in bounded truth-check chunks, byte-identical
acquired data, and shared physical/alias correction RAM. Results are local Linux
measurements with a warm filesystem cache, not hardware or cross-platform parity
claims. See `artifacts/benchmark-channel-aliases.json`.

Panel aliases advance the broader desktop goal. Virtual group concatenation,
batch normalization, other advanced management workflows and the remaining
platform and instrument validation are still tracked in [ROADMAP.md](ROADMAP.md).
