// Deterministic synthetic cells, corner debris and a matching unstained control.
// Shared by browser and native UI validation; scientific reference truth is in
// tests/fixtures/autospill and was generated independently with the author's R code.
export function autospillFixtures(events = 1400) {
  const detectors = ["D1", "D2", "AF", "FSC-A", "SSC-A"];
  const matrix = [
    [1, 0.18, 0.04],
    [0.09, 1, 0.1],
    [0.35, 0.2, 1],
  ];
  let state = 47123;
  const uniform = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return (state + 0.5) / 4294967296;
  };
  const normal = () =>
    Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
  const files = [
    "D1 single stain",
    "D2 single stain",
    "Unstained cells",
    "Mixture",
  ].map((name, control) => {
    const rows = Array.from({ length: events }, () => {
      const af = Math.exp(Math.log(350) + 0.7 * normal());
      const truth = [0, 0, af];
      if (control < 2)
        truth[control] = Math.exp(Math.log(4500) + 0.8 * normal());
      if (control === 3) {
        truth[0] = Math.exp(Math.log(1500) + 0.5 * normal());
        truth[1] = Math.exp(Math.log(2000) + 0.5 * normal());
      }
      const signal = [70, 35, 25].map(
        (background, j) =>
          background +
          truth.reduce((sum, value, i) => sum + value * matrix[i][j], 0) +
          8 * normal(),
      );
      return [...signal, 60000 + 5000 * normal(), 28000 + 2500 * normal()];
    });
    if (control < 3)
      for (let i = 0; i < 400; i++)
        rows.push([
          300 + 900 * uniform(),
          300 + 900 * uniform(),
          300 + 900 * uniform(),
          1200 + 160 * normal(),
          700 + 100 * normal(),
        ]);
    return {
      name,
      rows,
      csv: `${detectors.join(",")}\n${rows.map((row) => row.join(",")).join("\n")}\n`,
    };
  });
  return { detectors, matrix, files };
}
