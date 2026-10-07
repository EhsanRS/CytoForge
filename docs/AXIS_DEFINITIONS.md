# Independent desktop coordinate definitions

Choose **Coordinates** beside X, Y or Z in the desktop analysis view or a native
plot window. Each axis can use a measured/derived/computed channel or an explicit
ratio, a compensation reference, and its own transform. In 3D, **Color
coordinates** and **Size coordinates** provide the same choices for scalar
encodings. Cancel preserves the current view. Apply validates the definition
against the current acquisition and workspace revision without writing the
scientific workspace or history.

Compensation choices are current sample compensation, raw/uncompensated,
embedded FCS compensation when present, and a fixed workspace matrix whose
detectors match the acquisition. A fixed reference identifies a matrix; editing
that matrix changes its results. It does not retain a historical copy of its
coefficients. The sample reference follows the sample's current assignment.
The engine retains its existing fallback to raw values when an embedded FCS
matrix is absent.

Ratio coordinates use
`A * (numerator - B) / (denominator - C)` with the selected compensation applied
to both inputs before division and the selected transform applied afterward.
Ratio labels are local to the definition and do not add channels to the input
file or workspace. Optional ratio clamps run before the transform. An unbounded
division by zero remains nonfinite and is excluded from the display. Explicit
clamps can map positive/negative infinity to finite bounds; NaN remains NaN.
Population count, finite-coordinate count and viewport count remain distinct.

Two axes can name the same channel while using different matrices, transforms
or ratio definitions. Ordered definitions and exact full-event data determine
the result. Native display buffers and marker sampling never define a gate mask.
The legacy 3D default compensation control supplies raw coordinates only to
axes/scalars without explicit definitions. Explicit per-axis choices take
precedence.

New planar gates, linked partitions, volume gates and automatic density outlines
capture the coordinate definitions they use. Existing gates keep their saved
definitions and population membership when a view changes. Native shape editing
uses the gate's own definitions and rejects arbitrary plot overrides. Incompatible
overlays are omitted instead of being drawn in the wrong compensation basis.

Native duplication, population view memory and restart preserve each window's
choices. Compatible sample navigation retains the explicit definitions, checks
ratio input availability and matrix detectors, and preserves the source scales.
Layout Studio captures them in the current plot. Reports include the resolved
axes, ratio input parameters and referenced compensation matrices in scientific
provenance. Project serialization omits absent new fields so legacy documents
keep their existing shape.

The scientific tests cover independent raw/fixed values, repeated channel names,
all planar modes, zero denominators, full-event 3D/scalar streams, stale stream
keys, validation without writes, automatic outlines with holes, report
provenance, compatible navigation and legacy serialization. The native desktop
test uses literal CSV events, actual dialogs and separate Electron windows to
verify these controls, gate counts, undo/redo, reports, IPC validation and restart.
Linux headless proof does not establish Windows/macOS behavior or vendor numeric
parity.

FlowJo documents per-axis compensated/uncompensated parameter selection and
independent duplicate graph windows in its [Graph Window
guide](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-overview). Its
[Derived Parameters guide](https://docs.flowjo.com/flowjo/experiment-based-platforms/plat-derived-overview/)
describes ratio parameters used for display, gating and layouts. These are
workflow references; this implementation retains its explicit documented
scientific conventions.
