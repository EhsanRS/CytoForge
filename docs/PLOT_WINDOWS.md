# Native plot windows

Double-click a sample or population in the desktop sidebar to open an independent
plot window. **New plot window** beside the plot opens a copy of the current
sample/population, axes, graph mode, settings, resolution, zoom, imported coordinate
basis and backgate. The same button in a popup duplicates that view. Windows can
be moved to another display and resized independently.

Each window has its own sample/population navigation, axes, mode, zoom, pan,
backgate, full-event plot, gate tools and population inspector. Its workspace is
the workspace from which it opened; changing the main application's workspace
does not redirect existing plots. Select another sample or population in the
popup's own sidebar to change that window's source.

Use the plot's previous/next arrows or sample selector to retain its population
and view while changing samples. Shift-click an arrow to move plots from the same
source together; the up arrow displays the defining parent population. Each
window retains its group/search filter and copied display transforms on restart.
Population breadcrumbs remember each population's axes, zoom and camera in that
window; duplicated windows inherit a copy of this history and then keep their own.
See [Plot navigation](PLOT_NAVIGATION.md) for compatibility checks and shortcuts.

Gates and channel transforms are saved scientific workspace data, shared across
windows. Drawing, editing, deleting, undoing or redoing a gate updates the other
windows for that workspace. Axis _selection_ and zoom are independent view
settings; editing a saved channel _transform_ updates the shared scientific
coordinate definition.

Choose **Edit gate on plot** or double-click a visible saved gate to edit its
shape in that window's primary plot panel. The editor previews the full parent
in the saved gate coordinates. Apply or Cancel returns to that window's original
axes and zoom. **Numeric settings** retains the draft and its original revision
when opening the detailed editor. Other windows remain available for comparison.

If a workspace changes while the gate editor is open, the unsaved draft is
retained and saving is blocked. Review the current saved population and choose
**Keep draft and use current workspace** to deliberately apply the retained draft
against the latest revision, or cancel. The engine also checks the expected
revision atomically to reject a concurrent write that happens after this review.
Removing an existing population while it is being edited requires restoring it
before that draft can be saved.

A removed sample, population, coordinate gate, backgate or channel is displayed as
unavailable. The popup retains its original references; it does not display a
different sample or all events as a replacement. Undoing the removal resumes the
original source. Explicitly select a replacement sample/population/channel, or
choose the offered all-events/sample-coordinate action when appropriate.

Use the plot footer's export button for a PNG of the current view. Event and
GatingML export retain the selected population. Closing a window with an open
gate editor, unfinished drawing or 3D bounds editor asks whether to keep editing
or discard unsaved gate changes; closing
the main application checks its own gate editor and every open plot window before
shutting down the shared engine.

Open window descriptions and geometry are atomically stored in
`.config/plot-windows.json` under the runtime directory. Quitting preserves the
open windows and their sample/population, axis, mode, graph settings, resolution and zoom for the
next launch, including when the private engine gets a different port. Closing an
individual window removes it from this recovery set. Window placement is adjusted
to available displays on restore. At most 32 plot windows can be open; the
settings file is bounded to 12 MiB, including 96 remembered population views per
popup and 192 in the main desktop. The main desktop plot also resumes its view
when switching workspaces or restarting. Older version 1 window descriptions
remain readable. Gate drafts require saving or explicitly
discarding before normal application exit and are not stored in this file.
These are desktop session settings; portable scientific project archives retain
the scientific workspace rather than this machine's window geometry.

For the port 8001 progress session, use **Desktop window** in the viewer toolbar
to choose the main workspace or a plot popup. Mouse/keyboard input and screen
captures target the selected actual native window. Closing the selected popup
returns the viewer to the main workspace. The viewer exposes bounded input,
file transfers and window selection, without exposing the private analysis API
or renderer JavaScript execution.

The interaction uses FlowJo's documented double-click and duplicate-graph
workflow as a reference: [FlowJo Graph Window documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-overview).
Contour, zebra, CDF and pseudocolor displays are described in [Graph views](GRAPH_VIEWS.md).
Three-dimensional clouds, camera/settings recovery and volume gates are described
in [3D desktop plots](THREE_DIMENSIONAL.md). Magnetic/tinted gate controls and richer
graph styling remain in the original full scope, alongside Windows/macOS/ARM
release validation.

`tools/desktop_plot_windows_smoke.mjs` exercises real Electron windows in an
isolated local profile. It checks independent view state and duplicated zoom,
popup gate drawing against independently counted CSV events, synchronization,
stale-draft review, undo/redo, unavailable source recovery, workspace pinning,
native close guards, isolation, PNG export, restart recovery and engine shutdown.
`tests/test_workspace_revision.py` verifies the authenticated small revision
observation endpoint, monotonic undo/redo revisions and observation without
workspace deserialization. `tools/validate_desktop_preview.mjs` checks native popup
selection and input through the authenticated viewer using its own synthetic
workspace; existing projects and their undo histories are preserved.
