# DNA cell-cycle modeling

CytoForge implements the Dean–Jett–Fox (DJF) model for an individually selected
population or a batch of samples sharing a DNA parameter. Each sample is fitted
independently under the same requested constraints. A saved model keeps its
settings, scientific input fingerprint, histogram, components, residuals,
diagnostics, software versions and original event probabilities.

This is an original numerical implementation, not a claim of bitwise agreement
with a particular FlowJo version. FlowJo reference workspaces, biological truth
panels, full Watson support, sub-G1/super-G2 models, bivariate DNA/labeling models
and integration into configurable tables/layouts remain open requirements.

## Method and numerical choices

The phase model has Gaussian G0/G1 and G2/M peaks. Between their means, the
underlying S-phase distribution is quadratic. Each underlying DNA value is
broadened by a Gaussian whose SD is that DNA value multiplied by the G1 CV.
An optional, truncated Gaussian wave augments the underlying S distribution for
synchronized populations, before measurement broadening.
These model assumptions follow [Fox (1980)](https://doi.org/10.1002/cyto.990010114)
and [Dean and Jett (1974)](https://doi.org/10.1083/jcb.60.2.523).

The implementation integrates Gaussian probability over each histogram bin;
Gauss–Legendre quadrature integrates the underlying S distribution. Its order
increases with the minimum allowed G1 CV. A nonnegative Bernstein quadratic has
endpoint coefficients b0,b2 >= 0 and middle coefficient
b1 >= -sqrt(b0*b2). Unlike a mixture of three positive basis coefficients, this
also permits a nonnegative quadratic with a negative middle coefficient.
The quadratic is normalized analytically. The optional S wave is normalized
within the interval between the two peak means.

Three deterministic starts reduce sensitivity to local minima. The default
objective is Poisson deviance; weighted least squares is also selectable.
Every bin, including empty bins, contributes. The three conditional mixture
weights sum to one. Component probabilities are normalized within the fitted
DNA range; they do not imply an estimated frequency in excluded tails.

DNA is fitted in linear intensity units using the requested sample compensation.
Display transforms do not enter the DNA model. Source gates retain their own
compensation and transform definitions. Positive scaling before range selection
and optimization avoids overflow for large finite intensities. Every source
event contributes to the histogram; there is no model downsampling.

Automatic range selection starts at zero and ends at the lesser of the maximum
value and 1.05 times the 99.9th percentile. Explicit range limits override it.
All nonfinite, negative and out-of-range exclusions are counted and retained as
undefined output rows. At least 200 fitted events and 16 occupied histogram
bins are required. Counts below 1,000, broad/weak peaks, truncation, large
residuals, parameter boundaries and optimizer failure produce review warnings.

## Constraints and diagnostics

Peak means and CVs accept fixed values, bounds and initial guesses. The G2/G1
ratio can be fixed or bounded, and either peak CV can be linked to the other.
The optimizer's parameterization satisfies coupled mean/ratio constraints
exactly. Incompatible constraints fail before fitting. Automatic CV bounds are
0.5–20 percent and automatic ratio bounds are 1.5–2.4; explicit supported bounds
are 0.3–40 percent and 1.2–3 respectively.

RMSD is reported in events per histogram bin. Poisson deviance, Pearson
chi-square, nominal degrees of freedom, optimizer convergence, component mass
captured by the fit range, parameter boundaries and Jacobian rank are retained.
For an identifiable, converged DJF fit away from boundaries, local covariance
gives approximate fraction standard errors. These are conditional on the chosen
model, histogram and independent-count assumptions. Undefined uncertainty stays
undefined. Warnings and residuals require review; a small RMSD does not prove
that a source population is biologically valid.

## Fractions, event probabilities and populations

Three quantities are deliberately separate:

- **Model fraction:** normalized fitted component weight within the DNA range.
- **Expected events:** sum of conditional phase probabilities over fitted events.
- **Assigned events:** actual events whose largest phase probability selects that
  phase; ties prefer G0/G1, then S, then G2/M.

An event receives the conditional probabilities of its full-data histogram bin.
Four float32 columns, in original acquisition order, contain G1/S/G2 probability
and the assigned phase (1/2/3). All unfitted rows are entirely NaN. Optional
workspace populations select the assigned phase; they remain restricted to the
original fitted events when their current parent later grows.

This distinction between overlap-aware statistics and discrete event selection
also appears in [FlowJo's univariate statistics documentation](https://flowjo.com/docs/flowjo10/experiment-based-platforms/cell-cycle-univariate/plat-cc-statistics).
DNA alone does not separate G0 from G1 or G2 from mitosis. A reviewed singlet
population and a suitable stoichiometric DNA stain are required for biological
interpretation.

## Persistence, jobs and exports

Cell-cycle jobs share the durable two-worker queue and cancellation/parent
monitoring behavior of Discovery and QC. Successful jobs require explicit fit
review before workspace application. Revision conflicts and scientific input
changes block application; cosmetic changes and DNA display transforms do not
invalidate an otherwise unchanged fit. Downstream scientific fingerprints track
the SHA-256 of computed probability columns.

Saved probabilities are immutable and continue to represent original events
when inputs change. The UI marks such fits stale and supports refitting from
their settings. Undo/redo restores model definitions, columns and populations.
Removing an input sample preserves a saved batch's historical model and marks
its original scientific inputs stale.

Saved-model rename preserves parameter identifiers and scientific compatibility
while updating report and automatic population labels. Refit and replace keeps
the previous immutable fit, rebinds the existing four output columns and preserves
population IDs, descendants, formulas and report links. Replacement requires the
same samples and source populations, active output bindings and no input that
depends on the replaced outputs. A dependency preview lists the transitive effect
of removing a model before the user selects removal of dependent objects. That
review is checked against the current dependency fingerprint and workspace
revision. Historical downstream analyses remain available with staleness.

Exports include JSON provenance, all-sample statistics CSV, full event-identity
probability CSV and standalone SVG curves/residuals/statistics. Computed
probabilities and assigned phases also participate in normal FCS/CSV population
exports, formulas, plots and statistics.
Version-two project archives store hashed model reports separately from the
manifest and include every event-aligned probability array. Restore verifies
report fingerprints, array hashes, dimensions, probability normalization,
assignment consistency and phase counts before committing the workspace.
Existing version-one and QC version-two projects remain readable.

## Scientific validation and the Watson discrepancy

Tests generate latent biological DNA and apply independent measurement noise,
then compare recovered fractions, means and CVs with the original labels.
They cover asynchronous quadratic and synchronized S distributions. An
independent adaptive quadrature calculation verifies the production S
convolution, including a negative Bernstein middle coefficient.
API and browser workflows verify constraints, exclusions, identities, exports,
source changes, archive restore and undo/redo. These simulations establish
numerical/model recovery for their assumptions; they do not establish real
biological accuracy or FlowJo parity.

The experimental CDF/moment implementation intended for
[Watson, Chambers and Smith (1987)](https://doi.org/10.1002/cyto.990080101)
drifted in independently labeled simulations despite plausible curves and
internal convergence. It is unavailable through both the job API and the UI.
The failing interpretation is retained in the scientific module with an explicit
experimental marker for investigation. A regression requires rejection of that
method until the discrepancy has been resolved. The requested full Watson
workflow remains unfinished.

An additional primary reference is the MATLAB fitter supplied as Supplementary
Code 6 with [Blasi et al. (2016)](https://www.nature.com/articles/ncomms10256).
That code performs Gaussian refitting after empirical S subtraction, rather
than the moment updates used in our prototype. Its use of MATLAB `erf` needs
review against the original published equations, and its G2 threshold
calculation references G1 quantities. These are
observations from a code audit, not established corrections to the published
algorithm. The source is retained in the local reference cache for investigation;
it has not been adopted as independent proof of the unavailable implementation.

`tools/investigate_watson_refinement.py` compares the blocked moment prototype,
Gaussian refitting with its CDF interfaces, and experimental residual-based S
subtraction. It draws independently labelled beta, quadratic, narrow-wave and
mixed-wave S populations. Each mixture is also repeated with the true peak
means and CVs fixed, solely to distinguish peak-fitting errors from phase
allocation errors. The results retain failures, nonconvergence and internally
converged fits that still miss the known fractions. The recovery threshold is
1.7 percentage points; probability normalization alone cannot pass it.

The original publication's complete equations have not been verified against
these interpretations. The investigation and supplementary code are therefore
research evidence, not proof of a faithful Watson implementation. Results are
written to `artifacts/watson-refinement-investigation.json`; none of these
experimental refinements removes the API or UI guard.
