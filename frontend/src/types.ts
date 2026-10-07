export type TransformKind =
  | "linear"
  | "log"
  | "logicle"
  | "hyperlog"
  | "asinh"
  | "gml_linear"
  | "gml_log"
  | "gml_asinh"
  | "wsp_log"
  | "wsp_biex";
export interface Transform {
  kind: TransformKind;
  cofactor: number;
  t: number;
  w: number;
  m: number;
  a: number;
  offset?: number;
  positive?: number;
  negative?: number;
  width?: number;
  top?: number;
  bound_min?: number | null;
  bound_max?: number | null;
}
export const linear: Transform = {
  kind: "linear",
  cofactor: 150,
  t: 262144,
  w: 0.5,
  m: 4.5,
  a: 0,
};
export interface Channel {
  name: string;
  label: string;
  range: number;
  transform: Transform;
}
export interface ImportDataset {
  file: string;
  name: string;
  sample_id: string;
  dataset_index: number;
  dataset_count: number;
  event_count: number;
  channel_count: number;
  skipped: boolean;
}
export interface ImportResult {
  workspace: Workspace;
  imported: number;
  errors: { file: string; message: string }[];
  warnings: { file: string; message: string }[];
  datasets: ImportDataset[];
  cancelled: boolean;
  import_id: string;
}
export interface ImportSession {
  id: string;
  status:
    | "ready"
    | "receiving"
    | "reading"
    | "committing"
    | "succeeded"
    | "cancelled"
    | "failed"
    | "interrupted";
  stage: string;
  file: string;
  file_index: number;
  file_count: number;
  bytes_received: number;
  bytes_total: number;
  dataset_index: number;
  events_read: number;
  event_total: number;
  samples_prepared?: number;
  imported: number;
  cancel_requested: boolean;
  errors: ImportResult["errors"];
  warnings: ImportResult["warnings"];
  datasets: ImportDataset[];
}

export interface Sample {
  id: string;
  name: string;
  event_count: number;
  channels: Channel[];
  metadata: Record<string, string>;
  tags: Record<string, string>;
  compensation_id: string | null;
  sha256: string;
  source: string;
  derived_parameters: {
    name: string;
    label: string;
    expression: string;
    transform: Transform;
  }[];
  computed_parameters: { name: string; analysis_id: string; index: number }[];
  unmixed_parameters: string[];
  aliases?: Record<string, string>;
  event_export?: {
    version: 1;
    exported_at: string;
    values: "raw" | "compensated" | "scale";
    data_sha256: string;
    metadata_sha256: string;
    source_snapshot: Record<string, unknown>;
  };
  concatenation?: {
    version: 1;
    workspace_id: string;
    revision: number;
    created_at: string;
    values: "raw" | "compensated" | "scale";
    origins_sha256: string;
    sources: {
      index: number;
      sample_id: string;
      sample_name: string;
      gate_id: string | null;
      offset: number;
      count: number;
      event_count: number;
      parameters: Record<string, string>;
      snapshot: Record<string, unknown>;
    }[];
    keywords: Record<string, (string | null)[]>;
  };
}
export interface AnalysisRequest {
  revision: number;
  name: string;
  algorithm: "pca" | "umap" | "tsne" | "flowsom" | "phenograph";
  inputs: { sample_id: string; gate_id: string | null }[];
  channels: string[];
  seed: number;
  max_events: number;
  sampling: "balanced" | "proportional";
  use_transforms: boolean;
  standardize: boolean;
  n_neighbors: number;
  min_dist: number;
  perplexity: number;
  iterations: number;
  grid_size: number;
  n_clusters: number;
  epochs: number;
  create_cluster_gates: boolean;
  compensated?: boolean;
  min_cluster_size?: number;
  graph_resolution?: number;
  louvain_restarts?: number;
}
export interface AnalysisResult {
  id: string;
  request: AnalysisRequest;
  created_at: string;
  input_hash: string;
  input_snapshot: Record<string, unknown>[];
  columns: string[];
  data: {
    sample_id: string;
    event_count: number;
    population_count: number;
    finite_count: number;
    fitted_count: number;
    mapped_count: number;
    sha256: string;
    fitted_ids_sha256: string;
  }[];
  diagnostics: {
    community_sizes?: Record<string, number>;
    modularity?: number | null;
    graph_nodes?: number;
    graph_edges?: number;
    graph_isolated_cells?: number;
    unassigned_fitted_count?: number;
    feature_names: string[];
    fitted_events: number;
    eligible_events: number;
    mapping: string;
    explained_variance_ratio?: number[];
    loadings?: number[][];
    kl_divergence?: number;
    grid_size?: number;
    cluster_counts?: Record<string, Record<string, number>>;
  };
  versions: Record<string, string>;
  warnings: string[];
  duration_seconds: number;
  stale?: boolean;
}
export interface AnalysisJob {
  apply_blocker?: string;
  id: string;
  workspace_id: string;
  request: AnalysisRequest;
  status:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "interrupted"
    | "applied";
  stage: string;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: AnalysisResult | null;
  stale: boolean;
  can_apply: boolean;
}
export type QualityExclusion = "nonfinite" | "time" | "saturation" | "pulse";
export interface QualityRequest {
  revision: number;
  name: string;
  algorithm: "acquisition_qc";
  sample_id: string;
  gate_id: string | null;
  channels: string[];
  time_channel: string | null;
  compensated: boolean;
  use_transforms: boolean;
  bin_events: number;
  min_bin_events: number;
  score_threshold: number;
  signal_min_shift: number;
  rate_min_fold: number;
  saturation_channels: string[];
  saturation_fraction: number;
  pulse_area: string | null;
  pulse_height: string | null;
  pulse_score: number;
}
export interface QualityBin {
  index: number;
  start: number;
  end: number;
  population_count: number;
  time_start: number | null;
  time_end: number | null;
  rate: number | null;
  rate_score: number | null;
  signals: Record<
    string,
    {
      finite_count: number;
      p10: number | null;
      median: number | null;
      p90: number | null;
      score: number | null;
    }
  >;
  reasons: string[];
  suggested: boolean;
}
export interface QualityResult {
  id: string;
  request: QualityRequest;
  created_at: string;
  input_hash: string;
  data: {
    event_count: number;
    population_count: number;
    sha256: string;
    flag_counts: Record<QualityExclusion, number>;
  };
  bins: QualityBin[];
  diagnostics: {
    method: string;
    effective_bin_events: number;
    suggested_interval_events: number;
    time: {
      unit: string;
      resets: number;
      invalid_count: number;
      repeated_timestamps: number;
      gap_count?: number;
      median_positive_step?: number;
      resolution_limited_bins?: number;
    } | null;
    saturation: {
      channel: string;
      range: number;
      threshold: number;
      count: number;
    }[];
    pulse: {
      area: string;
      height: string;
      log_ratio_center: number | null;
      log_ratio_scale: number | null;
      invalid_count: number;
      positive_count: number;
      outlier_count: number;
      preview: {
        event_ids: number[];
        area: number[];
        height: number[];
        outlier: boolean[];
      };
    } | null;
  };
  warnings: string[];
  versions: Record<string, string>;
  duration_seconds: number;
  stale?: boolean;
}
export interface QualityJob {
  id: string;
  workspace_id: string;
  request: QualityRequest;
  status: AnalysisJob["status"];
  stage: string;
  progress: number;
  created_at: string;
  error: string | null;
  result: QualityResult | null;
  stale: boolean;
  can_apply: boolean;
}
export type PlotMode =
  | "density"
  | "scatter"
  | "histogram"
  | "cdf"
  | "contour"
  | "zebra"
  | "pseudocolor"
  | "3d";
