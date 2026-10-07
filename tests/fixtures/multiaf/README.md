# Independent numerical checks for multiple AF references

Four known sources (two fluorochromes and two AF reference spectra) produce eight
acquired detectors. Both fluorochromes peak in D1; the AF spectra peak in D5 and
D7. Inputs include electronic backgrounds and unequal positive detector weights.

`tools/validate_multiaf_reference.py` creates exact, independent AF, correlated AF,
detector noise, reordered detector, reordered source and alternate biexponential
lookup cases. It invokes the retained independent base R rectangular adaptation
and the original MIT-licensed AutoSpill Huber fit, pinned to commit
`1e60e86337b297f1dd9ffec6701d8010dd06175b`. Production CytoForge code is not imported
by the generator. Base R QR projection measures each AF reference's angle from
the joint span of every other source; production uses SVD projection.

`reference.json` pins raw inputs, truth, driver, generator and author fit hashes.
The tests compare initial signatures, refined matrices, residual slopes,
convergence, detector reconstruction and AF diagnostics, and recover the known
latent intensities from exact controls. Source-role metadata does not distinguish
biological AF classes automatically. Correlated AF can bias an estimated stain
signature even when source residuals converge.

This is synthetic numerical validation. These fixtures provide no FlowJo,
instrument or biological reference truth, and no automatic AF clustering.
