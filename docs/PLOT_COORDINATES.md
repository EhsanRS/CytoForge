# Native plot coordinate definitions

A gate may use the same measured parameter on several axes with different
compensation references, transforms or ratio expressions. Those dimensions
remain separate scientific definitions. For example, a raw X axis and an
asinh-transformed X axis using a fixed diagonal matrix must not both become the
last X definition in the gate.

Ordinary desktop plots, independent native windows, shape previews, 3D point
streams, vector reports and sample navigation retain the gate's ordered axes.
A one-dimensional view of its first axis retains that first definition. Changing
to a uniquely defined parameter still finds its definition by name. Display
transform overrides apply independently to each selected axis.

Rectangle and volume gates drawn in native coordinates save the exact references
of each displayed axis. Repeated measured parameter names are accepted for
volume gates. Membership uses the original full event arrays, with ordinary
nonfinite and parent-mask semantics. Plotting and navigation do not edit saved
gates, matrices or workspace history.

Overlay dimensions match distinct compatible display axes, preferring their
original order. This also preserves two axes with the same compensation basis
and different transforms. Sample navigation checks every axis, including repeated
names, and compares fixed matrices by their scientific definition rather than
their names or identifiers.

Acquisition channel labels and settings remain separate from synthetic ratio
definitions. Per-axis controls use the selected native dimension. A color, size
or rearranged axis request that cannot distinguish several definitions returns
an explicit error. Choose a uniquely defined parameter or leave gate coordinates
using **Sample scales**. Arbitrary per-axis reference selection and richer
color/size reference controls remain part of the full objective.

## Verification

Thirteen independent source cases cover five planar modes, histogram/CDF,
raw/fixed/asinh coordinates, shared report scales, 3D ratio streams and original
event identities, repeated-basis overlays, ambiguous scalar rejection and native
sample navigation. Fixed-matrix navigation also covers equivalent renamed
matrices and a changed coefficient. The full scientific/API suite passes 696
cases; evidence is `artifacts/pytest-ordered-coordinates-full.log`.

`tools/desktop_plot_coordinates_smoke.mjs` imports independently labelled events
and checks actual native plotting, pointer drawing, CDFs, three coordinate bases,
volume gating, vector reports and restart. Its rectangle and volume gates select
exactly 137 labelled cells. The 3D view retains 1,295 parent events and identifies
1,286 finite coordinate events, including the expected zero-denominator exclusions.
Native evidence records the binary and explicit headless sandbox exception.

These checks do not establish complete FlowJo numerical or feature parity,
normal sandboxed Linux startup, Windows/macOS/ARM or hardware GPU performance.
Direct exploration-view shape editing, arbitrary coordinate reference selection,
broader real-acquisition/large-workspace validation and every remaining requirement
in [ROADMAP.md](ROADMAP.md) remain active.