export interface ThreeDView {
  z: string;
  z_dimension?: GateDimension | null;
  color_dimension?: GateDimension | null;
  size_dimension?: GateDimension | null;
  z_transform?: Transform | null;
  color_by?: string | null;
  size_by?: string | null;
  color_transform?: Transform | null;
  size_transform?: Transform | null;
  color_bounds?: [number, number] | null;
  size_bounds?: [number, number] | null;
  compensation?: "coordinate" | "uncompensated";
  all_events?: boolean;
  yaw?: number;
  pitch?: number;
  zoom?: number;
  pan?: [number, number];
  point_size?: number;
  opacity?: number;
  show_cube?: boolean;
  show_labels?: boolean;
}
export interface GraphTextStyle {
  font_size_pt?: number | null;
  font_family?: "sans" | "serif" | "mono" | null;
  font_weight?: "normal" | "bold" | null;
  font_style?: "normal" | "italic" | null;
  color?: string | null;
}
export interface GraphTypography {
  axis_labels?: GraphTextStyle | null;
  tick_labels?: GraphTextStyle | null;
  gate_labels?: GraphTextStyle | null;
  statistics?: GraphTextStyle | null;
  legend?: GraphTextStyle | null;
  title?: GraphTextStyle | null;
}
export interface GraphOptions {
  smooth?: boolean | null;
  sigma?: number;
  contour_spacing?: "2" | "5" | "10" | "log" | null;
  show_outliers?: boolean;
  palette?: "ocean" | "gray" | "spectrum" | "viridis";
  axis_extent?: "robust" | "full";
  point_limit?: number;
  typography?: GraphTypography | null;
  gate_style?: GraphGateStyle | null;
}
export interface GraphGateStyle {
  fill_opacity?: number | null;
  fill_color?: string | null;
  line_width_px?: number | null;
  show_labels?: boolean | null;
}
export interface DesktopPlotState {
  workspaceId: string;
  sampleId: string;
  gateId: string | null;
  x: string;
  y: string | null;
  mode: PlotMode;
  graphOptions?: GraphOptions;
  threeD?: ThreeDView;
  bins?: number;
  coordinateGateId?: string | null;
  backgateId?: string | null;
  bounds?: number[] | null;
  groupId?: string | null;
  pooled?: boolean;
  sampleFilter?: string;
  xTransform?: Transform | null;
  yTransform?: Transform | null;
  xDimension?: GateDimension | null;
  yDimension?: GateDimension | null;
}
export interface PlotNavigationAction {
  direction:
    | "previous"
    | "next"
    | "parent"
    | "select"
    | "population"
    | "child"
    | "sibling_previous"
    | "sibling_next"
    | "reset_population";
  sync: boolean;
  targetSampleId?: string;
  targetGateId?: string | null;
}
export interface DesktopPlotWindow extends DesktopPlotState {
  id: string;
}

export type GateKind =
  | "rectangle"
  | "polygon"
  | "ellipse"
  | "range"
  | "quadrant"
  | "spider"
  | "curly"
  | "boolean"
  | "hyperrectangle"
  | "ellipsoid"
  | "container"
  | "quality"
  | "membership";

