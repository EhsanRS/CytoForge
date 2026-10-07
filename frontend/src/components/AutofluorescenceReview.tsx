import { reviewedAFReferences } from "../autofluorescence";

export function AutofluorescenceReview({
  rows,
  outputs,
}: {
  rows?: unknown;
  outputs: string[];
}) {
  const reviewed = reviewedAFReferences(rows, outputs);
  if (reviewed === null)
    return (
      <p role="status">
        AF separation data is invalid. Recalculate to review these references.
      </p>
    );
  if (!reviewed.length) return null;
  return (
    <section className="control-diagnostic">
      <h3>AF reference separation</h3>
      <p className="form-note">
        Each AF signature is compared with all other reference sources using the
        detector weights. A small separation angle means its signal is difficult
        to distinguish from combinations of the other sources. Use matching
        unstained populations and inspect both extraction and spreading.
      </p>
      <div className="control-matrix-scroll">
        <table className="control-matrix">
          <thead>
            <tr>
              <th>AF output</th>
              <th>Closest source</th>
              <th>Weighted similarity</th>
              <th>Separation angle</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {reviewed.map((row) => (
              <tr key={row.output}>
                <th>{row.output}</th>
                <td>{row.closest_output ?? "—"}</td>
                <td>{row.weighted_cosine?.toFixed(4) ?? "—"}</td>
                <td>{row.separation_angle_degrees.toFixed(2)}°</td>
                <td>
                  {row.weakly_separated
                    ? "Weak separation"
                    : "Separated reference"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
