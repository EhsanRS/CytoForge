# Kinetics

Kinetics is a desktop analysis platform for time-dependent fluorescence and
derived signals, including ratios. Select a source population from each sample,
choose a common signal parameter, and inspect clocks before calculating. Group
selection maps the current population's exact ancestry; missing or ambiguous
counterparts are reported. A batch contains at most 128 acquisitions.

Each result retains an immutable, event-aligned snapshot of aligned time,
signal and source membership. Calculations use every original event. Saving adds
these three parameters and, optionally, ordinary time-range and responder gates.
The parameters work in plots, derived formulas and ordinary population exports.
The analysis engine runs as a bounded, cancellable desktop worker.

## Clock and source scope

FCS import applies TIMESTEP through FlowIO preprocessing. Verify time units when
it is missing; automatic BTIM/ETIM fallback is not implemented. Set a positive
multiplier and per-sample offset to calibrate and align acquisitions. Signal
compensation is configurable; the time parameter uses uncompensated values.
Derived parameters can serve as signal or time parameters.

When no time channel is available, explicitly select an assumed events/second
rate. Estimated time is original event index divided by that rate, before
gating. It assumes constant acquisition flow and cannot measure pauses or
changes in event rate.

Clock decreases stop calculation by default. Explicit alternatives are pooling
the recorded timestamps or unwrapping resets by adding the previous-to-current
decrease plus the median positive time step. Unwrapping is an assumption, not
instrument clock recovery. Repeated timestamps remain distinct events; missing
time excludes an event from the curve but preserves its original row identity.
Clock inspection considers the complete acquisition before population gating.

Optional display bounds crop the aligned time domain. Otherwise it spans every
finite acquisition timestamp, preserving periods without events from the
selected population. A constant or entirely missing clock produces an undefined
curve. Explicit intervals can still report exact event counts for a constant
clock. Calibration, estimated duration and bin spacing that exceed finite
numeric precision produce an actionable error.

