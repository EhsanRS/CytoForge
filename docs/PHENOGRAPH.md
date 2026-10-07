# PhenoGraph community discovery

In the desktop **Discovery** panel, select **PhenoGraph**, input populations and
features. The method builds a Euclidean nearest-neighbour graph, weights each
directed link by the Jaccard similarity of the two neighbour sets, and averages
that graph with its transpose. Zero links and self loops are omitted. This graph
follows the maintained [PhenoGraph kernel](https://github.com/dpeerlab/PhenoGraph).
CytoForge uses igraph's Louvain optimizer, repeats it with recorded seeds and
retains the partition with the best modularity. Different optimizers can produce
different partitions; exact FlowJo or original binary-Louvain parity is unverified.

Set the neighbour count, minimum accepted community size, resolution and restart
count. Community IDs start at 1 and are ordered by size, then by the first fitted
cell. Discarded small communities have label **0**, meaning unassigned. These IDs
are specific to the fitted graph; they do not name biological cell types. The
review shows community counts, graph edges, best modularity and unassigned fitted
events. Graphs without positive Jaccard edges have no accepted communities.

Only fitted events receive labels. The analysis stores their original per-sample
event identities; every other event remains **NaN**, even if it was otherwise
eligible. There is no nearest-class prediction for unfitted events. Balanced and
proportional sampling retain their explicit semantics. Choose the full eligible
event count when it fits the analysis budget and you require labels for all cells.
The current budget is 100,000 fitted events and five million directed neighbour
links, alongside the existing 40-million input-value limit. Larger and streaming
analysis remain open work.

**Use assigned compensation or unmixing** selects the scientific input basis.
Uncheck it for acquired detector values and populations. Display transforms and
fitted-set standardization are separate options. Raw analyses save acquired copies
of their input parents, including Boolean dependencies and captured reviewed QC
flags. Assigning a matrix later preserves the raw analysis and its saved community
memberships. Changing a relevant gate, acquisition or transform still makes the
analysis stale. Community parameters use a linear display scale; **Explore
result** opens their histogram. Saved projects preserve labels, event identity,
parents and settings, and undo removes the added parameters and helper gates.

This supplies a clustering stage relevant to the AF reference workflow described
by [Roet et al. (2024)](https://pure.eur.nl/ws/portalfiles/portal/154751097/Unbiased_method_for_spectral_analysis_of_cells_with_great_diversity_of_autofluorescence_spectra.pdf).
That workflow also uses Opt-SNE and distinct-spectrum review. Automatic AF class
extraction, reference selection and biological validation of the complete workflow
remain unfinished. An accepted community is not automatically an AF reference.

Validation checks the graph against independent set arithmetic, seeded partitions
on known separated populations, duplicate-event self handling and small-community
discarding. Actual analysis workers and API flows check fitted identities, raw
parent preservation, project archives, assignment and undo. These are numerical
and lifecycle checks, not external biological or FlowJo reference truth.

Current source also supports [captured populations](POPULATION_SNAPSHOTS.md) for
reusing reviewed community event identities as acquired compensation or AF
controls. This does not automatically classify a community as autofluorescence.
