# Spillover spreading review

In the desktop app, save a conventional or spectral matrix, then open
**Compensation → Spillover spreading**. Choose one single-color control and an
acquired parent population for each included primary output. An autofluorescence
output can use an unstained control. Outputs without controls have no primary row;
they still appear as secondary columns.

The calculation applies the selected matrix to the acquired detector data,
independently of any matrix assigned to the control sample. Spectral matrices use
their saved background subtraction and inverse-variance detector weights. Parent
populations use acquired coordinates, including Boolean and ratio gates. Reviewed
QC populations are accepted when their dependencies and saved flag data are valid.
Raw and QC data checksums are verified before calculation and before saving.

The worker shows progress and supports cancellation. Review the matrix and click a
cell to see both regressions. Save the reviewed report with its matrix to retain it
across desktop restarts and project archives. Saving does not change matrix
coefficients or assign the matrix to samples. Undo restores the previous report.
Coefficient, output, detector, weight, background or control population changes
mark the report stale and prevent saving a pending result. Renaming samples,
matrices or geometric gates does not invalidate the scientific calculation.
Reopening a saved report restores its controls, quantile settings, event limit and
significance threshold. **Reuse controls and settings** copies a reviewed run's
settings into the form before recalculation.

Export **Matrix CSV** for the coefficient table or **Full report JSON** for settings,
control identities, checksums, counts, quantile measurements and regression
diagnostics. A diagonal blank means self-spreading was not estimated. A zero means
the fitted secondary coefficient was non-positive or did not pass its F-test.
JSON and response headers identify exports of stale results.

## Published method

The implementation follows the Methods section, “Linear models for estimation of
SSM,” of [Roca et al., Nature Communications (2021)](https://doi.org/10.1038/s41467-021-23126-8).
It is an independent implementation of the published AutoSpread equations.

1. Apply compensation/unmixing in linear intensity units. Partition each control
   by primary intensity into equal-count quantile bins, using stable event order
   for ties. Use up to 256 bins and at least eight. The number decreases to retain
   the requested minimum events per bin (default 100).
2. For each bin, measure the median primary intensity `F`, the secondary median,
   and robust secondary standard deviation `sigma = percentile84 - median`.
3. Define `f(x) = sign(x) * (sqrt(abs(x) + 1) - 1)`. Evaluate it as
   `x / (sqrt(abs(x) + 1) + 1)` to preserve small values.
4. Fit `sigma = baseline + beta * f(F)` by ordinary least squares with an
   intercept. This first regression estimates noise at zero primary intensity.
5. Fit `f(sigma² - baseline²) = coefficient * f(F)` through the origin. Retain
   signed adjusted variances; never clip them to zero before fitting.
6. Set a non-positive coefficient to zero. Also set a coefficient to zero if its
   one-term F-test is not significant at the recorded threshold (default 0.05).
   The second regression uses one numerator and `bins - 1` denominator degrees
   of freedom, without a multiple-testing correction.

The published description leaves bin population minima and tie handling to the
implementation. Those choices are recorded here and in each report. A negative
fitted baseline is retained for the published squared-baseline adjustment and
flagged for review. Secondary median regression diagnostics flag strong residual
signal trends that may indicate imperfect compensation or autofluorescence.

## Verification and limits

Seven deterministic synthetic cases, including 15 controls, have a separately
implemented base-R oracle in `tests/fixtures/autospread`. The oracle uses R `lm`,
type-7 quantiles and independent weighted normal equations for rectangular spectral
matrices. Production uses scaled OLS and a weighted pseudoinverse. Tests compare
coefficients, baseline estimates, significance, quantile medians, robust deviations
and adjusted deviations. The fixtures cover baseline correction, negative primary
intensities, zero spreading, negative slopes, non-significant fits, adaptive bin
counts and weighted spectral backgrounds.

This verifies the published numerical method on synthetic inputs. It does not
establish biological accuracy, instrument equivalence or exact agreement with
FlowJo's binary AutoSpread implementation. Controls must be on-scale and within
the instrument's linear range. Correlated fluorescence/autofluorescence and
unresolved secondary signals can bias either spreading estimator. Coefficients
also depend on intensity units and matrix normalization; compare them only on
consistent instrument scales.

Up to 64 output parameters and 512 acquired detectors are accepted. A single
control may retain at most 64 million output values. An explicit event limit uses
uniformly spaced acquired event IDs and records the selected identity checksum;
the default uses every finite selected event. Dense diagnostic reports can increase
workspace and project size. Hardware/native interaction, external biological
datasets and closed-source FlowJo comparisons require separate validation.

Rectangular spectral **AutoSpill estimation** remains pending. This spreading
workflow accepts existing rectangular spectral matrices calculated by the
single-stain estimator or entered manually.
