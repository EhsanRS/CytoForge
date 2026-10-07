# Dye-dilution proliferation models

CytoForge fits independently acquired sample populations with a shared generation
labeling reference. The workbench supports an undivided control or a manual
generation-zero intensity, optional unstained background calibration, lognormal
or Gaussian dye peaks, constrained dye partitioning and variability, full-event
generation probabilities and precursor-weighted statistics. Review plots,
residuals, overlap diagnostics, settings and controls before saving populations.
Reports and event identities can be exported before committing a fit.

Generation labeling depends on the reference. A nearly absent undivided peak
cannot be identified from peak order alone. The interface requires a control or
an explicit manual anchor. Controls can use their own gated populations and
calibration intensity range. Source samples are fitted separately; they are not
pooled into one mixture. Compensation is optional and display transforms do not
change the physical fluorescence model.

## Defined model and numerical integration

For generation `g`, the dye signal is `D_g = (M_0 - B) r^g`, where `B` is the
background mean, `M_0` the undivided center and `r` the dye partition ratio. The
default ratio is fixed at 0.5. Free or bounded ratios are supported between 0.25
and 0.75. Dye CV can be fixed or fitted between 0.5% and 100%; default free bounds
are 5%–80%. The default number of generations is configurable through G12.

A lognormal dye peak has median `D_g` and log-scale standard deviation
`sqrt(log(1 + (CV/100)^2))`. A Gaussian dye peak has mean `D_g` and standard
deviation `D_g CV/100`. Independent normal background with mean `B` and standard
deviation `s_B` is added to the dye. For the lognormal model, `M_0` is background
plus the dye median; with nonzero background spread it need not equal the median
of the complete measured distribution. The Gaussian model combines the dye and
background variances analytically.

The lognormal/background convolution integrates the narrower random variable
using 512-point Gauss–Legendre quadrature over nine standard deviations in each
direction. Bin masses use stable differences of distribution functions. Tests
compare these masses against independent adaptive integration over low and high
CVs and background spreads. Intensity scaling avoids overflow for very large
finite input values. Integrated peak shapes are reused when only mixture
fractions change.

All component masses are normalized within the fit range. Fractions and reported
precursor statistics are therefore conditional on finite selected events in that
range. Automatic ranges include theoretical peak support as well as observed
quantiles; explicit ranges are available. Logarithmic histograms exclude
nonpositive fluorescence and report that exclusion. Linear histograms can retain
negative values when the background distribution accounts for them. A range
excluding a requested generation is rejected. At least 200 eligible events and
eight occupied bins are required.

Fractions use a bounded simplex parameterization that permits absent generations.
Three deterministic bounded least-squares starts optimize Poisson deviance by
default; weighted least squares is also available. Diagnostics include fit
convergence, residuals, RMS discrepancy, captured mass, adjacent-generation
overlap, active bounds and Jacobian rank. Local standard errors are reported only
for converged, identifiable, interior solutions. They condition on controls,
generation count and fit range and exclude control-calibration uncertainty.

