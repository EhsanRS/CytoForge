# Desktop acquisition import

Use **Import FCS** in the desktop window to select FCS or numeric CSV files.
The progress dialog shows the current file, dataset and event pass, and provides
**Cancel import**. Initial multipart transfer is indeterminate; subsequent byte
progress measures the local staged copy. Cancelling discards every sample
prepared by that batch. Once **Saving samples** begins, the atomic commit finishes
and the import can be undone.

Each dataset in a chained FCS file becomes an independently selectable sample.
Dataset names include their one-based position; original acquisition keywords,
parameter labels, gain/range/log metadata, spillover and source filenames are
retained. Supplemental TEXT keywords are merged with conflict checks. Original
ANALYSIS keywords are retained under `cytoforge_analysis_…`; they are acquisition
metadata, not automatically applied CytoForge models.

A malformed later dataset removes all arrays staged from that file. Valid
separate files in the same selection still import. The result shows the imported
sample names, event/parameter counts, duplicate messages and per-file errors.

## Numeric interpretation

The decoder supports FCS 2.0, 3.0 and 3.1 list-mode acquisitions:

- IEEE float32 (`F`) and float64 (`D`), with matching parameter widths.
- Byte-aligned unsigned integers (`I`), including mixed 8/16/24/32/40/48/56/64-bit
  parameter widths and both byte orders. Bits above the next power of two of
  `$PnR` are masked as the standard requires.
- Little-endian packed unsigned integers (`I`) with mixed 1–64-bit fields.
  Fields and events share a continuous stream with the least significant bit
  first, following FlowCore's `.readFCSdataRaw` bit interpretation. No padding
  is inserted between parameters or events. Only the final DATA byte can have
  unused bits; those bits are ignored. `$PnR` masking precedes the exact-value
  precision check.
- Legacy fixed-width and delimited ASCII integer data (`A`). Repeated whitespace
  and comma delimiters are accepted in the free format; invalid integer tokens
  and incorrect value counts are rejected.

Zero-event acquisitions remain selectable samples, including ASCII FCS files
whose DATA offsets are zero. They store an empty array with the declared channels.

Binary integer values above `2^53` after masking are rejected when exact
float64 storage cannot be guaranteed. Float data are never range-masked.
Nonfinite values are retained with a count warning; existing plots and
statistics omit them.

Events use float64 acquisition units after Time × `$TIMESTEP`, logarithmic
linearization and gain division, in that order. Time does not use amplifier gain.
Zero logarithmic offset is corrected to one with a warning. Display ranges use
the same conversions. Compensation is stored separately and applied by the
analysis engine; invalid acquisition matrices remain in the source metadata
and produce an explicit warning.

Header/TEXT DATA offsets must agree, except for the standard's large-file zero
header offsets. DATA endpoints are inclusive and lengths must match the declared
event count and widths. Packed DATA length is the declared total number of bits
rounded up to complete bytes. `$NEXTDATA` is relative to the current dataset;
links must move beyond its segments and stay inside the file. Unsupported
histogram modes, big-endian packed integers and mixed-endian words produce
explicit errors. A verified big-endian packed instrument layout is still needed.
No endian guessing or silent offset repair is performed.

CSV uses UTF-8 with optional BOM, quoted headers and standard quoted records.
Headers must be nonempty and unique. Blank records are skipped; malformed widths
and nonnumeric fields report the physical line. A counting pass precedes the
bounded numeric pass, including valid zero-event acquisitions.

## Identity, storage and recovery

Duplicate identity combines original-file SHA-256, dataset position and absolute
dataset offset. Renaming a file does not create acquisitions. If one sibling
dataset was removed, importing the source again restores that sibling and skips
the retained datasets. Legacy single-dataset identities remain compatible.
**Import selection again** explicitly creates new samples for the complete
selection, including identical datasets.

Arrays are written and hashed once in bounded chunks under the experiment's
`imports` directory. Finished NumPy arrays move into the immutable event store
before one revision-checked SQLite snapshot. Progress and complete move intent
are persisted before the commit. On engine restart, interrupted staging and
uncommitted moves are removed; arrays referenced by any undo/redo snapshot are
preserved. Reselect files to retry an interrupted import. It does not automatically
resume at a partial byte position.

