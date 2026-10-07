# Portable event exports

Choose **Export events** below a native plot. The dialog starts with the plotted
population and lets you choose another population from the same sample. Select
FCS or CSV and stored measurements, compensated/unmixed values, or display scale
values. **Prepare export** stages a file without changing samples or workspace
history. Review its event count, parameter count, correction and origins, then
**Save event file** opens native saving. Cancelling a save keeps the prepared file
available for another attempt. Cancel in the export dialog discards it.

FCS uses the standard FCS 3.1 double-precision DATA type, 64 bits per parameter,
little-endian byte order, and zero logarithmic acquisition scaling. The writer
uses byte offsets for UTF-8 TEXT, escapes delimiters, aligns DATA, and moves DATA
offsets into TEXT when they exceed the eight-digit HEADER fields. It preserves
finite float64 measurements, signed zero, and nonfinite values. CSV writes the
numeric values with float64 round-trip precision and protects formula-like
column names.

Stored measurements export the acquired columns and assigned correction. A raw
spillover basis is also written as standard `$SPILLOVER`. CytoForge's checked
metadata preserves spectral detector weights, background, virtual display
settings, tags and scientific source snapshots. Compensated/unmixed export
materializes all available parameters once, including formulas and fitted
outputs; it carries no correction assignment. Display scale export additionally
materializes each parameter's display transform and uses linear output displays.
Earlier materialization remains in the source history when a saved file is
exported again.

Merged files preserve complete source snapshots, source/population/channel
mapping, keyword codebooks and exact event origins. `CF_Source`, `CF_EventID`
and declared keyword category columns remain integral in every measurement
mode. Selected populations keep their original event IDs and source indices;
source counts and offsets are recomputed, including zero-count sources. Import
rebuilds a separate uint64 origin array and verifies its digest, source counts,
event ranges and ordering. Event IDs above float64's exact integer range are
rejected instead of rounded. FCS metadata and DATA have separate SHA-256 checks;
these detect corruption and are not signatures of authorship.

Preparation writes at most 16,384 event rows per numeric chunk and stages the
DATA body on disk. It does not allocate a second complete event matrix. Population
masks and existing fitted-model calculations retain their usual memory and
cooperative cancellation behavior. Two writers can run at once; four running or
ready exports can be open. Cancelling cleans staged files before reporting
completion. Restart recovers completed files and removes interrupted writes.
Reopen **Export events** for the same sample and choose **Resume FCS export** or
**Resume CSV export** to save or discard a recovered preparation. Removed samples'
prepared copies are discarded when another export starts; their immutable event
data and undo history are preserved.

Scientific source bytes and fitted dependencies are checked before and after
preparation and again before downloading. The exact workspace revision must
still match. The desktop main process accepts only validated workspace/export
identifiers, supplies its private local engine token, and streams the file to
disk through Electron. The renderer never receives the engine token through the
save bridge or allocates a whole-file Blob. The saved file's size and hash are
checked before reporting success. Files in an active download are leased so
cancellation waits for its reader to close before deleting a prepared copy.

Reimport restores display settings, annotations, current correction and merged
history. Corrupt files contribute no samples or origin files. Publication uses
the existing revision-checked import transaction; undo/redo and native restart
retain immutable events and origins. Other FCS readers can read the standard
measurements and spillover; CytoForge-specific history and spectral settings
require a reader that understands the additional metadata. CSV carries numeric
rows only. Neither export creates copies of the original gates as editable gates
in another sample; their definitions remain in scientific source history.

The independent million-event fixture checks every exported value and origin
against generated truth, uses FlowIO as a separate reader, then reimports and
checks the correction and provenance. Timings in
`artifacts/benchmark-event-export.json` exclude fixture creation, independent
reader verification, HTTP, native download and disk-cache control. Native
development and AppImage checks cover save cancellation/retry, binary precision,
CSV, reimport, stale revisions, bridge validation, independent windows and restart.
Linux x64 headless testing does not establish interactive platform or instrument
parity. Virtual group concatenation and additional FlowJo workflows remain open.

Format behavior follows the original
[ISAC FCS 3.1 specification](<https://flowcytometry.smhs.gwu.edu/sites/g/files/zaskib311/files/2021-11/Data%20File%20Standard%20for%20Flow%20Cytometry(1).pdf>).
Independent reading uses the documented
[FlowIO FCS reader](https://flowio.readthedocs.io/en/latest/api.html).
