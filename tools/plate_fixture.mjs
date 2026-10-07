// Independent, deliberately unequal acquisition sizes distinguish equal-sample
// medians from pooled-event medians. The zero and invalid wells exercise missingness.
export function plateFixtures() {
  return [
    { name: "Plate first", well: "A1", values: [1, 2, 3, 4] },
    { name: "Plate replicate", well: "A01", values: [100, 200] },
    { name: "Plate second", well: "A02", values: [10, 20, 30, "NaN"] },
    { name: "Plate zero", well: "B1", values: [0, 0, 0] },
    { name: "Plate invalid", well: "A0", values: [7, 8] },
  ].map((sample) => ({
    ...sample,
    csv:
      "X,Y\n" +
      sample.values.map((value, index) => `${value},${index}`).join("\n"),
    tags: {
      "WELL ID": sample.well,
      "PLATE ID": "Plate P1",
      Treatment: "original",
    },
  }));
}
