import type { ReportIssue, ReportLayout, ReportPlan } from "./types";

export type TemplateBindingKind =
  | "sample"
  | "population"
  | "channel"
  | "group"
  | "compensation"
  | "table"
  | "column"
  | "cell_cycle"
  | "proliferation"
  | "kinetics"
  | "comparison"
  | "comparison_parameter"
  | "plate";
export interface TemplateBinding {
  key: string;
  kind: TemplateBindingKind;
  source_id: string;
  owner_id: string | null;
  name: string;
  population_path: string[];
  details: Record<string, string>;
}
export interface ReportTemplate {
  format: "cytoforge-report-template";
  version: 1;
  source_workspace_id: string;
  definition: ReportLayout;
  bindings: TemplateBinding[];
  sha256: string;
}
export interface TemplateRequest {
  revision: number;
  id: string;
  template: ReportTemplate;
  name: string;
  mappings: Record<string, string | null>;
  batch_scope: "destination" | "template";
  batch_sample_ids: string[];
  review_hash?: string;
}
export interface TemplateOption {
  id: string;
  name: string;
  population_path?: string[];
  details: Record<string, string>;
}
export interface TemplateReview {
  workspace_id: string;
  revision: number;
  review_hash: string;
  can_apply: boolean;
  template_sha256: string;
  bindings: (TemplateBinding & {
    active: boolean;
    target: string | null;
    status: "mapped" | "missing" | "ambiguous" | "unused";
    options_key: string | null;
  })[];
  targets: Record<string, TemplateOption[]>;
  issues: (ReportIssue & { binding_key?: string })[];
  definition: ReportLayout | null;
  plan: ReportPlan | null;
  resets_batch_bindings: boolean;
}
export const templateBindingLabels: Record<TemplateBindingKind, string> = {
  sample: "Sample",
  population: "Population",
  channel: "Parameter",
  group: "Group",
  compensation: "Fixed compensation",
  table: "Saved table",
  column: "Table column",
  cell_cycle: "Cell-cycle result",
  proliferation: "Proliferation result",
  kinetics: "Kinetics result",
  comparison: "Population comparison",
  comparison_parameter: "Comparison coordinate",
  plate: "Saved plate",
};
export const templateParentKinds: Partial<
  Record<TemplateBindingKind, TemplateBindingKind>
> = {
  population: "sample",
  channel: "sample",
  column: "table",
  comparison_parameter: "comparison",
};
export function templateOptionLabel(option: TemplateOption) {
  return option.population_path?.join(" / ") || option.name;
}
