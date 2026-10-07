import type { PlateDefinition, TableColumn, Workspace } from "./types";
import { id } from "./types";
import { newTableColumn } from "./components/TableBuilder";
import { plateDimensions, platePosition, wellName } from "./plateGeometry";
export {
  plateShapes,
  plateDimensions,
  plateGeometryLabel,
  rowName,
  wellName,
} from "./plateGeometry";
export const numericColumn = (column: TableColumn) =>
  column.kind !== "metadata" || column.metadata_numeric;
export function freshPlate(): PlateDefinition {
  return {
    id: id(),
    name: "New plate",
    plate_key: "",
    format: 96,
    assignments: {},
    annotations: {},
    columns: [{ ...newTableColumn(), decimals: 0, population_path: [] }],
    compensated: true,
    aggregate: "median",
    missing_replicates: "strict",
    view: {
      mode: "heatmap",
      primary: null,
      secondary: null,
      category_keyword: "",
      category_source: "tags",
      category_colors: {},
      domains: {},
    },
    provenance: {},
  };
}
export interface PlateSession {
  plate: PlateDefinition;
  baseline: string | null;
  baseRevision: number;
}
export const plateSessionKey = (workspace: string) =>
  `cytoforge.plate-draft.${workspace}`;
export function parsePlateSession(content: string): PlateSession | null {
  try {
    const cached = JSON.parse(content);
    if (cached?.plate) {
      const [rows, columns] = plateDimensions(cached.plate);
      for (const items of [
        cached.plate.assignments,
        cached.plate.annotations,
      ]) {
        for (const well of Object.keys(items ?? {})) {
          const [row, column] = platePosition(well);
          if (
            row >= rows ||
            column >= columns ||
            well !== wellName(row, column)
          )
            return null;
        }
      }
    }
    if (
      cached?.plate &&
      /^[a-f0-9]{32}$/.test(cached.plate.id) &&
      Array.isArray(cached.plate.columns) &&
      cached.plate.columns.length > 0 &&
      cached.plate.columns.length <= 10 &&
      cached.plate.columns.every(
        (c: TableColumn) =>
          typeof c.name === "string" &&
          /^[a-f0-9]{32}$/.test(c.id) &&
          c.formula_refs &&
          c.population_overrides &&
          Number.isInteger(c.decimals) &&
          c.decimals >= 0 &&
          c.decimals <= 12 &&
          ["tags", "metadata"].includes(c.metadata_source),
      ) &&
      typeof cached.plate.name === "string" &&
      typeof cached.plate.plate_key === "string" &&
      cached.plate.assignments &&
      Object.values(cached.plate.assignments).every(
        (ids) => Array.isArray(ids) && ids.every((v) => typeof v === "string"),
      ) &&
      cached.plate.annotations &&
      Object.values(cached.plate.annotations).every(
        (v) =>
          v &&
          typeof v === "object" &&
          Object.values(v).every(
            (text) => text === null || typeof text === "string",
          ),
      ) &&
      cached.plate.view?.domains &&
      ["heatmap", "split", "categories", "faces"].includes(
        cached.plate.view.mode,
      ) &&
      ["tags", "metadata"].includes(
        cached.plate.view.category_source ?? "tags",
      ) &&
      Number.isInteger(cached.baseRevision) &&
      cached.baseRevision >= 0 &&
      (cached.baseline === null || typeof cached.baseline === "string")
    ) {
      return {
        ...cached,
        plate: {
          ...cached.plate,
          view: { ...freshPlate().view, ...cached.plate.view },
        },
      };
    }
  } catch {
    /* Malformed cached data is not a scientific definition. */
  }
  return null;
}
export function initialPlateSession(workspace: Workspace): PlateSession {
  const saved = workspace.plates[0];
  try {
    const cached = parsePlateSession(
      sessionStorage.getItem(plateSessionKey(workspace.id)) ?? "null",
    );
    if (cached) return cached;
  } catch {
    /* A malformed or inaccessible cache does not replace saved work. */
  }
  return {
    plate: structuredClone(saved ?? freshPlate()),
    baseline: saved ? JSON.stringify(saved) : null,
    baseRevision: workspace.revision,
  };
}
