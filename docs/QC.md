# Acquisition quality control

CytoForge implements a reviewable **robust-bin acquisition method**. It measures
all events, retains original event identities and never cleans a sample merely
because a diagnostic job completed. This method is not PeacoQC, flowCut or flowAI,
and published performance claims for those algorithms do not apply to it.

## Workflow

Open **Acquisition QC**, select a sample and source population, and choose signal
markers. Select an acquired time channel when available. Signal checks can use
sample compensation and channel transforms; time, detector saturation and pulse
shape always use acquired values. Optional area/height pulse inspection does not
establish a doublet identity.

Run the background diagnostic job. Inspect acquisition rate and marker traces;
marker traces show the median and 10th/90th percentiles. Click or keyboard-select
intervals, or use the interval table, to choose exclusions. Independently choose
whether undefined marker values, invalid/decreasing time, saturation and pulse
outliers should be excluded. Categories overlap; the displayed counts count each
event once. Recommendations can all be cleared, and unflagged intervals can be
selected manually.

The diagnostic report and event CSV include the current review draft, before it
is applied. **Create reviewed populations** saves a retained population and a
complementary rejected population. Both support descendants, plots, statistics,
backgating and FCS/CSV export. **Update reviewed populations** changes their
choices while preserving IDs and descendants. Portable projects preserve the
diagnostics, scientific snapshot, choices and hashed event identity array. Reviews
are undoable/redoable. Restored projects can revise reviews without the original
job directory. A completed job can be applied after unrelated/cosmetic edits when
its scientific fingerprint still matches; the current revision remains required.

Projects containing QC use archive version 2: diagnostic JSON reports and event
arrays are separate hashed entries, so repeated runs do not bloat the 32 MiB
manifest. Version 1 remains readable, including its inline QC reports. Limits are
1,000 saved QC results per workspace and 32 MiB per diagnostic JSON report; the
overall archive import limits remain 4 GiB compressed and 8 GiB expanded.

## Method, version `robust-bins-1`

Bins partition the original acquired event order, not the selected population's
order. Effective bin size is `max(requested_bin_events, ceil(N / 1024))`.
Final partial bins remain explicit. Signal quantiles use the finite events of the
source population in each acquired bin. A channel is scored only if at least eight
bins contain `min_bin_events` finite source events; quantiles of smaller bins are
still reported. No event sampling is used for these measurements or membership.

Each of the bin's 10th, 50th and 90th percentiles is compared with that percentile's
median over eligible bins. Let `D` be the median within-bin 90th–10th percentile
spread and `s` the requested score threshold. The score denominator is the maximum
of `1.4826 * median(abs(q - median(q)))`,
`signal_min_shift * D / s`, `abs(median(q)) * 1e-12` and `1e-12`.
An interval is suggested when any of its marker quantile scores exceeds `s`.
Rescaled arithmetic prevents valid large finite values from overflowing quantile
interpolation or deviations. Unrepresentable diagnostic numbers remain undefined.

Time is never sorted or reconstructed. Nonfinite/negative time and each event
whose time decreases from its valid predecessor receive time flags. Intervals
crossing invalid time or a reset have undefined rates and are suggested for review.
For a complete interval `[start, end)`, rate is acquired event count divided by
`Time[end] - Time[start]`. The final interval uses the last timestamp plus the
median positive acquisition time step as its estimated endpoint. Repeated
timestamps are reported; intervals spanning fewer than three median positive
steps have undefined rates when timestamps repeat. This avoids attributing
precise rates to a coarse time clock. Increasing bin size can resolve this limit.

Rate scores require at least eight measurable bins containing at least 50
acquired events each. The logarithms of their rates use the same median/MAD score
with a scale floor of `log(rate_min_fold) / s`. A suggested rate interval must
exceed `s` and differ from the median rate by at least `rate_min_fold`. Rates use
all acquired events, so source population frequency changes do not fabricate
changes in instrument rate. Positive time gaps exceeding 20 times the median
positive step, resets and their first 100 event IDs are recorded as diagnostics.
Gaps alone do not automatically remove events.