export type GateDrawingKind = GateKind | "bisector";
export interface GatePartition {
  id: string;
  kind: "bisector" | "quadrant" | "spider" | "curly";
  member: 1 | 2 | 3 | 4;
}
export interface GateDimension {
  channel: string;
  transform: Transform;
  compensation_ref: string;
  minimum: number | null;
  maximum: number | null;
  ratio_channels: [string, string] | null;
  ratio_a: number;
  ratio_b: number;
  ratio_c: number;
  ratio_bound_min?: number | null;
  ratio_bound_max?: number | null;
}
export interface SpiderGeometry {
  center: [number, number];
  scale: [number, number];
  angles: [number, number, number, number];
}
export interface CurlyGeometry {
  center: [number, number];
  coefficients: [number, number];
  convention: "sqrt-positive-intensity-v1";
}
export interface MagneticGate {
  max_shift: number;
  algorithm?: "local-window-count-v1";
}
export interface MagneticResolution {
  algorithm: string;
  grid_per_gate_width: number;
  status: string;
  anchor: number[];
  position: number[];
  shift: number[];
  distance: number;
  max_shift: number;
  near_limit: boolean;
  anchor_count: number;
  resolved_count: number;
  parent_count: number;
  finite_parent_count: number;
  population_count: number;
  arrow?: { axes: number[]; from: number[]; to: number[] };
}
export interface Gate {
  id: string;
  sample_id: string;
  name: string;
  parent_id: string | null;
  kind: GateKind;
  x: string | null;
  y: string | null;
  x_transform: Transform;
  y_transform: Transform;
  bounds: number[];
  vertices: [number, number][];
  holes?: [number, number][][];
  center: [number, number] | null;
  radii: [number, number] | null;
  angle: number;
  quadrant: 1 | 2 | 3 | 4;
  operation: "and" | "or" | "not" | "xor";
  operands: string[];
  color: string;
  dimensions?: GateDimension[];
  covariance?: number[][];
  coordinates?: number[];
  distance_square?: number;
  complement?: boolean;
  operand_complements?: boolean[];
  provenance?: Record<string, unknown>;
  quality_id?: string | null;
  quality_excluded_bins?: number[];
  quality_exclusions?: QualityExclusion[];
  quality_keep?: boolean;
  membership?: {
    id: string;
    sample_id: string;
    raw_sha256: string;
    sha256: string;
    event_count: number;
    selected_count: number;
    acquisition_channels: string[];
    encoding: "packed-little";
  } | null;
  magnetic?: MagneticGate | null;
  partition?: GatePartition | null;
  spider?: SpiderGeometry | null;
  curly?: CurlyGeometry | null;
}
export interface Compensation {
  id: string;
  name: string;
  detectors: string[];
  outputs: string[];
  matrix: number[][];
  kind: "spillover" | "spectral";
  source: string;
  background?: number[];
  weights?: number[];
  provenance?: Record<string, unknown>;
}
export const acquisitionChannels = (sample: Sample) =>
  sample.channels.filter(
    (c) =>
      !sample.derived_parameters.some((d) => d.name === c.name) &&
      !sample.computed_parameters.some((p) => p.name === c.name) &&
      !sample.unmixed_parameters?.includes(c.name) &&
      !(c.name in (sample.aliases ?? {})),
  );
