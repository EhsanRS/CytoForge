# Linked bisectors and quadrants

In a native main or plot window, select **Histogram** or **CDF**, then use
**Bisector gates** (**B**) and click a threshold. Review the name and **X threshold**
before saving. The negative population contains values below the threshold; the
positive population contains values at or above it. Both ranges extend beyond the
displayed viewport. Zoom does not crop their scientific membership.

On a two-dimensional plot, **Quadrant gates** (**Q**) creates four populations
sharing an X/Y threshold pair. Q1 is upper left, Q2 upper right, Q3 lower right,
and Q4 lower left. Events exactly on a threshold belong to its positive side.
Each eligible finite parent event belongs to exactly one member. Nonfinite native
coordinate values remain excluded; a ratio with a zero denominator is ineligible.

**Edit gate on plot** or double-click a saved boundary to edit any member. Drag its
threshold handle or shared corner; arrow keys nudge the focused handle. The editor
shows every linked member's full-event count. **Numeric settings** retains the
draft and exposes X/Y thresholds and the saved scientific coordinate definitions.
The open ends of the intervals remain open.

Saving changes every member's thresholds, parent and coordinate definitions in
one revision and one undoable operation. Member IDs, descendants, individual names
and colors remain intact. Dependent populations, statistics and other native
windows refresh together. Each window keeps its own axes, zoom and display mode.
Cancel leaves every member unchanged. Concurrent workspace changes retain the
draft and block Apply until deliberate review; the ordinary native drawing and
close guards also apply.

Deleting a member opens a confirmation that explicitly names the linked family.
It removes the entire family, its descendants and dependent Boolean populations;
Undo restores their exact IDs and definitions. Tree propagation creates fresh
family IDs on each target sample so subsequent source edits cannot move a copied
target family. Incomplete families, duplicate members, mismatched parents or
coordinate definitions, detached membership and separate complements or magnetic
shifts are rejected atomically.

The scientific populations use ordinary one- or two-dimensional half-open
hyperrectangles. Every ordered axis retains its transform, compensation reference
and ratio definition, including duplicate measured-parameter labels. Ratio labels
are display identifiers; membership consistency compares their ordered input
parameters and coefficients. No new membership approximation is introduced.

GatingML export encodes each population as a standard `RectangleGate`, with absent
minimum/maximum bounds representing the open ends. A schema-valid CytoForge
`custom_info/linked_partition` element records the family relationship. CytoForge
restores that relationship on import and remaps family IDs separately for every
target sample. Other readers can use the standard geometry while ignoring the
extension. Portable workspace data retains the relationship directly.

Existing independent quadrant gates and imported strategies lacking this metadata
retain their original relationships. Automatic recognition of shared dividers in
other FlowJo/GatingML strategies, curly vendor/interchange comparisons and the remaining complete
FlowJo features are still required. Native shared-center spider gates and automatic
density gates are described in [Spider gates](SPIDER_GATES.md) and
[Autogating](AUTOGATING.md). Native curly quadrants and their explicit noise
convention are described in [Curly quadrants](CURLY_GATES.md). The complete project scope
has not been reduced to these partition tools.

`tests/test_gate_partitions.py` adds 16 independent scientific/API checks for
boundary membership, read-only previews and cache isolation, family-wide edits,
children and Boolean dependencies, undo/redo, invalid requests, propagation,
deletion, fixed compensation/ratio/transform coordinates and GatingML round trips.
The current full source suite passes 721 scientific/API cases and 21 interface
workflows. `tools/desktop_partition_smoke.mjs` verifies 13 native workflows in an
isolated headless Linux x64 profile, including actual threshold/corner drags,
independent popup CDF views, stale drafts, numeric handoff, propagation, family
delete/undo, ordered native coordinates, nested CDF partitions and normal restart.
This uses the explicit test sandbox exception with hardware GPU disabled; it does
not verify normal sandboxed Linux launch, physical input devices or Windows/macOS.

`artifacts/benchmark-linked-partitions-preview.json` records full-parent previews
on one million independently labelled events. Five requests after the first
measured median times of 43 ms for histogram bisectors, 66 ms for CDF bisectors and
121 ms for quadrant density previews. Counts matched independent labels on every
request, and workspace/history data remained unchanged. These local TestClient
timings include the isolated draft engine, plotting and JSON parsing; they exclude
native rendering, with OS caches and host activity uncontrolled.

FlowJo's documented bisector workflow also splits histogram/CDF data into adjacent,
non-overlapping negative and positive ranges. See [Drawing gates](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-gating/gw-gatedrawing).
