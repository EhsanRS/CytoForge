# Population comparison

The desktop workbench has a population-comparison platform with background calculations,
reviewed saving, independent native comparison windows, and publication/table integration.
The latest additions are in source. API, native window and installer checks for this revision
remain pending because the current managed environment denies local network sockets.

## Compare distributions

Open **Population comparison**, choose actual acquisitions and populations for targets and
controls, then define the measured coordinates. Each coordinate has its own transform and
correction definition; ratio axes are supported. Control populations are pooled by original
event identity, so overlapping gates from one acquisition contribute each event once.

Run the background job, inspect its diagnostics and counts, and save the reviewed result.
Saving adds a result record in one undoable operation. Original event arrays are retained.
Calculations use all selected finite events, with explicit counts of excluded nonfinite events.
Joint distributions use events finite in every selected coordinate.

Results include exact empirical-CDF KS distances, cumulative Overton subtraction,
enhanced Dmax, the published ENS-1 correction, and probability-binning statistics.
Joint probability bins use median splits along the coordinate with greatest raw variance;
whole tied values stay together. Requested bin counts and achieved counts are recorded.

Independent control baselines leave out an entire acquisition. Shared target/control events
make independent KS probability unavailable. Ties and other assumptions are reported.
Event probabilities describe distributions of measured events; biological replicate tests
belong in the saved statistics-table comparison tools.

## Large comparisons and cancellation

Joint calculations store transformed coordinates once per acquisition in temporary column
files. Overlapping target and control gates reuse those columns and retain their original
event identities. Partitioning reads one coordinate at a time, including every selected event
that is finite in all selected coordinates. It does not copy a full joint matrix for each gate,
pooled cohort or recursive partition.

Raw acquisition mappings are released after copying the needed coordinate. Univariate
vectors are released after each coordinate. Control baseline partitions are reused for gates
that exclude the same acquisition; the cache holds at most eight partition trees.

Staging uses checked ordinary file writes. Each acquisition opens a read-only mapping
after all coordinates have been flushed successfully. Storage checks allow 256 MiB
headroom and reserve physical blocks where the operating system supports it. When
allocation is unsupported, unwritten acquisitions share the available-space budget.
Quota, disk-space, write and flush failures fail the job and remove temporary columns.

Successful and failed calculations close mappings before removing temporary files.
Cancellation, engine shutdown and detected worker crashes remove owned comparison scratch
and unpublished `.npz.partial` result files after the worker exits. Published `.npz`
artifacts remain available for results and undo. Cleanup checks the owning store and job
identities. Restart alone does not establish that an older worker has exited.
The 43 focused storage cases include quota/disk errors, short writes, allocation fallbacks,
mapping cleanup and actual result writes interrupted by cancellation, shutdown or a crash.
Evidence is in `artifacts/pytest-comparison-storage-failures.xml` and its log.
Large acquisitions still need memory for event identities, masks and one-dimensional
statistics. Operating-system mapping behavior and acquisition layout also affect peak memory.

The reproducible synthetic benchmark compares dense and temporary-column joint calculations
and then runs the complete comparison calculation on the same three acquisitions. It checks
exact partitions, every joint bin count and statistic, overlapping controls, independent
acquisition baselines, original source bytes and workspace preservation. Run it with:

```bash
source tools/env.sh
uv run --offline python tools/benchmark_population_comparison.py
```

The default uses one million events and both 16 and 64 coordinates. Generated acquisitions
and scratch are removed after the run; evidence remains in
`artifacts/benchmark-population-comparison.json`. Peak RSS includes imports, source loading
and staging in fresh Linux/macOS worker processes. Temporary staging has a measurable time
cost. These measurements cover the Python calculation; native rendering, IPC, vendor truth
and platform performance require their own checks.

The Linux reference run used one million total events, two target populations and
three control populations across three acquisitions. Full calculation peak memory
includes imports, original acquisition reads, all univariate effects and joint statistics:

| Coordinates | Before coordinate-lifetime cleanup | Current full calculation | Current full time |
| ----------- | ---------------------------------- | ------------------------ | ----------------- |
| 16          | 358 MiB                            | 232 MiB                  | 23.97 s           |
| 64          | 735 MiB                            | 392 MiB                  | 91.96 s           |

The separate 64-coordinate joint-storage benchmark used 1,626 MiB with dense arrays
and 392 MiB with column files. It took 9.51 s and 13.38 s respectively, including
preparation; the temporary-column path spent part of that time staging the data.
These are individual synthetic runs on this host, rather than cross-platform or
vendor performance measurements. Exact joint results and original source bytes matched.
The current checked-write run also reproduces every joint count, statistic and partition
from the earlier mapping-write run; evidence is in
`artifacts/population-comparison-disk-safety-benchmark-equivalence.json`.

## Windows and figures