export interface Group {
  id: string;
  name: string;
  sample_ids: string[];
  color: string;
}
export interface PlotDefinition {
  backgate_id?: string | null;
  pooled?: boolean;
  group_id?: string | null;
  sample_filter?: string;
  x_dimension?: GateDimension | null;
  y_dimension?: GateDimension | null;
  three_d?: ThreeDView | null;
  graph_options?: GraphOptions;
  id: string;
  sample_id: string;
  gate_id: string | null;
  coordinate_gate_id?: string | null;
  x: string;
  y: string | null;
  mode: string;
  title: string;
  overlays?: PlotLayer[];
  locked_control?: boolean;
  color?: string;
  x_transform?: Transform | null;
  y_transform?: Transform | null;
  bounds?: number[] | null;
  bins?: number;
  normalization?: "count" | "percent_population" | "unit_area" | "peak";
  show_gates?: boolean;
}
export interface PlotLayer {
  id: string;
  sample_id: string;
  gate_id: string | null;
  coordinate_gate_id?: string | null;
  backgate_id?: string | null;
  label: string;
  color: string;
  locked_control: boolean;
}
export interface ReportPage {
  width_mm: number;
  height_mm: number;
  margin_mm: number;
  background: string;
}
export interface ReportTableCellStyle {
  row?: number | null;
  column: number;
  align?: "left" | "center" | "right" | null;
  vertical_align?: "top" | "middle" | "bottom" | null;
  background?: string | null;
  color?: string | null;
  font_size_pt?: number | null;
  font_family?: "sans-serif" | "serif" | "monospace" | null;
  font_weight?: 400 | 600 | 700 | 800 | null;
  font_style?: "normal" | "italic" | null;
  text_decoration?: "none" | "underline" | null;
  line_spacing?: number | null;
}
export interface ReportTableGeometry {
  column_width_mm?: number | null;
  row_height_mm?: number | null;
  header_height_mm?: number | null;
  column_widths_mm?: Record<number, number>;
  row_heights_mm?: Record<number, number>;
  padding_x_mm?: number;
  padding_y_mm?: number;
  align?: "left" | "center" | "right";
  vertical_align?: "top" | "middle" | "bottom";
  cell_styles?: ReportTableCellStyle[];
}
export interface ReportTablePagination {
  total_rows: number;
  total_columns: number;
  header_height_mm: number;
  rows_per_page: number;
  columns_per_page: number;
  vertical_pages: number;
  horizontal_pages: number;
  segments: number;
  row_windows: { start: number; count: number; height_mm: number }[];
  column_windows: {
    start: number;
    count: number;
    width_mm: number;
    columns: { index: number; left: number; width_mm: number }[];
  }[];
  columns: { index: number; id: string; name: string; fixed: boolean }[];
}
export interface ReportElement {
  id: string;
  kind:
    | "plot"
    | "table"
    | "biology"
    | "population_comparison"
    | "plate"
    | "text"
    | "shape";
  page: number;
  x_mm: number;
  y_mm: number;
  width_mm: number;
  height_mm: number;
  rotation: number;
  group_id: string | null;
  position_locked: boolean;
  iterate: boolean;
  title: string;
  plot?: PlotDefinition | null;
  table_id?: string | null;
  table_view: "data" | "pivot" | "comparisons";
  table_geometry?: ReportTableGeometry | null;
  auto_paginate: boolean;
  row_start: number;
  row_count: number;
  column_ids: string[];
  column_start: number;
  columns_per_page: number;
  pivot_counts: boolean;
  comparison_fields: (
    | "n_a"
    | "n_b"
    | "pairs"
    | "excluded_a"
    | "excluded_b"
    | "mean_a"
    | "mean_b"
    | "mean_difference"
    | "median_difference"
    | "statistic"
    | "p_value"
    | "adjusted_p_value"
    | "confidence_interval"
  )[];
  platform?: "cell-cycle" | "proliferation" | "kinetics" | null;
  result_id?: string | null;
  sample_id?: string | null;
  gate_id?: string | null;
  compensated: boolean;
  follow_replacement: boolean;
  comparison_parameter_id?: string | null;
  comparison_view?: ComparisonFigureView | null;
  plate_id?: string | null;
  text: string;
  font_size_pt: number;
  font_family: "sans-serif" | "serif" | "monospace";
  font_weight: 400 | 600 | 700 | 800;
  font_style?: "normal" | "italic";
  text_decoration?: "none" | "underline";
  line_spacing?: number;
  align: "left" | "center" | "right";
  color: string;
  fill: string;
  stroke: string;
  stroke_width_mm: number;
  opacity: number;
  shape: "rectangle" | "ellipse" | "line" | "arrow";
}
export interface ReportBatch {
  mode: "off" | "sample" | "keyword" | "panel";
  group_id: string | null;
  sample_ids: string[];
  iterator_keyword: string;
  discriminator_keyword: string;
  panel_size: number;
  tile_columns: number;
  tile_rows: number;
  order: "across" | "down";
  overrides: Record<string, Record<string, string>>;
  population_overrides: Record<string, Record<string, string | null>>;
}
export interface LayoutDefinition {
  id: string;
  name: string;
  description: string;
  plots: PlotDefinition[];
  pages?: ReportPage[];
  elements?: ReportElement[];
  batch?: ReportBatch;
  export_policy?: "current" | "snapshots" | "placeholders";
  show_header?: boolean;
  show_footer?: boolean;
  template_origin?: {
    template_sha256: string;
    source_workspace_id: string;
    source_layout_id: string;
    bindings: Record<string, string | null>;
    batch_scope: "destination" | "template";
  } | null;
}
export interface ReportLayout extends LayoutDefinition {
  pages: ReportPage[];
  elements: ReportElement[];
  batch: ReportBatch;
  export_policy: "current" | "snapshots" | "placeholders";
  show_header: boolean;
  show_footer: boolean;
}
export interface ReportIssue {
  message: string;
  severity: string;
  element_id?: string | null;
}
export interface ReportPlan {
  revision: number;
  review_hash: string;
  page_count: number;
  output_pages: {
    prototype_page: number;
    batch_index: number;
    continuation: number;
    continuation_count: number;
  }[];
  prototype_sources: string[];
  notices: ReportIssue[];
  definition: ReportLayout;
  exportable: boolean;
  iterations: {
    key: string;
    label: string;
    sample_ids: string[];
    mapping: Record<string, string | null>;
    issues: ReportIssue[];
    tables?: Record<string, ReportTablePagination>;
    bindings: {
      element_id: string;
      source_sample_id?: string;
      source_gate_id?: string | null;
      source_coordinate_gate_id?: string | null;
      sample_id: string;
      gate_id?: string | null;
      locked_control?: boolean;
    }[];
  }[];
}
export interface ReportRender {
  revision: number;
  review_hash: string;
  page: number;
  page_count: number;
  geometry: ReportPage;
  svg: string;
  manifest: Record<string, unknown>;
  issues: ReportIssue[];
  exportable: boolean;
}
export interface FitConstraint {
  fixed?: number | null;
  minimum?: number | null;
  maximum?: number | null;
  initial?: number | null;
}
export interface CellCycleRequest {
  replace_result_id: string | null;
  revision: number;
  name: string;
  algorithm: "cell_cycle";
  method: "djf" | "watson";
  inputs: { sample_id: string; gate_id: string | null }[];
  channel: string;
  compensated: boolean;
  bins: number;
  range_min: number | null;
  range_max: number | null;
  g1_mean: FitConstraint;
  g2_mean: FitConstraint;
  g1_cv: FitConstraint;
  g2_cv: FitConstraint;
  peak_ratio: FitConstraint;
  linked_cv: "none" | "g2_to_g1" | "g1_to_g2";
  synchronous_s: boolean;
  s_peak_initial: number | null;
  objective: "poisson" | "weighted_least_squares";
  maximum_evaluations: number;
  smoothing: number;
  create_phase_gates: boolean;
}
export interface CellCycleFit {
  sample_id: string;
  data: {
    sample_id: string;
    event_count: number;
    population_count: number;
    finite_count: number;
    fitted_count: number;
    sha256: string;
  };
  range_min: number;
  range_max: number;
  edges: number[];
  observed: number[];
  components: number[][];
  weights: number[][];
  parameters: {
    g1_mean: number;
    g2_mean: number;
    g1_cv: number;
    g2_cv: number;
    peak_ratio: number;
  };
  fractions: number[];
  expected_counts: number[];
  assigned_counts: number[];
  diagnostics: {
    converged: boolean;
    rmsd_events_per_bin: number;
    normalized_rmsd: number;
    poisson_deviance: number;
    reduced_chi_square: number | null;
    degrees_of_freedom: number | null;
    fraction_standard_errors: number[] | null;
    captured_component_mass?: number[];
    jacobian_rank?: number;
    parameters_at_bounds?: number[];
    excluded_nonfinite: number;
    excluded_negative: number;
    excluded_below_range: number;
    excluded_above_range: number;
    parameter_basis: string;
    percentage_basis: string;
    expected_fraction: number[];
  };
  warnings: string[];
}
export interface CellCycleResult {
  id: string;
  request: CellCycleRequest;
  created_at: string;
  input_hash: string;
  input_snapshot: Record<string, unknown>;
  columns: string[];
  fits: CellCycleFit[];
  warnings: string[];
  versions: Record<string, string>;
  duration_seconds: number;
  stale?: boolean;
}
export interface CellCycleJob extends Omit<AnalysisJob, "request" | "result"> {
  request: CellCycleRequest;
  result: CellCycleResult | null;
}
export interface ProliferationRequest {
  replace_result_id: string | null;
  revision: number;
  name: string;
  algorithm: "proliferation";
  inputs: { sample_id: string; gate_id: string | null }[];
  channel: string;
  compensated: boolean;
  distribution: "lognormal" | "gaussian";
  histogram_space: "log2" | "linear";
  generations: number;
  bins: number;
  range_min: number | null;
  range_max: number | null;
  undivided_control: { sample_id: string; gate_id: string | null } | null;
  control_mode: "initial" | "fix_mean" | "fix_mean_cv";
  control_range_min: number | null;
  control_range_max: number | null;
  undivided_mean: FitConstraint;
  peak_ratio: FitConstraint;
  dye_cv: FitConstraint;
  autofluorescence_control: {
    sample_id: string;
    gate_id: string | null;
  } | null;
  background: number;
  background_sd: number;
  objective: "poisson" | "weighted_least_squares";
  maximum_evaluations: number;
  create_generation_gates: boolean;
}
export interface ProliferationStatistics {
  total_events: number;
  precursor_equivalents: number;
  responding_precursor_equivalents: number;
  division_equivalents: number;
  precursor_frequency: number;
  division_index: number;
  proliferation_index: number | null;
  expansion_index: number;
  replication_index: number | null;
  observed_divided_fraction: number;
}
export interface ProliferationFit extends Omit<
  CellCycleFit,
  "parameters" | "diagnostics"
