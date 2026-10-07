# Native spider populations

Choose **Spider gates** or press **S** in a two-dimensional plot, then click to
place the shared center. Review the name and geometry and choose **Create
population** to create Q1–Q4 together. The tool is available in both the main
desktop and independent native plot windows.

Use **Edit gate on plot** on any member to edit the entire family. Drag the center
to move all four populations; drag an arm handle to rotate that boundary. The
preview shows every member's count using all parent events. Arrow keys move the
focused handle by one pixel, or ten with Shift. Crossing or collapsing adjacent
arms displays an error and retains the last valid draft. **Numeric settings**
retains the draft and exposes the shared center, four angles and saved X/Y scales.

Saving changes every member in one revision and one undoable operation. Member
IDs, descendants, individual names and colors remain intact. Other native windows
refresh their counts while retaining their own axes and zoom. Cancel leaves the
saved family unchanged. Concurrent workspace edits retain the draft and block
Apply until deliberate review. Propagation gives each target sample its own
family ID; deleting a member removes its family and dependents, and Undo restores
their exact definitions. Portable project export and normal desktop restart keep
the complete scientific geometry.

## Scientific definition

The populations are four **unbounded angular sectors** in their ordered, saved
scientific coordinates. Their rays continue beyond the viewport. Changing zoom
or resizing a window never crops the population or changes membership. Each axis
retains its transform, compensation reference and ratio definition, including
two axes with the same measured-parameter label and distinct scientific inputs.
The initial X/Y scales come from the drawing viewport and are then saved with the
geometry. Angles turn counterclockwise after dividing X/Y displacement by those
scales. Numeric scale changes therefore change the boundary angles in native
coordinates and are applied to every member.

Starting with the right arm, the counterclockwise sectors are Q2, Q1, Q4 and Q3.
Every finite event in the parent belongs to exactly one member. The shared center
belongs to Q2; arms 1–4 belong to Q2, Q2, Q1 and Q3 respectively. These rules match
ordinary quadrants when the arms are right, up, left and down, and remain fixed
when the arms rotate. Nonfinite transformed events belong to no sector, so the
sum of frequencies can be below 100% if the parent contains those events.

Classification aligns floating-point exponents before computing cross-product
signs, avoiding overflow at opposite coordinate extremes. Near cancellation it
resolves the sign with exact rational arithmetic on the saved float coefficients
and original coordinates; no angular tolerance turns neighboring events into
boundary events. Saved members share one bounded, immutable uint8 label cache.
Draft previews use a separate engine and cannot replace the saved revision's
cached membership. Display and vector reports clip rays to their viewport while
scientific membership uses the complete, unbounded geometry.

## Verification and remaining scope

`tests/test_spider_gates.py` contains 20 independent science/API cases covering
cardinal and rotated/reflex sectors, nonfinite and extreme coordinates, events
one float step from an arm, cache reuse, read-only previews, linked edits,
propagation/delete/undo, duplicate-label fixed compensation/ratio coordinates,
portable projects, exact CSV/FCS population export and viewport clipping.
`tools/desktop_spider_smoke.mjs` passes ten native Linux x64 workflows, including
actual center and arm drags, popup synchronization, crossing rejection, numeric
handoff, stale drafts, ordered scientific axes and restart recovery. It uses the
explicit isolated headless test sandbox exception with hardware GPU disabled.

`tools/benchmark_spider.py` measures classification and complete draft preview
requests on one million independently labelled events, and verifies that the
workspace/history remain unchanged and that each saved family caches labels once.
The result is `artifacts/benchmark-spider-preview.json`. These host-local timings
exclude socket transport and native rendering; host activity and OS caches are
uncontrolled.
On this host, five requests after the first measured a median of 419 ms for all
four preview counts and their parent plot. A cold saved-family mask evaluation
took 241 ms; its shared label array used 1,000,000 bytes. Every result matched the
independent labels, and workspace/history data remained unchanged.

Standard GatingML export currently reports an explicit unsupported-format error
for unbounded spider geometry. A portable project retains it fully; selected
event CSV/FCS export uses exact scientific membership. Faithful spider WSP import,
GatingML interoperability, vendor numerical and real-acquisition comparisons,
3D boundary surfaces, normal sandbox startup, physical input and Windows/macOS/ARM
validation remain required. Native curly controls are described in
[Curly quadrants](CURLY_GATES.md); their vendor/interchange comparisons and every other retained FlowJo
feature, instrument and performance requirement remain in the full project scope.

The interaction follows the shared-center and movable-arm workflow documented in
[FlowJo's spider gate documentation](https://www.flowjo.com/docs/flowjo10/graphs-and-gating/advanced-gates1/spider-gate).
