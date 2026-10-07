# Desktop plot navigation

Use the arrows above a plot to move to the previous or next sample with the same
population ancestry. The sample selector moves directly to an acquisition in the
selected group and workspace search filter. The up arrow displays the population
that defines the current gate. A first visit uses that gate's parameter
coordinates; a previously visited population restores its remembered view.

Click a population in the breadcrumb bar to return to its last axes, graph mode,
resolution, zoom and 3D camera in this window. Use the child selector, first-child
shortcut or sibling arrows to explore the same gating tree. The last six
ancestors appear as buttons; an earlier-ancestor selector keeps the rest
accessible. **Reset population view** deliberately replaces remembered settings
with the current defining axes and clears zoom and backgating.

Each native window keeps its own history for each sample and population.
Duplicating a window copies that history, then the two histories are independent.
The main desktop view also resumes after reload, workspace switching and restart.
Group and search controls stay with the window when changing populations.
Histories contain display settings, are bounded to 96 populations per popup and
192 in the main window, and never contain event arrays or unsaved gate geometry.

Hold **Shift** when clicking a sample arrow to move the open analysis plots that
share the current workspace and source sample together. Each window retains its
own population, coordinate gate, backgate, axes, graph mode, resolution, palette,
zoom bounds and 3D camera. The main analysis view participates when it displays
that source sample. Other samples, other workspaces and inactive analysis views
remain independent.

Keyboard shortcuts:

- **Ctrl/Cmd+PageDown**: previous matching sample.
- **Ctrl/Cmd+PageUp**: next matching sample.
- **Ctrl/Cmd+U**: defining parent population.
- **Ctrl/Cmd+O**: first child population in workspace order.
- Add **Shift** to a sample shortcut to move matching plots together.

Each plot has a group selector. Group and search selection are included when
duplicating and restoring native windows. Navigation follows workspace sample
order without wrapping, and respects every participating window's group and
search filter. An empty group, an unavailable group or a current sample outside
the filter requires choosing a compatible selection before sample navigation.
Parent population navigation remains available independently of the cohort.

Population, coordinate-gate and backgate references match by their complete
ancestry, with exactly one matching population of the same gate type. A missing
or ambiguous population, missing parameter, differing ratio definition, differing
derived/computed parameter definition or incompatible coordinate compensation
makes a sample incompatible. Sequential navigation skips incompatible samples;
direct selection reports the reason. A coordinated move requires a common target
for every affected plot. It never substitutes all events for a missing population.

X/Y and active 3D Z/color/size transforms are retained explicitly when stepping
between samples. This keeps transformed zoom bounds meaningful when acquisitions
have different saved display scales. **Use current sample scales** clears those
view overrides and zoom bounds. Choosing another axis clears that axis override;
saving a channel or imported coordinate transform clears the corresponding view
override. Gates drawn in the retained view use its actual display transform.
Report plots added from that view retain these transform overrides as well.

The native broker checks unsaved gate editors, partial polygon/drag drawings and
open 3D bounds edits before planning a move. Finish, save or explicitly cancel an
affected drawing first. The same draft flag protects native window closure.
Starting a drawing keeps the canvas in place. If its population, sample or
workspace revision changes, the draft vertices or bounds remain visible and
completion is blocked until cancellation and redrawing against the current view.
It captures each participating window's state
generation, requests an authenticated plan against the workspace revision, and
checks the revision, window membership and generations again before applying
every native descriptor and notifying renderers. A concurrent view change,
workspace edit, opened/closed participant or new dirty editor rejects the move.
Navigation does not write scientific workspace data or create undo entries.

Removed groups remain explicit references in restored windows. Restore the group
with undo or select a replacement group. Source and population removal follow the
same unavailable-reference workflow described in [Native plot windows](PLOT_WINDOWS.md).

The workflow reference is FlowJo's [Graph Window documentation](https://flowjo.com/docs/flowjo10/graphs-and-gating/gw-overview),
which describes sample arrows, a parent arrow and Shift-click coordination.
The keyboard bindings follow its [Shortcut Techniques](https://docs.flowjo.com/flowjo/faq/tech-shortcuts/).

`tests/test_plot_navigation.py` verifies revision/authentication, full ancestry
and ambiguity, cohort intersection, removed references, parameter compatibility,
retained transforms, parent coordinates, remembered views, first visits, sibling
order, stale references and bounded requests.
`tools/desktop_plot_navigation_smoke.mjs` exercises real Electron windows in an
isolated profile: independent and coordinated 2D/3D moves, keyboard/direct
selection, an actual unsaved gate editor, a delayed-plan race, removed-group undo,
IPC rejection and restart recovery. Its target population has ten independently
specified original event IDs out of 100; every streamed XYZ position is checked
against the acquisition and retained view transform. Linux headless execution
uses the explicitly authorized test sandbox exception and software rendering.
`tools/desktop_population_history_smoke.mjs` verifies native breadcrumbs, deep
ancestry, independent histories and duplication, CDF zoom, literal 3D volume
event identities, dirty-drawing and close guards, deleted-source recovery,
retained stale drafts, main workspace switching and restart recovery.
`tools/validate_plot_view_memory.mjs` checks isolation, recency, restoration and
bounded history. Windows/macOS execution and hardware GPU performance remain
unverified.
