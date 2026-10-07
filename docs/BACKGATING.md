# Backgates and ancestry reports

Select **Backgate population** in a main plot or an independent native plot
window to highlight a population inside the plotted population. Membership uses
the full scientific mask, including ancestors, Boolean operands, magnetic
resolution and each gate's own coordinate definitions. Changing the backgate
does not change the base histogram, CDF, density or population count.

The footer distinguishes the finite intersection, events inside the current
axes and displayed highlights. Planar highlights sample at most 6,000 events
inside those axes using deterministic seed 43. Sampling can miss rare parts of
the highlighted population; its complete count remains available. Moving the
axes changes the visible markers rather than sampling mostly invisible events.
Histograms and CDFs show a bottom rug indicating highlighted X values; their
curves and normalization remain unchanged.

In 3D, highlighted events retain their original event IDs, positions, color
values and size values. Highlight flags travel with each streamed event. The
**All events** setting controls whether every visible event or a deterministic
sample is drawn. Complete, visible and sampled highlight counts remain separate.
Camera-visible report counts also remain distinct from events inside the XYZ
axis bounds.

## Save the current view

**Layout studio → Add current plot** retains the current backgate. Each plot
layer's **Backgate highlight** selector can change or clear it. Changing a layer's
acquisition clears references belonging to the previous acquisition. Independent
native windows retain separate selections.

Publication SVGs draw planar highlights as vector markers and univariate rugs.
3D SVGs color the same flagged event identities. A second legend line identifies
the backgate and distinguishes finite events from drawn highlights. Fonts and
gate appearance settings remain available in the report inspector. Increasing
figure size or reducing fonts resolves a legend that cannot fit its statistics.

Saved report definitions, drafts, undo/redo, portable projects, template bindings
and report ZIP manifests retain the selection. The manifest records its finite
denominator, fraction, total/visible/displayed counts, sampling and scientific
source closure. For 3D, it also records a digest of the displayed highlighted
event IDs. A backgate driven by a biological model participates in that model's
dependency review before removal.

## Build an ancestry layout

Choose **Ancestry across** or **Ancestry down**, then **Add backgate ancestry**.
The selected backgate, or the current population when no backgate is selected,
appears in the context of every ancestor. Each stage plots the population before
that gate and uses the gate's original axes, transforms, ratio definitions and
compensation. Three-dimensional gates use their first three ordered dimensions.
Boolean stages retain the current display coordinates and camera.

Stages run from the sample toward the selected population. They paginate inside
the current page's margins and header/footer area. Existing report objects are
retained; an otherwise empty default layout can use its first page. Cyclic,
missing or foreign ancestry and layouts exceeding the existing 32-page or
256-element limits produce an error without inserting a partial ancestry.

For a sample-iterated report, both the plotted population and its backgate map by
their complete population paths. Locked controls retain their source acquisition.
Missing or ambiguous paths require a mapping review; an unresolved backgate does
not become all events or disappear. Portable templates bind it to an actual
destination population. Explicitly clearing a backgate uses the layer selector.

## Verification and remaining scope

`tests/test_report_backgates.py` verifies independently enumerated intersections,
range boundaries, viewport sampling, literal highlight pixels, vector output,
sample/locked-control mappings, pooled scientific closures, missing/ambiguous
sources, magnetic provenance, biological removal review, project exports,
templates and history. A 66,000-event case verifies highlights across chunk
boundaries and sampled clouds. `tools/test_report_backgates.sh` executes the
compiled interface helpers, including ordered ratio/repeated-parameter 3D axes,
ancestry placement, rejected partial layouts, canvas drawing and count summaries.

`tools/benchmark_report_backgates.py` checks a complete 1,048,576-event grid with
131,072 independently labelled backgate events, unchanged base payloads and
complete streamed IDs/highlight membership. Its timings cover source calculations
and vector reports, excluding HTTP, native interaction and hardware GPU work.
The frozen worker validator checks bundled report backgates and their template
round trips. `tools/desktop_backgate_reports_smoke.mjs` prepares an actual
AppImage workflow in its own profile; native execution remains pending where
local TCP sockets are prohibited.

Additional backgate styling, large planar rendering, hardware WebGL2 validation,
full FlowJo compatibility and the remaining cross-platform/scientific scope stay
open in [the implementation ledger](ROADMAP.md).
FlowJo's corresponding workflow is described in its
[backgating documentation](https://docs.flowjo.com/flowjo/graphical-reports/graph-options-and-annotation/le-backgate/).