Portable projects retain dataset identities, acquisition metadata and event-array
hashes. Original FCS/CSV bytes are not retained after import; their hashes and
filenames are retained alongside the preprocessed arrays.

Current limits are 512 acquired parameters, 150 million values across one FCS
file's datasets, 1 GiB per file, 128 files/4 GiB per selection, 1,024 chained
datasets and 4 MiB per metadata segment. Output-space checks reserve a margin;
write failures remove pending arrays. Event buffers contain at most 262,144
values (2 MiB of float64); metadata and CSV parsing have additional bounded
buffers. Packed decoding gathers at most nine octets per field and carries at
most one byte between chunks. It reads each original DATA byte once, preserving
scientific export hash verification without expanding acquisitions into bit
arrays. Large downstream analyses and FCS exports still need further streaming.

## Validation

`tests/test_imports.py` uses an independent byte encoder to check formats,
endianness, masks, precision, multiple panels, preprocessing, metadata escaping,
malformed chains, cancellation, duplicates, deleted siblings, revision races,
storage failure and restart recovery across undo/redo. Four retained reference
acquisitions match FlowIO's preprocessed event arrays exactly.

`tests/test_fcs_packed.py` checks literal bytes, every width from 1 to 64 at every
bit offset, unaligned 64-bit fields, masks, gain/log/time units, final unused
bits, bounded reads, exact DATA hashing, chained failure rollback, cancellation,
gating, lossless double export and portable project restoration. These packed
cases use independently known integers and a scalar fixture encoder. FlowIO
cannot read packed input; it independently verifies the double-precision export.
FlowCore's published implementation establishes the bit direction; FlowCore
has not been executed as an oracle for these mixed-width packed cases.

`tools/benchmark_fcs_packed.py` imports 131,075, 1,048,579 and 4,194,307 events
with mixed 3/18/27/64/9-bit fields. It verifies every preprocessed value through
an independently computed NumPy file hash, including high unused integer bits,
non-power-of-two ranges and Time/gain conversions. Separate child processes
measure peak RSS before the verification reread. This is source Python on this
Linux host, excluding input generation, multipart transfer, workspace commit,
Electron startup, plotting and other platforms. Evidence is retained in
`artifacts/fcs-packed-benchmark.json`.

`tools/validate_frozen_fcs_packed.py` runs the bundled importer in an actual
frozen child with source import paths removed. It checks complete output hashes,
literal streams, mixed widths across chunks, empty samples, cancellation,
precision/length/endian failures and population identities after SQLite restart.
`tools/desktop_fcs_packed_smoke.mjs` prepares the corresponding packaged file
chooser and individual plot-window check; its native execution is tracked
separately from source and frozen-engine checks.

`tools/desktop_import_smoke.mjs` drives the actual desktop file chooser, imports
three different datasets and a mixed CSV batch, exercises intentional reimport
and undo/redo, cancels during a real three-million-row event pass, exports FCS and
a portable project, restarts with a new engine port and restores the archive.
It then imports a zero-event ASCII acquisition using the native chooser.
`tools/validate_import_exports.py` independently reads that native FCS export
with FlowIO and checks both compensated events and Time units.

`tools/benchmark_imports.py` measures import process peak RSS at increasing FCS
and CSV sizes. Every stored value is checked by an independently generated
float64 file hash. Timings include decode/hash/write/fsync; they exclude input
generation, multipart transfer, SQLite commit, plots and Electron startup.
The independent verification reread is excluded from those timings. Peak RSS
is captured immediately after import, before verification.
The measurements are source Python on this Linux host, not cross-platform or
FlowJo performance comparisons. Evidence is in `artifacts/import-benchmark.json`.

Instrument coverage and external packed instrument truth, big-endian packed
integers, histogram modes, FCS 3.2 including mixed parameter data types,
permissive recovery for nonconforming instruments, original-file retention,
resumable transfer and multi-gigabyte downstream analysis/export remain unfinished.

The interpretation follows the [ISAC FCS 3.1 normative specification](https://www.citometriagic.it/wp-content/uploads/2025/02/FCS_3_1.pdf);
reference preprocessing is compared with the [FlowIO API](https://flowio.readthedocs.io/en/latest/api.html).
Packed bit direction is documented in the
[FlowCore source](https://github.com/RGLab/flowCore/blob/devel/R/IO.R).