> {
  parameters: {
    undivided_mean: number;
    peak_ratio: number;
    dye_cv: number;
    background: number;
    background_sd: number;
  };
  peak_locations: number[];
  statistics: ProliferationStatistics;
  diagnostics: Omit<CellCycleFit["diagnostics"], "excluded_negative"> & {
    excluded_nonpositive: number;
    adjacent_generation_overlap: number[];
    metric_standard_errors: Record<string, number> | null;
    uncertainty_basis: string;
    posterior_basis: string;
  };
}
export interface ProliferationResult extends Omit<
  CellCycleResult,
  "request" | "fits"
> {
  request: ProliferationRequest;
  fits: ProliferationFit[];
  calibration: Record<string, unknown> & {
    undivided_mean?: number;
    dye_cv?: number;
    background: number;
    background_sd: number;
  };
}
export interface ProliferationJob extends Omit<
  AnalysisJob,
  "request" | "result"
> {
  request: ProliferationRequest;
  result: ProliferationResult | null;
}
export interface KineticsRange {
  id: string;
  name: string;
  start: number;
  end: number;
  color: string;
}
export interface KineticsRequest {
  revision: number;
  name: string;
  algorithm: "kinetics";
  inputs: { sample_id: string; gate_id: string | null }[];
  channel: string;
  compensated: boolean;
  time_channel: string | null;
  event_rate: number | null;
  time_multiplier: number;
  time_offsets: Record<string, number>;
  clock_policy: "reject" | "use_recorded" | "unwrap";
  time_min: number | null;
  time_max: number | null;
  bins: number;
  minimum_events: number;
  statistic:
    "median" | "mean" | "geometric_mean" | "percentile" | "percent_positive";
  percentile: number;
  threshold_mode: "absolute" | "baseline_percentile";
  threshold: number;
  baseline: KineticsRange | null;
  baseline_percentile: number;
  above_threshold_only: boolean;
  smoothing: "none" | "moving_average" | "gaussian";
  smoothing_width: number;
  gaussian_sigma: number;
  ranges: KineticsRange[];
  create_range_gates: boolean;
  create_responder_gates: boolean;
  replace_result_id: string | null;
}
export interface KineticsBin {
  index: number;
  start: number;
  end: number;
  center: number;
  population_count: number;
  finite_count: number;
  selected_count: number | null;
  responder_count: number | null;
  raw_value: number | null;
  value: number | null;
}
export interface KineticsRangeSummary extends KineticsRange {
  population_count: number;
  finite_count: number;
  selected_count: number | null;
  responder_count: number | null;
  curve_points: number;
  peak_time: number | null;
  peak: number | null;
  mean: number | null;
  slope: number | null;
  auc: number | null;
  covered_duration: number | null;
  duration: number | null;
}
export interface KineticsFit {
  sample_id: string;
  gate_id: string | null;
  time_domain: [number, number] | null;
  threshold: number | null;
  baseline_count: number;
  reset_count: number;
  repeated_timestamps: number;
  bins: KineticsBin[];
  ranges: KineticsRangeSummary[];
  warnings: string[];
}
export interface KineticsResult {
  id: string;
  request: KineticsRequest;
  created_at: string;
  input_hash: string;
  input_snapshot: Record<string, unknown>;
  columns: string[];
  data: {
    sample_id: string;
    event_count: number;
    population_count: number;
    time_count: number;
    finite_count: number;
    represented_count: number;
    sha256: string;
  }[];
  fits: KineticsFit[];
  versions: Record<string, string>;
  duration_seconds: number;
  stale: boolean;
}
export interface KineticsJob extends Omit<AnalysisJob, "request" | "result"> {
  request: KineticsRequest;
  result: KineticsResult | null;
}
export interface TableColumn {
  id: string;
  name: string;
  kind:
    "statistic" | "metadata" | "formula" | "biology" | "population_comparison";
  statistic: string;
  channel: string;
  percentile: number;
  channel_overrides?: Record<string, string>;
  population_path: string[] | null;
  population_overrides: Record<string, string | null>;
  population_unavailable?: string[];
  compensation_overrides?: Record<string, string>;
  control_sample_id: string | null;
  control_unavailable?: boolean;
  metadata_source: "tags" | "metadata" | "keywords";
  metadata_key: string;
  metadata_numeric: boolean;
  expression: string;
  formula_refs: Record<string, string>;
  platform: "cell-cycle" | "proliferation" | "kinetics";
  kinetics_range_id: string | null;
  comparison_parameter_id?: string | null;
  result_id: string | null;
  biology_metric: string;
  generation: number;
  allow_stale: boolean;
  follow_replacement: boolean;
  hidden: boolean;
  decimals: number;
  heatmap: boolean;
}
export interface TablePivot {
  rows: string[];
  columns: string[];
  measures: string[];
  aggregation: "mean" | "median" | "sum" | "count" | "std" | "min" | "max";
}
export interface TableComparison {
  group_column: string;
  group_a: string;
  group_b: string;
  measures: string[];
  method: "welch" | "mann_whitney" | "paired_t" | "wilcoxon";
  pair_column: string | null;
  adjustment: "holm" | "benjamini_hochberg" | "none";
  confidence_level: number;
}
export interface TableDefinition {
  id: string;
  name: string;
  channel: string;
  filter: string;
  columns: TableColumn[];
  row_mode: "samples" | "populations";
  group_id: string | null;
  sample_ids: string[];
  sample_order?: "workspace" | "selection";
  compensated: boolean;
  sort_by: string;
  descending: boolean;
  pivot: TablePivot | null;
  comparison: TableComparison | null;
  provenance?: Record<string, string>;
}
export interface TableEvaluation {
  workspace_id: string;
  revision: number;
  definition: TableDefinition;
  columns: TableColumn[];
  rows: {
    id: string;
    sample_id: string;
    sample: string;
    gate_id: string | null;
    population: string;
    population_path: string[];
    values: Record<string, number | string | null>;
    status: Record<string, string>;
  }[];
  total_rows: number;
  column_ranges: Record<string, [number, number] | null>;
  offset: number;
  notices: string[];
  pivot: {
    row_columns: { id: string; name: string }[];
    columns: { id: string; name: string; measure: string }[];
    rows: {
      group: (string | number | null)[];
      values: Record<string, number | null>;
      counts: Record<string, number>;
    }[];
    aggregation: string;
  } | null;
  comparisons: {
    column_id: string;
    column: string;
    method: string;
    group_a: string;
    group_b: string;
    n_a: number;
    n_b: number;
    excluded_a: number;
    excluded_b: number;
    pairs: number;
    mean_a: number | null;
    mean_b: number | null;
    mean_difference: number | null;
    median_difference: number | null;
    statistic: number | null;
    p_value: number | null;
    adjusted_p_value: number | null;
    confidence_interval: (number | null)[] | null;
    issue: string | null;
  }[];
}
export interface PlateGeometry {
  rows: number;
  columns: number;
}
export interface PlateDefinition {
  id: string;
  name: string;
  plate_key: string;
  format: 6 | 12 | 24 | 48 | 96 | 384 | 1536 | "custom";
  geometry?: PlateGeometry | null;
  assignments: Record<string, string[]>;
  annotations: Record<string, Record<string, string | null>>;
  columns: TableColumn[];
  compensated: boolean;
  aggregate: "median" | "mean" | "sum";
  missing_replicates: "strict" | "available";
  view: {
    mode: "heatmap" | "split" | "categories" | "faces";
    primary: string | null;
    secondary: string | null;
    category_keyword: string;
    category_source: "tags" | "metadata";
    category_colors: Record<string, string>;
    domains: Record<string, [number, number]>;
  };
  provenance: Record<string, unknown>;
}
export interface PlateWell {
  well: string;
  row: number;
  column: number;
  sample_ids: string[];
  samples: string[];
  values: Record<string, number | string | null>;
  status: Record<string, string | null>;
  keywords: Record<string, string>;
  category_value: string | null;
  staged: Record<string, string | null>;
  acquisitions: TableEvaluation["rows"];
  normalized: Record<string, number | null>;
  clipped: Record<string, boolean>;
  color: string;
  secondary_color: string;
  category_color: string;
  face_svg: string;
}
export interface PlateEvaluation {
  plate: PlateDefinition;
  revision: number;
  rows: number;
  columns: number;
  wells: PlateWell[];
  domains: Record<
    string,
    {
      min: number | null;
      max: number | null;
      display_min: number | null;
      display_max: number | null;
      finite_wells: number;
      explicit: boolean;
    }
  >;
  categories: Record<string, string>;
  primary: string;
  secondary: string;
  face_features: { feature: string; column_id: string; name: string }[];
  acquisition_count: number;
  mapped_wells: number;
  provenance: Record<string, unknown>;
  notices: string[];
  aggregation: string;
}
export interface ComparisonParameter extends Omit<
  GateDimension,
  "minimum" | "maximum"