FlowJo's time handling changed across releases; its current release notes name
TIMESTEP as the primary calibration source. These choices should be compared
against the instrument and reference workspace rather than inferred from an
older software version. [FlowJo 10.9 release notes](https://docs.flowjo.com/flowjo/getting-acquainted/10-9-release-notes/10-9-exhaustive-release-notes/).

## Curve values and thresholds

Use 8–4096 equal-width time bins; the default is 256. Each bin retains timed
population, finite signal, selected signal and responder counts, plus unsmoothed
and displayed values. A minimum event count controls whether a value is defined.
The last analysis endpoint belongs to the last bin.

- Arithmetic mean, median and linearly interpolated percentile use finite
  signals. Geometric mean requires all selected signals to be positive.
- Responders have signal **strictly greater** than the threshold; equal values
  are excluded. Thresholds can be absolute or a percentile of finite source
  signals in a baseline time interval. The baseline may precede a cropped
  analysis display and remains part of the reference calculation.
- Responder percentages use all finite signal events in a bin as the denominator.
  Other curve statistics can optionally use responders alone. An empty baseline
  makes responder membership, counts and percentages undefined, distinct from a
  measured zero. Saving responder gates requires a defined threshold.
- Centered moving-average and Gaussian smoothing use an odd window of 1–255 bins.
  Windows are cropped and renormalized at the ends of each contiguous measured
  segment. They do not fill missing bins or bridge gaps. Smoothing weights time
  bins rather than event counts.

The threshold workflow follows the distinction between reference-percentile
thresholds and fractions above a threshold described by
[FlowJo's threshold documentation](https://docs.flowjo.com/flowjo/experiment-based-platforms/kinetics/plat-kin-threshold/).
Our explicit missing-value and boundary rules define CytoForge's calculations;
exact FlowJo numerical parity remains unverified.

## Time ranges, statistics and gates

Define up to 32 named intervals or use the full collection. Adjacent intervals
are half-open: start included, end excluded. The final analysis endpoint is
included. Overlapping intervals are permitted and summarize their events
independently. Counts respect the chosen analysis time domain.

Each interval reports exact event counts and the displayed curve's first maximum
and its time, arithmetic mean across measured bin centers, ordinary least-squares
slope, area and integration coverage. Duration is the requested interval length.
Area integrates a piecewise-linear curve between adjacent measured bin centers,
clipped at interval boundaries. It does not extrapolate to outer acquisition
edges or connect across empty bins. Measured integration duration exposes how
much of the interval contributed area. Undefined and numerically unrepresentable
statistics remain null. These definitions are documented separately from
[FlowJo's interval summary statistics](https://docs.flowjo.com/flowjo/experiment-based-platforms/kinetics/plat-kin-summarystats/).

Suggested time ranges use detected peaks and intervening minima in contiguous
measured curve segments. Suggestions populate a new analysis setup and require
review and recalculation. They are not a validated reproduction of FlowJo's
automatic segmentation.

Saving creates a frozen source-membership gate, interval children and optional
responder grandchildren. The ordinary gate engine uses the three output
parameters and exact endpoint comparisons. Explore opens aligned time versus
signal in the desktop plot. Review confirmation covers all batch samples.

## Reports and model lifecycle

Overlay aligned samples in the desktop time-course chart. Inspect individual
bins with the slider; both raw and displayed values remain visible. Export batch
time-series CSV, interval-statistics CSV, original-event CSV, complete analysis
JSON and standalone SVG. CSV text fields are protected against spreadsheet
formula interpretation. SVG preserves missing segments and interval shading.

Saved time-range metrics are available in custom tables and plate measurements,
including responder percentages, peaks, slopes, areas and measured durations.
Formula columns and follow-replacement policies work with these metrics. Stale
inputs return an explicit missing value unless stale values are allowed.

Rename preserves scientific fingerprints and output identifiers. Replacement
requires the same source populations and interval IDs, updates existing gate
boundaries and responder thresholds, retains stable gate IDs and follows live
table references. Historical reports and arrays remain available. Adding or
removing intervals requires saving a new model. Dependency review and cascade
removal participate in workspace history; removed sources remain visible in
historical reports. Missing source populations are shown explicitly on refit.

Portable projects store separate hashed reports and event arrays. Restore checks
SHA-256, structure and event identity, then reconstructs thresholds, bin values,
smoothing and interval summaries from the event arrays. A rewritten checksum
does not make an altered curve valid. Saved scientific input hashes include
acquisition data, population dependencies, parameter definitions and matrices.

Embedding the specialized curve figure in Layout Studio, automatic missing-FCS
time calibration, instrument reference panels and exact FlowJo comparisons
remain pending. The overall feature scope stays active.

## Validation and performance

The first increment adds 32 independent numerical cases and six durable workflow
cases. They cover analytic slope/peak/area, finite denominators, strict threshold
ties, cropped-baseline references, empty populations, missing time and signal,
nonpositive geometric means, clock resets, event-index time, adjacent endpoints,
missing-bin smoothing, large common offsets, extreme finite values, tampered
arrays and rehashed report corruption. Workflow checks exercise replacements,
live table references, removal dependencies and portable restoration.

Two interface workflows cover aligned batch thresholding, native-style review,
exports, replacement, plot exploration and reset recovery. Source Electron and
the actual AppImage exercise the bundled worker, JSON/SVG/time-series downloads,
exact generated gate counts and restoration across engine port changes. The
standalone engine independently recovers a linear curve with slope 4, area 168,
peak 38 at time 7.5 and 15 strict responders among 24 original events.

The synthetic million-event, 256-bin Gaussian-smoothed benchmark on this host
took 0.144 seconds initially and 0.137 seconds warm for one acquisition; 16
acquisitions totaling one million events took 0.341 and 0.317 seconds. Original
event outputs total 24 MB. Timings exclude event generation, file writing,
validation, HTTP and rendering. They are not biological accuracy or FlowJo
performance comparisons. Evidence is in `artifacts/kinetics-benchmark.json`;
reproduce with `source tools/env.sh` and `uv run python tools/benchmark_kinetics.py`.
