# Rectangular AutoSpill numerical fixtures

Generated with `source tools/env.sh` and
`uv run --offline python tools/validate_spectral_autospill_reference.py`.
All runtime, cache and generated files remain inside the checkout.

`controls.npz` contains acquired 3-source × 6-detector controls and separately
known latent source intensities. Two fluorophores share detector D1 as their
primary peak; their complete signatures remain independent. The seven cases
cover independent and correlated autofluorescence, outliers, detector noise,
a nonlinear component outside the expected row space, reordered detectors,
and 4096-bin native biex coordinates. Background and inverse detector variances
are explicit. No production CytoForge numerical code is imported by the generator.

`reference.json` records an independent R rectangular adaptation of the paper's
equations, using base R weighted normal equations, the MIT-licensed author Huber
routine pinned at `1e60e86337b297f1dd9ffec6701d8010dd06175b`, and cached native
biex tables with R natural splines. It includes initial/final signatures, source
residuals, convergence traces, detector reconstruction slopes and RMS residuals. Generator,
paper, author regression, input and truth checksums are recorded. This is an
adaptation of the published method, not the original author's rectangular code.

The latent recovery criterion for independent controls is an absolute matrix
error below 0.002 and relative unmixed RMS error below 0.002. Correlated
autofluorescence is deliberately expected to remain biased despite source-slope
convergence. The unexplained component is expected to trigger detector
reconstruction warnings; refinement must not claim to repair the row space.
These checks do not establish FlowJo parity or biological/instrument accuracy.