> {
  id: string;
  label: string;
}
export interface PopulationComparisonRequest {
  revision: number;
  name: string;
  algorithm: "population_comparison";
  inputs: { sample_id: string; gate_id: string | null }[];
  controls: { sample_id: string; gate_id: string | null }[];
  parameters: ComparisonParameter[];
  probability_bins: number;
  histogram_bins: number;
  minimum_bin_events: number;
  positive_direction: "higher" | "lower";
  joint: boolean;
  control_baselines: boolean;
  replace_result_id: string | null;
}
export interface PopulationComparisonRow {
  source: { sample_id: string; gate_id: string | null };
  source_name: string;
  population_name: string;
  role: "target" | "control_baseline";
  parameter_id: string | null;
  selected_count: number;
  finite_count: number;
  control_selected_count: number;
  control_finite_count: number;
  shared_events: number;
  status: "available" | "unavailable";
  error: string | null;
  metrics: Record<string, number | boolean | string | null>;
  probability: Record<string, number | boolean | string | null>;
  warnings: string[];
}
export interface PopulationComparisonResult {
  id: string;
  request: PopulationComparisonRequest;
  created_at: string;
  input_hash: string;
  input_snapshot: Record<string, unknown>;
  data: {
    sha256: string;
    bytes: number;
    parameters: number;
    targets: number;
    controls: number;
  };
  rows: PopulationComparisonRow[];
  joint_rows: PopulationComparisonRow[];
  diagnostics: Record<string, unknown>;
  warnings: string[];
  versions: Record<string, string>;
  duration_seconds: number;
  stale?: boolean;
}
export interface PopulationComparisonJob extends Omit<
  AnalysisJob,
  "request" | "result"
