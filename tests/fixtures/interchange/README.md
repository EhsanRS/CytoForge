# Interchange reference fixtures

These files were copied without modification from the primary FlowKit repository,
commit `f2159043b6a56e527d4baacf97490caf8354618e`:
https://github.com/whitews/FlowKit/tree/f2159043b6a56e527d4baacf97490caf8354618e/data

- `gatingml/` comes from `data/gate_ref`: ISAC GatingML conformance documents,
  event data, and independent event-membership truth files. The attribute-testing
  XML is an intentionally invalid document. The second ellipse has no truth file
  in the repository and is compared with FlowKit's independent implementation.
- `flowjo/` contains the simple-line, diamond, and selected eight-color workspace
  fixtures plus their event data. Diamond transform counts are also asserted
  against the published FlowKit tests; eight-color masks are compared event by
  event, including nested and Boolean gates and an ellipse.

ISAC's original notices permit free distribution and read-only use and reserve
modification and other rights. The fixtures and schemas are retained unchanged.
FlowKit's BSD-3-Clause notice is in `licenses/FlowKit-BSD-3-Clause.txt`.
See `SHA256SUMS` for the exact fixture bytes copied into this checkout.

The eight-color workspace's stored FlowJo counts differ from the counts computed
by both CytoForge and FlowKit. The tests preserve that finding in the import
report. Agreement with these references does not establish full FlowJo parity.