Saturation is a raw acquired detector threshold, `value >= range * fraction`.
FCS ranges already reflect acquisition gain/log/time preprocessing. CSV/synthetic
ranges may be display estimates and are identified as such in the report.
Saturation exclusions are independently optional, not part of interval scores.

Pulse inspection uses `log(area) - log(height)` for positive finite acquired
pulses. Median/MAD scoring uses a minimum log-ratio shift of `0.15`, divided by the
requested pulse score threshold to set its scale floor. Invalid/nonpositive
pulses and ratio outliers are reported separately. The scatter preview shows at
most 2,000 evenly spaced finite source events with original event IDs; pulse
flags are calculated on all source events. Biological size, shape and debris can
alter pulse ratios. Inspect a manually validated singlet population before
treating these flags as doublet candidates.

## Identity, integrity and stale inputs

The saved `N × 2` uint32 array holds event flags and bin ID for every original
event. An outside-source flag preserves the exact population that was measured.
Reviewed gates can shrink with a subsequently narrowed parent but cannot gain
unmeasured events when that parent grows. Retained/rejected selection partitions
the original source, intersected with the current gate parent.

The scientific fingerprint includes acquired event hash/order, source gate
dependencies, relevant derived/computed/explicit-compensation inputs, signal
transforms when used, detector ranges and method/settings. Colors, names and
annotations do not affect it. Changed inputs mark diagnostics stale and prevent
creating/revising reviewed populations until QC is rerun. Existing reviewed event
identities remain usable; reports expose staleness. Downstream analyses track QC
choices and the event-array hash. Cleanup identities cannot be propagated to a
different sample or represented exactly by GatingML; use per-sample QC, gated
FCS/CSV, or portable projects.

Workers share the durable analysis queue, cancellation and interrupted-job
recovery. Engine caches are bounded at 256 MiB. Flags, masks and scientific
vectors still require memory proportional to the acquisition; large compensation
arrays are currently computed in memory. This is not a streaming QC engine.

## Validation and limits

`tests/test_quality.py` checks exact known signal/rate disturbances, pulse and
saturation identities, overlapping exclusions, time quantization/resets/constant
and coarse clocks, sparse/empty populations, partial tails, large finite values,
hash/count/bin corruption, historical fingerprints, stale inputs, cancellation,
portable restoration, review revision and undo/redo. A 283,969-event instrument
fixture's measured quantiles are independently verified with sorted order-statistic
interpolation. This verifies measurement and identity on that file, **not** its
biological QC accuracy. Browser tests exercise review, draft exports, plotting,
archive revision and stale-apply prevention. Frozen engine and desktop checks are
recorded separately; source tests alone do not establish packaged behavior.

`tools/benchmark.py` measures a synthetic million-event acquisition with 16
markers plus time, compensation and transforms. Two known disturbances contain
40,000 events. Precision, recall, wall time and actual effective bin size are
recorded in `artifacts/qc-benchmark.json`. These host-specific measurements do not
establish performance on all instruments or superiority over another application.

The majority-stable baseline can miss gradual drift, anomalies affecting most of
a file, or changes invisible to marker quantiles. Rare population/composition
changes can produce recommendations even when the instrument is stable. Drift and
large suggested removal fractions are reported for review. No validated external
reference QC truth dataset has yet established sensitivity/specificity across
instruments, mass or spectral cytometry. Automated peak tracking, isolation-tree
cleaning, advanced singlet models and batch QC heatmaps remain open requirements.

Published comparison methods and their validation should be consulted separately:

- [PeacoQC paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC9293479/)
- [PeacoQC implementation](https://github.com/saeyslab/PeacoQC)
- [PeacoQC evaluation code](https://github.com/saeyslab/PeacoQC_evaluation)
- [FlowJo flowCut plugin](https://www.flowjo.com/exchange/plugin/flowcut)

The instrument fixture retains its original source and redistribution notices in
`tests/fixtures/interchange/README.md`. No external QC implementation was copied
or bundled for this method.