> {
  request: PopulationComparisonRequest;
  result: PopulationComparisonResult | null;
}
export interface ComparisonPlotData {
  edges: number[];
  control_counts: number[];
  target_counts: number[];
  control_fraction: number[];
  target_fraction: number[];
  control_cdf: number[];
  target_cdf: number[];
  difference: number[];
  individual_controls: {
    source: { sample_id: string; gate_id: string | null };
    counts: number[];
    fraction: number[];
  }[];
  parameter: ComparisonParameter;
  row: PopulationComparisonRow;
  stale: boolean;
}
export interface ComparisonFigureView {
  mode: "histogram" | "cdf" | "difference";
  smoothing: number;
  control_color: string;
  target_color: string;
  difference_scale: number;
  show_individuals: boolean;
}
export interface DesktopComparisonState {
  workspaceId: string;
  resultId: string;
  parameterId: string | null;
  targetIndex: number;
  mode: "histogram" | "cdf" | "difference";
  smoothing: number;
  controlColor: string;
  targetColor: string;
  differenceScale: number;
  showIndividuals: boolean;
}
export interface Workspace {
  id: string;
  name: string;
  description: string;
  revision: number;
  samples: Sample[];
  gates: Gate[];
  groups: Group[];
  compensations: Compensation[];
  layouts: LayoutDefinition[];
  tables: TableDefinition[];
  plates: PlateDefinition[];
  analyses: AnalysisResult[];
  interchanges: InterchangeRecord[];
  quality_results: QualityResult[];
  cell_cycle_results: CellCycleResult[];
  proliferation_results: ProliferationResult[];
  kinetics_results: KineticsResult[];
  comparison_results?: PopulationComparisonResult[];
  created_at: string;
  updated_at: string;
}
export interface InterchangeIssue {
  severity: string;
  code: string;
  message: string;
  source_id?: string | null;
  gate?: string | null;
}
export interface InterchangeRecord {
  id: string;
  name: string;
  format: "flowjo" | "gatingml";
  sha256: string;
  created_at: string;
  mappings: Record<string, string[]>;
  gate_ids: string[];
  table_ids?: string[];
  report: {
    issues: InterchangeIssue[];
    tables?: {
      id: string;
      name: string;
      converted_columns: number;
      total_source_columns: number;
    }[];
    counts: (GateCount & {
      sample_id: string;
      name: string;
      reported_count: number | null;
    })[];
  };
}
export interface InterchangePreview {
  preview_id: string;
  revision: number;
  requires_acknowledgement: boolean;
  document: {
    format: "flowjo" | "gatingml";
    name: string;
    version: string;
    sha256: string;
    issues: InterchangeIssue[];
    tables: {
      id: string;
      name: string;
      group_name: string;
      source_ids: string[];
      total_columns: number;
      definition: TableDefinition | null;
    }[];
    sources: {
      id: string;
      name: string;
      file_name: string;
      event_count: number | null;
      channels: string[];
      gates: Gate[];
      total_gates: number;
      suggested_sample_ids: string[];
      compatible_sample_ids: string[];
      matrices: Compensation[];
    }[];
  };
}
export interface WorkspaceSummary {
  id: string;
  name: string;
  sample_count: number;
  updated_at: string;
}
export interface PooledScope {
  anchor_id: string;
  group_id: string | null;
  sample_filter: string;
  gate_id: string | null;
}
export interface PooledGroup {
  revision?: number;
  group_id: string | null;
  group_name: string;
  sample_filter: string;
  common_channels: string[];
  total_events: number;
  population_events: number;
  sources: {
    sample_id: string;
    sample_name: string;
    source_index: number;
    offset: number;
    event_count: number;
    population_id: string | null;
    count: number;
  }[];
  analysis_inputs?: AnalysisRequest["inputs"];
}
export interface GateCount {
  id: string;
  count: number | null;
  complete?: boolean;
  error?: string;
  percent_parent: number | null;
  percent_total: number | null;
}
export interface Tick {
  value: number;
  label: string;
}
export interface Overlay {
  id: string;
  pooled_source_id?: string;
  pooled_source_name?: string;
  name: string;
  color: string;
  kind: "range" | "polygon" | "spider" | "curly";
  axis?: "x" | "y";
  bounds?: number[];
  vertices?: [number, number][];
  holes?: [number, number][][];
  magnetic?: MagneticResolution;
  segments?: [number, number][][];
  shared_segments?: [number, number][][];
  center?: [number, number] | null;
}
export interface ThreeDData {
  pooled?: PooledGroup;
  revision: number;
  mode: "3d";
  x: string;
  y: string;
  z: string;
  count: number;
  finite_count: number;
  visible_count: number;
  displayed_count: number;
  backgate_count: number;
  backgate_visible_count?: number;
  backgate_displayed_count?: number;
  backgate_sampling?: string | null;
  bounds: number[];
  axes: GateDimension[];
  ticks: Tick[][];
  three_d: ThreeDView;
  graph_options: GraphOptions;
  color_bounds: [number, number];
  size_bounds: [number, number];
  color_finite_count: number;
  size_finite_count: number;
  chunk_events: number;
  point_stride: number;
  data_key: string;
  sampling: string | null;
  boxes: {
    id: string;
    name: string;
    color: string;
    bounds: number[];
    normalized: number[];
    magnetic?: MagneticResolution;
  }[];
}
export interface PlotData {
  pooled?: PooledGroup;
  axes?: GateDimension[];
  x_transform?: Transform;
  y_transform?: Transform | null;
  revision: number;
  mode: string;
  x: string;
  y: string | null;
  bins: number;
  count: number;
  finite_count: number;
  visible_count: number;
  bounds: number[];
  ticks_x: Tick[];
  ticks_y: Tick[];
  counts?: number[];
  edges?: number[];
  max_count?: number;
  points?: [number, number][];
  displayed_count?: number;
  overlays: Overlay[];
  backgate_points?: [number, number][];
  backgate_count?: number;
  backgate_visible_count?: number;
  backgate_displayed_count?: number;
  backgate_sampling?: string | null;
  graph_options?: GraphOptions;
  density_bounds?: number[];
  density_field?: number[];
  density_max?: number;
  density_count?: number;
  cdf_counts?: number[];
  cdf_percent?: (number | null)[];
  cdf_denominator?: number;
  cdf_undefined_reason?: string | null;
  probability_denominator?: number;
  probability_levels?: {
    probability: number;
    threshold: number | null;
    estimated_probability?: number;
    binned_event_probability?: number;
    tied_bins?: number;
    reason: string | null;
  }[];
  contours?: {
    threshold: number;
    geometry: "bin_cells" | "interpolated_centers";
    paths: [number, number][][];
  }[];
  contour_geometry?: string;
  contours_truncated?: boolean;
  zebra_bands?: number[];
  zebra_max_band?: number;
  outlier_points?: [number, number][];
  outlier_count?: number;
  outlier_visible_count?: number;
  outlier_displayed_count?: number;
  point_sampling?: string | null;
  outlier_sampling?: string | null;
}
export interface History {
  cursor: number;
  can_undo: boolean;
  can_redo: boolean;
  entries: { seq: number; label: string; at: string }[];
}
export type TableRow = Record<string, string | number | boolean | null>;
export const id = () => crypto.randomUUID().replaceAll("-", "");
export const formatNumber = (n: number | null | undefined, digits = 1) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en", { maximumFractionDigits: digits }).format(n);
export const percentage = (n: number | null | undefined) =>
  n == null ? "—" : `${n.toFixed(1)}%`;
export const channelLabel = (channel: Channel) =>
  channel.label &&
  channel.label !== channel.name &&
  !["Forward scatter", "Side scatter", "Singlets"].includes(channel.label)
    ? `${channel.label} · ${channel.name}`
    : channel.name;
export function makeGate(
  sampleId: string,
  parent: string | null,
  kind: GateKind,
  x: Channel,
  y: Channel | null,
): Gate {
  return {
    id: id(),
    sample_id: sampleId,
    name: "New population",
    parent_id: parent,
    kind,
    x: x.name,
    y: y?.name ?? null,
    x_transform: x.transform,
    y_transform: y?.transform ?? linear,
    bounds: [],
    vertices: [],
    center: null,
    radii: null,
    angle: 0,
    quadrant: 1,
    operation: "and",
    operands: [],
    color: "#38d9ba",
  };
}
