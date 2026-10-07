# Captured populations

Select a sample or population in the desktop analysis view and choose **Capture
population** in its inspector. Name the snapshot and choose current population
coordinates or acquired detector coordinates. The saved population contains the
exact selected event identities, including the selection through all parents and
Boolean operands. It appears at the sample root and can be renamed, recolored,
complemented, combined with Boolean gates, or used as a parent for new geometry.

The captured selection stays fixed when its source gate is edited or deleted,
compensation is assigned, or discovery results change. Its provenance retains the
source scientific definitions and capture time. Plot it on acquired parameters if
the original computed axes are no longer available. A snapshot is specific to its
original acquisition; copying it as geometry to a different sample is rejected.

PhenoGraph community gates ordinarily depend on computed parameters. Capture a
reviewed community to make it reusable in the median spectral control wizard or
AutoSpill. Its control measurements use the original acquired detector values.
Several snapshots can provide separate named AF controls. A discovered population
still needs experimental review before being treated as an AF reference.

Membership uses one packed bit per acquired event plus a NumPy file header. Its
descriptor pins the original sample, acquired-file SHA-256, event count, detector
order, membership SHA-256 and selected count. Event arrays remain untouched.
Corrupted membership data, padding outside the event count, changed acquisitions,
foreign samples and inconsistent descriptors are rejected. Cached descendants and
Boolean populations also check their captured dependencies before returning masks.

Snapshots survive undo/redo, restart and portable `.cytoforge` projects. Shared
membership files are archived once and verified before the imported workspace is
created. Failed imports remove their owned files. Standard GatingML cannot encode
an arbitrary list of event identities; use gated FCS/CSV or a portable project to
retain the population.

The capture API is `POST /api/workspaces/{workspace_id}/gates/capture` with
`revision`, `sample_id`, optional `gate_id`, `name` and `compensated` (default
`true`). A revision conflict leaves the workspace unchanged. Capture and geometry
editing remain distinct operations.

Current validation uses generated acquisitions and actual analysis workers. It
checks PhenoGraph control reuse, compensation changes, archive integrity and
history. Native interaction and biological AF reference selection remain to be
validated. This supplies saved controls for the discovery workflow; automatic
distinct-spectrum ranking and Opt-SNE remain unfinished.
