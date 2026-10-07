# Three-dimensional desktop plots

Select **3D** in a main or popup plot, then choose X, Y and Z. Drag the cloud to
rotate, Shift-drag or use the pan tool to move it, and scroll to zoom. Arrow keys
rotate a focused plot; `+` and `-` zoom; `0` resets the camera. **Reset 3D view**
also restores automatic axes. Double-clicking the cloud resets the camera.

Color and point size can each follow a parameter. Cube, labels, opacity, point
size and compensation controls are independent in every native window. **New
plot window** copies the complete view, including six axis limits, camera and
backgate. Quitting restores open native views on the next launch. A population
with three explicit gate dimensions opens on its XYZ coordinates.

## Counts, coordinates and volume gates

Population counts include every selected source event. Finite XYZ counts require
all three coordinates to be finite; a missing color or size value does not remove
an event. Missing color uses a neutral shade; missing size uses the midpoint.
Viewport counts separately describe events within the six current axis limits.
Robust automatic axes can omit rare tails; **Graph settings → Axis extent → All
finite events** includes the full finite coordinate range.

**All events in view** draws every finite event within those limits, without a
marker cap. Turning it off enables the graph marker limit and deterministic
uniform display sampling with seed 45. Sampling changes the drawing and keeps
population, finite and viewport counts intact. Camera zoom can further clip
projected markers without changing the six axis limits or scientific population.

**3D bounds / box gate** accepts transformed minimum and maximum values for
each axis. Apply them to clip the display, or create a three-dimensional volume
gate. Three different axis parameters are required for a box gate. The gate
editor retains the coordinate transforms, fixed/sample/acquisition compensation
references and imported ratio definitions. Its membership uses the full event
coordinates, includes each minimum and excludes each maximum. Display precision,
rotation and marker sampling never define the gate mask. Matching box gates
appear as projected wireframes; other saved gate types still filter the full
population but do not yet have a 3D surface overlay.

## Rendering and reports

The private scientific engine sends revision-bound binary chunks of at most
65,536 events. Each 32-byte record contains normalized XYZ/color/size float32
values, an original uint64 event ID and a backgate flag. A changed workspace,
coordinate basis or population invalidates the stream; malformed or mixed
chunks cannot mark the plot ready. Rotation, pan, camera zoom and glyph changes
reuse the loaded buffers. The scientific arrays retain their full precision.

Three.js uses WebGL2 when it is available. The software renderer draws the same
complete point set if WebGL2 is unavailable or its context is lost. The footer
identifies the renderer in use. Both paths use orthographic projection and source
order with alpha blending, rather than opaque depth occlusion. Very large clouds
can require substantial graphics and process memory; no implicit downsampling
is applied. This server's headless desktop verifies the software path. An explicit
attempt with default GPU settings also used software, so hardware WebGL2
performance and shader rendering remain unverified here. No unsafe graphics
fallback switches are enabled.

PNG export composites the cloud, cube, axis labels and matching volume gates.
Layout Studio retains XYZ, camera, color/size and glyph settings, provides their
editing controls, and exports vector points to SVG and native PDF. Overlay
layers share XYZ transforms/limits and color/size scales. Portable projects keep
these definitions. Attached manifests record source hashes, coordinate bases,
scalar transforms, counts, sampling, camera clipping and the SHA-256 of the
ordered original event IDs. Full-event vector exports can be large.

## Verification and remaining scope

`tests/test_three_dimensional.py` checks literal event IDs and finite masks,
missing scalar values, exact volume/backgate masks, chunk boundaries, explicit
sampling, cache reuse, imported ratios/fixed compensation, camera mathematics,
empty/constant/extreme inputs, stream authentication/revision guards, shared
report scales and portable report/project round trips.

`tools/desktop_three_dimensional_smoke.mjs` exercises actual desktop windows,
independent cameras, synchronized volume gates, six-limit copies, malformed
native requests, PNG export, restart recovery and a full 65,553-event stream.
The graph-report smoke produces five native PDF pages; the independent PDF
validator checks vector paths and attached XYZ/camera/event-ID provenance.
`tools/benchmark_three_dimensional.py` measures source preparation and bounded
encoding for one million synthetic events, excluding network and drawing.

Hardware GPU/large-cloud stress, synchronized group/population sample stepping,
additional 3D gate surfaces, richer legends and styling, saved 3D backgate report
layers and Windows/macOS/ARM verification remain in the full active objective.
Discovery model calculations still have their separately documented dimensional
limits; a 3D view can display any three available parameters.

Workflow reference: [FlowJo 3D controls](https://docs.flowjo.com/flowjo/experiment-based-platforms/3d-viewer/plat-3d-controls/).
Renderer reference: [Three.js WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer.html).
