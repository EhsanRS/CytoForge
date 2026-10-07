import type { PlateDefinition, PlateGeometry } from "./types";

export const plateShapes = {
  6: [2, 3],
  12: [3, 4],
  24: [4, 6],
  48: [6, 8],
  96: [8, 12],
  384: [16, 24],
  1536: [32, 48],
} as const;

export function rowName(row: number) {
  let letters = "";
  for (let index = row + 1; index > 0;) {
    const digit = (index - 1) % 26;
    letters = String.fromCharCode(65 + digit) + letters;
    index = Math.floor((index - 1) / 26);
  }
  return letters;
}
export const wellName = (row: number, column: number) =>
  `${rowName(row)}${String(column + 1).padStart(2, "0")}`;

export function platePosition(value: string): readonly [number, number] {
  const match = /^([A-Za-z]{1,2})[\s_:-]*0*([1-9][0-9]{0,2})$/.exec(
    value.trim(),
  );
  if (!match) throw new Error("Choose an alphanumeric plate well.");
  let row = 0;
  for (const letter of match[1].toUpperCase())
    row = row * 26 + letter.charCodeAt(0) - 64;
  return [row - 1, Number(match[2]) - 1];
}

export function validPlateGeometry(value: unknown): value is PlateGeometry {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const geometry = value as PlateGeometry;
  return (
    Object.hasOwn(value, "rows") &&
    Object.hasOwn(value, "columns") &&
    Object.keys(value).every((key) => ["rows", "columns"].includes(key)) &&
    Number.isInteger(geometry.rows) &&
    Number.isInteger(geometry.columns) &&
    geometry.rows >= 1 &&
    geometry.rows <= 96 &&
    geometry.columns >= 1 &&
    geometry.columns <= 96 &&
    geometry.rows * geometry.columns <= 1536
  );
}

export function plateDimensions(
  plate: Pick<PlateDefinition, "format" | "geometry">,
): readonly [number, number] {
  if (plate.format === "custom") {
    if (!validPlateGeometry(plate.geometry))
      throw new Error("Custom plates require valid row and column dimensions.");
    return [plate.geometry.rows, plate.geometry.columns];
  }
  if (plate.geometry != null || !Object.hasOwn(plateShapes, plate.format)) {
    throw new Error("The plate format and dimensions do not agree.");
  }
  return plateShapes[plate.format];
}

export function plateGeometryLabel(
  plate: Pick<PlateDefinition, "format" | "geometry">,
) {
  const [rows, columns] = plateDimensions(plate);
  return `${rows * columns} wells · ${rows} × ${columns}`;
}