This explicitly defined implementation differs from FlowJo's proprietary fitter
and the published FlowMax model. The latter's log-intensity peak equations and
optimization procedure are not claimed here. Its discussion of peak ambiguity
and autofluorescence informed the validation scope. See
[Shokhirev and Hoffmann (2013)](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0067620).
The reference-and-review workflow is consistent with
[FlowJo's proliferation model controls](https://flowjo.com/docs/flowjo10/experiment-based-platforms/proliferation/plat-prolif-modeladjust).

## Controls and interpretation

An unstained control estimates background using its median and a normal-consistent
median absolute deviation. A negative compensated median is modeled with a zero
background mean while preserving the estimated spread, and is reported. This
normal approximation needs review against the selected control population.
Manual background mean and standard deviation are also available.

The undivided control is fitted as one generation with Poisson bin counts and
the selected dye/background distribution. Its report retains event counts,
calibrated center/CV, convergence, captured mass and goodness of fit. It can fix
the undivided center, fix center and CV, or supply starting values for both. A
broad or poorly described control triggers a warning. Calibration is not a joint
fit of all controls and experimental samples.

These models assume binary divisions and adequately stable dye retention. They
do not correct for selective cell death, uptake, dye loss, asymmetric sampling or
acquisition bias. Singlet and viability populations must be appropriate. Strongly
merged late generations may be unidentifiable despite a good total curve.
Instrument panels, external biological truth and exact FlowJo fit comparisons
remain required validation work.

## Explicit statistics and event identities

For model counts `N_g`, precursor equivalents are `p_g = N_g / 2^g`. Define
`P = sum(p_g)`, `R = sum(p_g, g >= 1)`, `D = sum(g p_g)`, `T = sum(N_g)` and
`T_divided = sum(N_g, g >= 1)`. CytoForge reports:

| Statistic | Definition |
|---|---|
| Precursor frequency / percent divided | `R/P` / `100 R/P` |
| Division index | `D/P` |
| Proliferation index | `D/R` |
| Expansion index | `T/P` |
| Replication index | `T_divided/R` |
| Collected divided-event fraction | `T_divided/T` |

Proliferation and replication indices are undefined when there are no responding
precursor equivalents. Precursor equivalents describe the collected modeled
population and are not absolute original culture counts. Tests independently
check these definitions against a published worked example and known division
histories. See [FlowJo's proliferation platform](https://flowjo.com/docs/flowjo10/experiment-based-platforms/proliferation).

Three quantities stay distinct: fitted model counts, posterior expected counts
over observed events, and discrete counts from each event's largest generation
probability. Posterior probabilities are evaluated at each event's histogram bin;
they are not an unbinned likelihood. Full event-aligned float32 arrays contain
G0–Gmax probabilities and a zero-based assigned generation. Excluded events have
NaN in every output. Hard assignment is performed after float32 rounding so it
agrees with the saved probabilities. Populations use these discrete assignments,
while precursor statistics use fitted model counts.

## Saved models, refitting and portability

Proliferation and cell-cycle models share saved-model controls. Renaming updates
report and automatic population labels while preserving parameter identifiers.
Refit settings creates a separate model. Refit and replace retains the previous
report and immutable arrays, rebinds the existing output parameter names to the
new fit, and preserves population IDs, descendants, formulas and layout links.
Replacement requires the same sample/population scope, the same output dimension
and active original bindings. Inputs or controls cannot depend on the replaced
outputs. Changed scope or generation count requires a separate model.

Removal first lists affected formulas, populations (including descendants and
Boolean references), layout plots and table selections. Applying that reviewed
closure removes the selected model and dependent objects; retained historical
analyses expose their input compatibility. A review fingerprint rejects changed
dependencies. Undo restores the previous workspace and its retained arrays.

Scientific fingerprints include acquisition hashes, source/control gates,
compensation and computed/formula dependencies. Cosmetic names and display
scales do not make a physical dye fit stale. Workers check actual input hashes.
Draft reports and saved fits support JSON, batch statistics CSV, full event
probability CSV and standalone SVG with histogram, components and residuals.
Outputs also work in normal plots, gates, formulas, statistics and FCS/CSV exports.

Version-two project archives store reports at `proliferation/<id>.json` and arrays
at `proliferation/<id>/<sample-id>.npy`, with SHA-256 checks. Restore validates
report structure, statistics, probability normalization, assigned generations,
event/count agreement and hashes before committing. Earlier archive versions
remain readable. Each platform permits 1,000 saved models and a 32 MiB report;
batch fits support up to 128 sources. Histogram fitting is bounded, but all
batch output arrays are currently held in worker memory before writing, so
very large batches still require streaming improvements.

`tools/benchmark_proliferation.py` records million-event fits with independently
sampled latent generation labels, with and without background spread. Evidence
is saved in `artifacts/proliferation-benchmark.json`. These synthetic recovery and
performance measurements do not establish biological accuracy or FlowJo parity.