**Open comparison window** creates an independent desktop window with its own target,
parameter, histogram/CDF/difference mode, tints, smoothing and vertical difference scale.
Window presentation survives restart and does not edit the scientific comparison.
CDF accumulation is linear in the number of bins. Smoothing affects display only.

SVG exports retain the chosen colours, Gaussian smoothing rule, individual-control overlays,
coordinate positions, signed zero line and difference scale. Statistics CSV and the JSON
report retain numerical results and provenance. Scientific source bytes and saved count
artifacts are checked before export.

In **Layout**, add a saved comparison population. Choose its parameter and presentation in
the inspector. Figures support the existing SVG/PNG/PDF report pipeline and its review rules.
Batch mapping resolves actual populations; a mapped population that was never compared is
explicitly unavailable. Comparison controls remain the cohort from the saved result.
The report manifest records its input snapshot, artifact digest, actual target and settings.
Historical results require the report's snapshot policy; current-only exports reject them.

## Statistics tables and history

Add a **Population comparison** table column and choose the saved result, coordinate or joint
distribution, and metric. Population rows retain actual gate identities, including duplicate
gate names. Sample rows with several compared populations require an explicit mapping.
Choosing a saved result supplies an acquisition override only when that acquisition has one
compared target population. Unavailable and stale values have explicit cell status.

Columns support table formulas, CSV/XLSX export and report tables. **Follow reviewed refits**
selects the retained replacement chain; fixed bindings preserve the original result.
Rename is cosmetic. Removal reviews all later refit branches and affected table/figure
bindings. Affected cells become unavailable or follow the previous retained refit as shown in
the review. Undo restores result records, bindings and original immutable artifacts.

## Evidence and remaining work

Tests cover scientific references, source/count integrity, overlapping controls, finite-event
selection, historical/refitted values, exact population mapping, portable spreadsheets,
publication batch mapping and reviewed removal/undo. Frontend geometry is compared directly
with export geometry across 108 combinations of mode, smoothing, scale, individual controls
and ordinary/extreme/subnormal coordinates.

The focused memory/source checks are recorded in
`artifacts/pytest-population-comparison-memory-focused.log`. API workflows can also run through
the application's real ASGI routes, middleware, lifespan and background workers without a
listening server, using `pytest --in-process-asgi`. The test client keeps worker callbacks
moving on the caller's event loop. Ordinary pytest retains the usual Starlette test client.
Request signatures normalize typed numeric defaults before worker serialization; tests cover
minimal parameter requests, saving, renaming, table export and archive restoration.
The full current Python suite passes 1,086 tests with this transport, including 363
HTTP cases and 723 other cases. Evidence is in
`artifacts/pytest-population-comparison-disk-safety-full-asgi.xml` and its log.

An isolated Linux engine candidate is under `artifacts/candidates/population-comparison/engine`.
`artifacts/frozen-population-comparison-validation.json` verifies actual frozen workers at
2 and 64 coordinates, bundled NumPy/SciPy and model modules, independent dense joint scores,
the known 44% ENS reference, source integrity and temporary-file cleanup. Two additional
frozen jobs verify low-space and quota failures, the reported job error, unchanged original
events/workspace and absence of published or partial artifacts. Its source test
driver uses the frozen-worker spawn arguments and removes project/Python import paths from
the children. The listening engine, desktop renderer and installer remain separate checks.
The released AppImage is unchanged.

`artifacts/population-comparison-publication-example.svg` is explicitly synthetic.
The source checks and typecheck/build logs do not prove vendor equivalence, native platform
parity, real instrument truth, native renderer/GPU performance, or a newly validated installer.
Broader instrument/biological references, larger cohorts, full native regression and packaging
remain part of the active goal. Socket restrictions still prevent validating a listening
private engine and the desktop progress viewer in this managed session.

## Scientific references

- [FlowJo population comparison](https://flowjo.com/docs/flowjo10/experiment-based-platforms/population-comparison)
- [FlowJo univariate comparison](https://flowjo.com/docs/flowjo10/experiment-based-platforms/plat-comparison-univariate)
- [FlowJo probability binning](https://flowjo.com/docs/flowjo10/experiment-based-platforms/plat-comparison-probabilitybinning)
- [Roederer et al., univariate probability binning (2001)](https://herzenberglab.stanford.edu/sites/g/files/sbiybj27506/files/media/file/lah484_0.pdf)
- [Roederer et al., multivariate probability binning (2001)](https://herzenberglab.stanford.edu/sites/g/files/sbiybj27506/files/media/file/lah485.pdf)
- [Bagwell, Journey Through Immunofluorescence Analysis](https://www.vsh.com/publication/JourneyThroughImmunofluorescenceAnalysis.pdf)

ENS-1 follows the published correction. Proprietary vendor adjustments are not inferred.
