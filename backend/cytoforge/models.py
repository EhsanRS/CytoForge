from __future__ import annotations

import math
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .formulas import parse
from .table_expressions import parse_table_formula


def new_id() -> str:
    return uuid.uuid4().hex


Id = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Name = Annotated[str, Field(min_length=1, max_length=160)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Transform(Model):
    kind: Literal[
        "linear",
        "log",
        "logicle",
        "hyperlog",
        "asinh",
        "gml_linear",
        "gml_log",
        "gml_asinh",
        "wsp_log",
        "wsp_biex",
    ] = "linear"
    cofactor: float = Field(default=150, gt=0)
    t: float = Field(default=262144, gt=0)
    w: float = Field(default=0.5, ge=0)
    m: float = Field(default=4.5, gt=0)
    a: float = 0
    offset: float = Field(default=1, gt=0)
    positive: float = Field(default=4.418540, gt=0, le=20)
    negative: float = Field(default=0, ge=0, le=20)
    width: float = Field(default=-10, le=-1)
    top: float = Field(default=262144.000029, gt=0)
    bound_min: float | None = None
    bound_max: float | None = None

    @model_validator(mode="after")
    def valid_logicle(self):
        if (
            self.bound_min is not None
            and self.bound_max is not None
            and self.bound_min > self.bound_max
        ):
            raise ValueError("Transform bound minimum cannot exceed its maximum")
        if self.kind == "gml_linear" and self.t + self.a <= 0:
            raise ValueError("Linear transform requires T + A > 0")
        if self.kind == "gml_asinh" and self.a < 0:
            raise ValueError("GatingML arcsinh requires A ≥ 0")
        if self.kind == "wsp_biex" and self.positive <= math.log10(-self.width) / 2:
            raise ValueError("Biex positive decades must exceed half the width decades")
        if self.kind in {"logicle", "hyperlog"}:
            if self.w > self.m / 2 or self.a < -self.w or self.a > self.m - 2 * self.w:
                raise ValueError("Transform requires W ≤ M/2 and -W ≤ A ≤ M - 2W")
            if self.kind == "hyperlog" and self.w == 0:
                raise ValueError("Hyperlog requires W > 0")
        return self


class Channel(Model):
    name: Name
    label: str = ""
    range: float = Field(default=262144, gt=0)
    transform: Transform = Field(default_factory=Transform)


class DerivedParameter(Model):
    name: Name
    label: str = ""
    expression: str = Field(min_length=1, max_length=1024)
    transform: Transform = Field(default_factory=Transform)


class ComputedParameter(Model):
    name: Name
    analysis_id: Id
    index: int = Field(ge=0, le=63)


class AnalysisInput(Model):
    sample_id: Id
    gate_id: Id | None = None


class AnalysisRequest(Model):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=130)
    algorithm: Literal["pca", "umap", "tsne", "flowsom", "phenograph"]
    inputs: list[AnalysisInput] = Field(min_length=1, max_length=128)
    channels: list[Name] = Field(min_length=2, max_length=64)
    seed: int = Field(default=42, ge=0, le=2147483647)
    max_events: int = Field(default=10000, ge=4, le=100000)
    sampling: Literal["balanced", "proportional"] = "balanced"
    use_transforms: bool = True
    compensated: bool = True
    standardize: bool = True
    n_neighbors: int = Field(default=15, ge=2, le=200)
    min_dist: float = Field(default=0.1, ge=0, le=1)
    perplexity: float = Field(default=30, gt=0, le=1000)
    iterations: int = Field(default=1000, ge=300, le=10000)
    grid_size: int = Field(default=10, ge=2, le=20)
    n_clusters: int = Field(default=10, ge=2, le=100)
    epochs: int = Field(default=10, ge=1, le=100)
    create_cluster_gates: bool = True
    min_cluster_size: int = Field(default=10, ge=2, le=100000)
    graph_resolution: float = Field(default=1.0, gt=0, le=100)
    louvain_restarts: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def unique_inputs(self):
        if len({v.sample_id for v in self.inputs}) != len(self.inputs):
            raise ValueError("Select each sample only once")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Analysis channels must be unique")
        if self.algorithm == "tsne" and self.max_events > 30000:
            raise ValueError("t-SNE supports at most 30,000 fitted events in this release")
        if self.algorithm == "phenograph" and self.max_events * self.n_neighbors > 5_000_000:
            raise ValueError("Choose fewer fitted events or neighbours for at most 5 million links")
        if self.algorithm == "flowsom" and self.n_clusters > self.grid_size**2:
            raise ValueError("FlowSOM metaclusters cannot exceed the number of SOM nodes")
        if self.algorithm == "flowsom" and self.create_cluster_gates:
            if len(self.inputs) * self.n_clusters > 2000:
                raise ValueError("Choose fewer samples or clusters to create at most 2,000 gates")
        return self


class AnalysisData(Model):
    sample_id: Id
    event_count: int = Field(ge=0)
    population_count: int = Field(ge=0)
    finite_count: int = Field(ge=0)
    fitted_count: int = Field(ge=0)
    mapped_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fitted_ids_sha256: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")


class AnalysisResult(Model):
    id: Id
    request: AnalysisRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: list[dict] = Field(default_factory=list)
    columns: list[Name] = Field(min_length=1, max_length=64)
    data: list[AnalysisData] = Field(min_length=1, max_length=128)
    diagnostics: dict = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def unique_data(self):
        if self.input_snapshot:
            import hashlib
            import json

            digest = hashlib.sha256(
                json.dumps(self.input_snapshot, sort_keys=True).encode()
            ).hexdigest()
            if digest != self.input_hash:
                raise ValueError(
                    "Analysis input snapshot does not match its scientific fingerprint"
                )
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Analysis output columns must be unique")
        if {d.sample_id for d in self.data} != {v.sample_id for v in self.request.inputs}:
            raise ValueError("Analysis output samples must match its inputs")
        if len({d.sample_id for d in self.data}) != len(self.data):
            raise ValueError("Duplicate analysis output samples")
        for d in self.data:
            if not (d.fitted_count <= d.finite_count <= d.population_count <= d.event_count):
                raise ValueError("Analysis event counts are inconsistent")
            if not (d.fitted_count <= d.mapped_count <= d.finite_count):
                raise ValueError("Analysis mapping counts are inconsistent")
        return self


class KineticsRange(Model):
    id: Id = Field(default_factory=new_id)
    name: Name = "Time range"
    start: float
    end: float
    color: str = Field(default="#38d9ba", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def increasing(self):
        if self.start >= self.end:
            raise ValueError("A kinetics range must end after it starts")
        return self


class KineticsRequest(Model):
    revision: int = Field(ge=0)
    name: str = Field(default="Kinetics", min_length=1, max_length=125)
    algorithm: Literal["kinetics"] = "kinetics"
    inputs: list[AnalysisInput] = Field(min_length=1, max_length=128)
    channel: Name
    compensated: bool = True
    time_channel: Name | None = "Time"
    event_rate: float | None = Field(default=None, gt=0)
    time_multiplier: float = Field(default=1.0, gt=0)
    time_offsets: dict[Id, float] = Field(default_factory=dict, max_length=128)
    clock_policy: Literal["reject", "use_recorded", "unwrap"] = "reject"
    time_min: float | None = None
    time_max: float | None = None
    bins: int = Field(default=256, ge=8, le=4096)
    minimum_events: int = Field(default=1, ge=1, le=1000000)
    statistic: Literal["median", "mean", "geometric_mean", "percentile", "percent_positive"] = (
        "median"
    )
    percentile: float = Field(default=50.0, ge=0, le=100)
    threshold_mode: Literal["absolute", "baseline_percentile"] = "absolute"
    threshold: float = 0.0
    baseline: KineticsRange | None = None
    baseline_percentile: float = Field(default=95.0, ge=0, le=100)
    above_threshold_only: bool = False
    smoothing: Literal["none", "moving_average", "gaussian"] = "none"
    smoothing_width: int = Field(default=5, ge=1, le=255)
    gaussian_sigma: float = Field(default=1.0, gt=0, le=100)
    ranges: list[KineticsRange] = Field(default_factory=list, max_length=32)
    create_range_gates: bool = True
    create_responder_gates: bool = False
    replace_result_id: Id | None = None

    @model_validator(mode="after")
    def valid_settings(self):
        samples = {v.sample_id for v in self.inputs}
        if len(samples) != len(self.inputs):
            raise ValueError("Select each kinetics sample only once")
        if not set(self.time_offsets) <= samples:
            raise ValueError("Time alignment offsets must refer to selected samples")
        if self.time_channel is None and self.event_rate is None:
            raise ValueError("Estimated time requires an explicit event rate in events/second")
        if self.time_channel is not None and self.event_rate is not None:
            raise ValueError("Choose a time parameter or an assumed event rate")
        if (
            self.time_min is not None
            and self.time_max is not None
            and self.time_min >= self.time_max
        ):
            raise ValueError("Time minimum must be below its maximum")
        if self.threshold_mode == "baseline_percentile" and self.baseline is None:
            raise ValueError("A baseline-relative threshold requires a reference time range")
        if self.statistic == "percent_positive" and self.above_threshold_only:
            raise ValueError("Responder percentages require all finite events as the denominator")
        if self.smoothing_width % 2 == 0:
            raise ValueError("The smoothing window must contain an odd number of bins")
        if len({r.id for r in self.ranges}) != len(self.ranges):
            raise ValueError("Time range IDs must be unique")
        if len({r.name for r in self.ranges}) != len(self.ranges):
            raise ValueError("Time range names must be unique")
        return self


class KineticsBin(Model):
    index: int = Field(ge=0, le=4095)
    start: float
    end: float
    center: float
    population_count: int = Field(ge=0)
    finite_count: int = Field(ge=0)
    selected_count: int | None = Field(default=None, ge=0)
    responder_count: int | None = Field(default=None, ge=0)
    raw_value: float | None = None
    value: float | None = None

    @model_validator(mode="after")
    def consistent(self):
        if not self.start <= self.center <= self.end or self.start >= self.end:
            raise ValueError("Kinetics bin coordinates must be ordered")
        if not (
            self.finite_count <= self.population_count
            and (self.selected_count is None or self.selected_count <= self.finite_count)
            and (self.responder_count is None or self.responder_count <= self.finite_count)
        ):
            raise ValueError("Kinetics bin counts are inconsistent")
        return self


class KineticsData(Model):
    sample_id: Id
    event_count: int = Field(ge=0)
    population_count: int = Field(ge=0)
    time_count: int = Field(ge=0)
    finite_count: int = Field(ge=0)
    represented_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def consistent(self):
        if not (
            self.represented_count
            <= self.finite_count
            <= self.time_count
            <= self.population_count
            <= self.event_count
        ):
            raise ValueError("Kinetics event counts are inconsistent")
        return self


class KineticsRangeSummary(KineticsRange):
    population_count: int = Field(default=0, ge=0)
    finite_count: int = Field(default=0, ge=0)
    selected_count: int | None = Field(default=None, ge=0)
    responder_count: int | None = Field(default=None, ge=0)
    curve_points: int = Field(default=0, ge=0)
    peak_time: float | None = None
    peak: float | None = None
    mean: float | None = None
    slope: float | None = None
    auc: float | None = None
    covered_duration: float | None = Field(default=0.0, ge=0)
    duration: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent_counts(self):
        if not (
            self.finite_count <= self.population_count
            and (self.selected_count is None or self.selected_count <= self.finite_count)
            and (self.responder_count is None or self.responder_count <= self.finite_count)
        ):
            raise ValueError("Kinetics range counts are inconsistent")
        if (self.peak is None) != (self.peak_time is None):
            raise ValueError("A kinetics peak must include its time and value")
        return self


class KineticsFit(Model):
    sample_id: Id
    gate_id: Id | None = None
    time_domain: tuple[float, float] | None = None
    threshold: float | None = None
    baseline_count: int = Field(default=0, ge=0)
    reset_count: int = Field(default=0, ge=0)
    repeated_timestamps: int = Field(default=0, ge=0)
    bins: list[KineticsBin] = Field(default_factory=list, max_length=4096)
    ranges: list[KineticsRangeSummary] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_bins(self):
        if self.time_domain is None:
            if self.bins:
                raise ValueError("Kinetics bins require a time domain")
        else:
            if self.time_domain[0] >= self.time_domain[1]:
                raise ValueError("Kinetics time domain must be increasing")
            if not self.bins:
                raise ValueError("An increasing kinetics domain requires its measured bins")
            if self.bins:
                if (
                    self.bins[0].start != self.time_domain[0]
                    or self.bins[-1].end != self.time_domain[1]
                ):
                    raise ValueError("Kinetics bins must cover the recorded time domain")
                for i, b in enumerate(self.bins):
                    if b.index != i or (i and self.bins[i - 1].end != b.start):
                        raise ValueError("Kinetics bins must be consecutive and contiguous")
        if len({r.id for r in self.ranges}) != len(self.ranges):
            raise ValueError("Kinetics summary range IDs must be unique")
        return self


class KineticsResult(Model):
    id: Id
    request: KineticsRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: dict
    columns: list[Name] = Field(min_length=3, max_length=3)
    data: list[KineticsData] = Field(min_length=1, max_length=128)
    fits: list[KineticsFit] = Field(min_length=1, max_length=128)
    versions: dict[str, str]
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def consistent_sources(self):
        if len(set(self.columns)) != 3:
            raise ValueError("Kinetics time, signal and source output names must be distinct")
        requested = {v.sample_id: v.gate_id for v in self.request.inputs}
        if (
            {v.sample_id for v in self.data} != set(requested)
            or len(self.data) != len(requested)
            or len(self.fits) != len(requested)
            or {f.sample_id: f.gate_id for f in self.fits} != requested
        ):
            raise ValueError("Kinetics reports must retain every original input exactly once")
        for fit, data in zip(self.fits, self.data, strict=True):
            if fit.sample_id != data.sample_id:
                raise ValueError("Kinetics fits and event data must use the same order")
            if fit.bins:
                if len(fit.bins) != self.request.bins:
                    raise ValueError("Kinetics bin count does not match its settings")
                if sum(b.finite_count for b in fit.bins) != data.represented_count:
                    raise ValueError("Kinetics bins do not match their represented event count")
            if any(r.population_count > data.time_count for r in fit.ranges):
                raise ValueError("A kinetics range cannot contain more than its timed population")
        return self


QualityExclusion = Literal["nonfinite", "time", "saturation", "pulse"]


class QualityRequest(Model):
    revision: int = Field(ge=0)
    name: Name = "Acquisition QC"
    algorithm: Literal["acquisition_qc"] = "acquisition_qc"
    sample_id: Id
    gate_id: Id | None = None
    channels: list[Name] = Field(min_length=1, max_length=32)
    time_channel: Name | None = None
    compensated: bool = True
    use_transforms: bool = True
    bin_events: int = Field(default=500, ge=50, le=100000)
    min_bin_events: int = Field(default=50, ge=10, le=100000)
    score_threshold: float = Field(default=6.0, ge=2, le=20)
    signal_min_shift: float = Field(default=0.1, gt=0, le=2)
    rate_min_fold: float = Field(default=2.0, gt=1, le=20)
    saturation_channels: list[Name] = Field(default_factory=list, max_length=512)
    saturation_fraction: float = Field(default=0.9999, ge=0.9, le=1)
    pulse_area: Name | None = None
    pulse_height: Name | None = None
    pulse_score: float = Field(default=6.0, ge=2, le=20)

    @model_validator(mode="after")
    def valid_settings(self):
        if len(set(self.channels)) != len(self.channels) or len(
            set(self.saturation_channels)
        ) != len(self.saturation_channels):
            raise ValueError("QC channels must be unique")
        if self.min_bin_events > self.bin_events:
            raise ValueError("Minimum signal events cannot exceed events per bin")
        if bool(self.pulse_area) != bool(self.pulse_height):
            raise ValueError("Pulse shape requires both an area and a height channel")
        if self.pulse_area and self.pulse_area == self.pulse_height:
            raise ValueError("Select distinct area and height channels")
        if self.time_channel in self.channels:
            raise ValueError("Time is an acquisition coordinate, not a stability marker")
        return self


class QualityBin(Model):
    index: int = Field(ge=0, le=1023)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    population_count: int = Field(ge=0)
    time_start: float | None = None
    time_end: float | None = None
    rate: float | None = None
    rate_score: float | None = None
    signals: dict[str, dict] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    suggested: bool = False


class QualityData(Model):
    event_count: int = Field(ge=0)
    population_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    flag_counts: dict[QualityExclusion, int] = Field(default_factory=dict)


class QualityResult(Model):
    id: Id
    request: QualityRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: dict
    data: QualityData
    bins: list[QualityBin] = Field(max_length=1024)
    diagnostics: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_result(self):
        import hashlib
        import json

        digest = hashlib.sha256(
            json.dumps(self.input_snapshot, sort_keys=True).encode()
        ).hexdigest()
        if digest != self.input_hash:
            raise ValueError("QC input snapshot does not match its scientific fingerprint")
        if self.data.population_count > self.data.event_count or any(
            v < 0 or v > self.data.population_count for v in self.data.flag_counts.values()
        ):
            raise ValueError("QC event counts are inconsistent")
        end = 0
        for index, segment in enumerate(self.bins):
            if segment.index != index or segment.start != end or segment.end <= end:
                raise ValueError("QC bins must partition acquisition order without gaps")
            if segment.population_count > segment.end - segment.start:
                raise ValueError("QC bin population count exceeds its acquired events")
            end = segment.end
        if (
            end != self.data.event_count
            or sum(b.population_count for b in self.bins) != self.data.population_count
        ):
            raise ValueError("QC bins do not match their event data")
        return self


class QualityApply(Model):
    revision: int = Field(ge=0)
    name: Name = "QC clean"
    excluded_bins: list[Annotated[int, Field(ge=0, le=1023)]] = Field(
        default_factory=list, max_length=1024
    )
    exclusions: list[QualityExclusion] = Field(default_factory=lambda: ["nonfinite", "time"])
    create_rejected: bool = True

    @model_validator(mode="after")
    def unique_choices(self):
        if len(set(self.excluded_bins)) != len(self.excluded_bins) or len(
            set(self.exclusions)
        ) != len(self.exclusions):
            raise ValueError("QC review choices must be unique")
        return self


class FitConstraint(Model):
    fixed: float | None = Field(default=None, gt=0)
    minimum: float | None = Field(default=None, gt=0)
    maximum: float | None = Field(default=None, gt=0)
    initial: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_limits(self):
        if self.fixed is not None and (self.minimum is not None or self.maximum is not None):
            raise ValueError("A fixed fit parameter cannot also have range constraints")
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("Fit constraint minimum must be below its maximum")
        if self.initial is not None and (
            (self.minimum is not None and self.initial < self.minimum)
            or (self.maximum is not None and self.initial > self.maximum)
        ):
            raise ValueError("Initial fit value must be inside its constraint range")
        return self


class CellCycleRequest(Model):
    revision: int = Field(ge=0)
    name: str = Field(default="Cell cycle", min_length=1, max_length=125)
    algorithm: Literal["cell_cycle"] = "cell_cycle"
    method: Literal["watson", "djf"] = "djf"
    inputs: list[AnalysisInput] = Field(min_length=1, max_length=128)
    channel: Name
    compensated: bool = True
    bins: int = Field(default=256, ge=64, le=1024)
    range_min: float | None = Field(default=None, ge=0)
    range_max: float | None = Field(default=None, gt=0)
    g1_mean: FitConstraint = Field(default_factory=FitConstraint)
    g2_mean: FitConstraint = Field(default_factory=FitConstraint)
    g1_cv: FitConstraint = Field(default_factory=FitConstraint)
    g2_cv: FitConstraint = Field(default_factory=FitConstraint)
    peak_ratio: FitConstraint = Field(default_factory=FitConstraint)
    linked_cv: Literal["none", "g2_to_g1", "g1_to_g2"] = "none"
    synchronous_s: bool = False
    s_peak_initial: float | None = Field(default=None, gt=0)
    objective: Literal["poisson", "weighted_least_squares"] = "poisson"
    maximum_evaluations: int = Field(default=800, ge=100, le=3000)
    smoothing: float = Field(default=0.75, ge=0, le=3)
    create_phase_gates: bool = True
    replace_result_id: Id | None = None

    @model_validator(mode="after")
    def valid_fit(self):
        if len({v.sample_id for v in self.inputs}) != len(self.inputs):
            raise ValueError("Select each cell-cycle sample only once")
        if (
            self.range_min is not None
            and self.range_max is not None
            and self.range_min >= self.range_max
        ):
            raise ValueError("Cell-cycle fit range minimum must be below its maximum")
        if self.method == "watson" and self.synchronous_s:
            raise ValueError("The synchronous S-phase component is available for DJF")
        for constraint in (self.g1_cv, self.g2_cv):
            if any(v is not None and not 0.3 <= v <= 40 for v in constraint.model_dump().values()):
                raise ValueError("Peak CV constraints must be between 0.3 and 40 percent")
        if any(v is not None and not 1.2 <= v <= 3 for v in self.peak_ratio.model_dump().values()):
            raise ValueError("G2/G1 ratio constraints must be between 1.2 and 3")
        if (
            self.linked_cv != "none"
            and self.g1_cv.fixed is not None
            and self.g2_cv.fixed is not None
            and self.g1_cv.fixed != self.g2_cv.fixed
        ):
            raise ValueError("Linked peak CVs cannot have different fixed values")
        return self


class FitEventData(Model):
    sample_id: Id
    event_count: int = Field(ge=0)
    population_count: int = Field(ge=0)
    finite_count: int = Field(ge=0)
    fitted_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_counts(self):
        if not self.fitted_count <= self.finite_count <= self.population_count <= self.event_count:
            raise ValueError("Fitted event counts are inconsistent")
        return self


class CellCycleData(FitEventData):
    pass


class CellCycleFit(Model):
    sample_id: Id
    data: CellCycleData
    range_min: float = Field(ge=0)
    range_max: float = Field(gt=0)
    edges: list[float] = Field(min_length=65, max_length=1025)
    observed: list[int] = Field(min_length=64, max_length=1024)
    components: list[list[float]] = Field(min_length=3, max_length=3)
    weights: list[list[float]] = Field(min_length=3, max_length=3)
    parameters: dict[str, float]
    fractions: list[float] = Field(min_length=3, max_length=3)
    expected_counts: list[float] = Field(min_length=3, max_length=3)
    assigned_counts: list[int] = Field(min_length=3, max_length=3)
    diagnostics: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_histogram(self):
        n = len(self.observed)
        if self.sample_id != self.data.sample_id or self.range_min >= self.range_max:
            raise ValueError("Cell-cycle fit sample/range is inconsistent")
        if (
            len(self.edges) != n + 1
            or self.edges[0] != self.range_min
            or self.edges[-1] != self.range_max
            or any(a >= b for a, b in zip(self.edges[:-1], self.edges[1:], strict=True))
        ):
            raise ValueError("Cell-cycle histogram edges must partition its fit range")
        if any(v < 0 for v in self.observed) or sum(self.observed) != self.data.fitted_count:
            raise ValueError("Cell-cycle histogram does not match fitted event counts")
        if any(
            len(row) != n or any(not math.isfinite(v) or v < 0 for v in row)
            for row in self.components + self.weights
        ):
            raise ValueError("Cell-cycle curves must have finite, nonnegative histogram values")
        if any(v > 1 for row in self.weights for v in row) or any(
            abs(sum(row[i] for row in self.weights) - 1) > 1e-6 for i in range(n)
        ):
            raise ValueError("Cell-cycle phase probabilities must sum to one in each bin")
        if (
            any(not math.isfinite(v) for v in self.parameters.values())
            or any(v < 0 or v > 1 for v in self.fractions)
            or abs(sum(self.fractions) - 1) > 1e-6
        ):
            raise ValueError("Invalid cell-cycle parameters or phase fractions")
        if (
            sum(self.assigned_counts) != self.data.fitted_count
            or any(v < 0 for v in self.assigned_counts)
            or any(not math.isfinite(v) or v < 0 for v in self.expected_counts)
            or abs(sum(self.expected_counts) - self.data.fitted_count) > 0.01
        ):
            raise ValueError("Cell-cycle phase event counts are inconsistent")
        total = self.data.fitted_count
        masses = [math.fsum(row) for row in self.components]
        if not math.isclose(math.fsum(masses), total, abs_tol=0.01, rel_tol=1e-7):
            raise ValueError("Cell-cycle fitted curves must sum to the modeled event count")
        for phase in range(3):
            if not math.isclose(masses[phase] / max(total, 1), self.fractions[phase], abs_tol=1e-6):
                raise ValueError("Cell-cycle fractions do not match their fitted component areas")
            expected = math.fsum(self.observed[i] * self.weights[phase][i] for i in range(n))
            if not math.isclose(expected, self.expected_counts[phase], abs_tol=0.01, rel_tol=1e-6):
                raise ValueError("Cell-cycle expected events do not match histogram probabilities")
        return self


class CellCycleResult(Model):
    id: Id
    request: CellCycleRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: dict
    columns: list[Name] = Field(min_length=4, max_length=4)
    fits: list[CellCycleFit] = Field(min_length=1, max_length=128)
    versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)

    @property
    def data(self):
        return [fit.data for fit in self.fits]

    @model_validator(mode="after")
    def valid_result(self):
        import hashlib
        import json

        if (
            hashlib.sha256(json.dumps(self.input_snapshot, sort_keys=True).encode()).hexdigest()
            != self.input_hash
        ):
            raise ValueError("Cell-cycle input snapshot does not match its scientific fingerprint")
        if (
            len(set(self.columns)) != 4
            or len({fit.sample_id for fit in self.fits}) != len(self.fits)
            or {fit.sample_id for fit in self.fits} != {v.sample_id for v in self.request.inputs}
        ):
            raise ValueError("Cell-cycle results must match their input samples and unique outputs")
        if any(len(fit.observed) != self.request.bins for fit in self.fits):
            raise ValueError("Cell-cycle histograms must use the requested bin count")
        return self


class ProliferationRequest(Model):
    revision: int = Field(ge=0)
    name: str = Field(default="Proliferation", min_length=1, max_length=125)
    algorithm: Literal["proliferation"] = "proliferation"
    inputs: list[AnalysisInput] = Field(min_length=1, max_length=128)
    channel: Name
    compensated: bool = True
    distribution: Literal["lognormal", "gaussian"] = "lognormal"
    histogram_space: Literal["log2", "linear"] = "log2"
    generations: int = Field(default=6, ge=0, le=12)
    bins: int = Field(default=512, ge=64, le=2048)
    range_min: float | None = None
    range_max: float | None = None
    undivided_control: AnalysisInput | None = None
    control_mode: Literal["initial", "fix_mean", "fix_mean_cv"] = "fix_mean"
    control_range_min: float | None = Field(default=None, gt=0)
    control_range_max: float | None = Field(default=None, gt=0)
    undivided_mean: FitConstraint = Field(default_factory=FitConstraint)
    peak_ratio: FitConstraint = Field(default_factory=lambda: FitConstraint(fixed=0.5))
    dye_cv: FitConstraint = Field(default_factory=FitConstraint)
    autofluorescence_control: AnalysisInput | None = None
    background: float = Field(default=0.0, ge=0)
    background_sd: float = Field(default=0.0, ge=0)
    objective: Literal["poisson", "weighted_least_squares"] = "poisson"
    maximum_evaluations: int = Field(default=800, ge=100, le=3000)
    create_generation_gates: bool = True
    replace_result_id: Id | None = None

    @model_validator(mode="after")
    def valid_fit(self):
        if len({v.sample_id for v in self.inputs}) != len(self.inputs):
            raise ValueError("Select each proliferation sample only once")
        for lower, upper in (
            (self.range_min, self.range_max),
            (self.control_range_min, self.control_range_max),
        ):
            if lower is not None and upper is not None and lower >= upper:
                raise ValueError("Fit/control range minimum must be below its maximum")
        if self.histogram_space == "log2" and any(
            v is not None and v <= 0 for v in (self.range_min, self.range_max)
        ):
            raise ValueError("A logarithmic histogram requires a positive fit range")
        if not self.undivided_control and (
            self.undivided_mean.fixed is None and self.undivided_mean.initial is None
        ):
            raise ValueError("Select an undivided control or provide the generation-zero intensity")
        if self.undivided_control and self.control_mode != "initial":
            if any(v is not None for v in self.undivided_mean.model_dump().values()):
                raise ValueError(
                    "A control-fixed generation-zero peak cannot have manual constraints"
                )
            if self.control_mode == "fix_mean_cv" and any(
                v is not None for v in self.dye_cv.model_dump().values()
            ):
                raise ValueError("A control-fixed dye CV cannot also have manual constraints")
        if not self.undivided_control and (
            self.control_range_min is not None or self.control_range_max is not None
        ):
            raise ValueError("An undivided control is required for its calibration range")
        for spec, lower, upper, label in (
            (self.peak_ratio, 0.25, 0.75, "Peak ratio"),
            (self.dye_cv, 0.5, 100, "Dye CV"),
        ):
            if any(v is not None and not lower <= v <= upper for v in spec.model_dump().values()):
                raise ValueError(f"{label} constraints must be between {lower} and {upper}")
        return self


class ProliferationStatistics(Model):
    total_events: float = Field(gt=0)
    precursor_equivalents: float = Field(gt=0)
    responding_precursor_equivalents: float = Field(ge=0)
    division_equivalents: float = Field(ge=0)
    precursor_frequency: float = Field(ge=0, le=1)
    division_index: float = Field(ge=0)
    proliferation_index: float | None = Field(default=None, ge=1)
    expansion_index: float = Field(ge=1)
    replication_index: float | None = Field(default=None, ge=2)
    observed_divided_fraction: float = Field(ge=0, le=1)


def proliferation_statistics(counts):
    """Binary-division precursor weighting, independent of the fitting model."""
    total = math.fsum(counts)
    if total <= 0 or any(not math.isfinite(v) or v < 0 for v in counts):
        raise ValueError("Generation counts must be finite, nonnegative and have a positive total")
    precursors = [v / 2**i for i, v in enumerate(counts)]
    original = math.fsum(precursors)
    responding = math.fsum(precursors[1:])
    divisions = math.fsum(i * v for i, v in enumerate(precursors))
    divided = math.fsum(counts[1:])
    return ProliferationStatistics(
        total_events=total,
        precursor_equivalents=original,
        responding_precursor_equivalents=responding,
        division_equivalents=divisions,
        precursor_frequency=min(1, responding / original),
        division_index=divisions / original,
        proliferation_index=max(1, divisions / responding) if responding else None,
        expansion_index=max(1, total / original),
        replication_index=max(2, divided / responding) if responding else None,
        observed_divided_fraction=min(1, divided / total),
    )


class ProliferationData(FitEventData):
    pass


class ProliferationFit(Model):
    sample_id: Id
    data: ProliferationData
    range_min: float
    range_max: float
    edges: list[float] = Field(min_length=65, max_length=2049)
    observed: list[int] = Field(min_length=64, max_length=2048)
    components: list[list[float]] = Field(min_length=1, max_length=13)
    weights: list[list[float]] = Field(min_length=1, max_length=13)
    parameters: dict[str, float]
    peak_locations: list[float] = Field(min_length=1, max_length=13)
    fractions: list[float] = Field(min_length=1, max_length=13)
    expected_counts: list[float] = Field(min_length=1, max_length=13)
    assigned_counts: list[int] = Field(min_length=1, max_length=13)
    statistics: ProliferationStatistics
    diagnostics: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_histogram(self):
        n, k = len(self.observed), len(self.fractions)
        if self.sample_id != self.data.sample_id or self.range_min >= self.range_max:
            raise ValueError("Proliferation fit sample/range is inconsistent")
        if (
            len(self.edges) != n + 1
            or self.edges[0] != self.range_min
            or self.edges[-1] != self.range_max
            or any(a >= b for a, b in zip(self.edges[:-1], self.edges[1:], strict=True))
        ):
            raise ValueError("Proliferation histogram edges must partition its fit range")
        if any(v < 0 for v in self.observed) or sum(self.observed) != self.data.fitted_count:
            raise ValueError("Proliferation histogram does not match fitted event counts")
        if any(
            len(v) != k
            for v in (
                self.components,
                self.weights,
                self.peak_locations,
                self.expected_counts,
                self.assigned_counts,
            )
        ):
            raise ValueError("Proliferation results must describe every modeled generation")
        if any(
            len(row) != n or any(not math.isfinite(v) or v < 0 for v in row)
            for row in self.components + self.weights
        ):
            raise ValueError("Proliferation curves must be finite, nonnegative histogram values")
        if any(v > 1 for row in self.weights for v in row) or any(
            abs(math.fsum(row[i] for row in self.weights) - 1) > 1e-6 for i in range(n)
        ):
            raise ValueError("Generation probabilities must sum to one in each bin")
        if any(not math.isfinite(v) for v in self.parameters.values()) or (
            any(v < 0 or v > 1 for v in self.fractions) or abs(math.fsum(self.fractions) - 1) > 1e-6
        ):
            raise ValueError("Invalid proliferation parameters or generation fractions")
        p = self.parameters
        if (
            set(p) != {"undivided_mean", "peak_ratio", "dye_cv", "background", "background_sd"}
            or p["undivided_mean"] <= p["background"]
            or not 0.25 <= p["peak_ratio"] <= 0.75
            or not 0.5 <= p["dye_cv"] <= 100
            or p["background"] < 0
            or p["background_sd"] < 0
        ):
            raise ValueError("Invalid proliferation peak parameters")
        for i, peak in enumerate(self.peak_locations):
            expected = (
                p["background"] + (p["undivided_mean"] - p["background"]) * p["peak_ratio"] ** i
            )
            if not math.isclose(peak, expected, rel_tol=1e-7, abs_tol=1e-300):
                raise ValueError("Generation peak locations do not match their dye-dilution model")
        total = self.data.fitted_count
        if (
            sum(self.assigned_counts) != total
            or any(v < 0 for v in self.assigned_counts)
            or any(not math.isfinite(v) or v < 0 for v in self.expected_counts)
            or not math.isclose(math.fsum(self.expected_counts), total, abs_tol=0.01, rel_tol=1e-6)
        ):
            raise ValueError("Proliferation generation event counts are inconsistent")
        masses = [math.fsum(row) for row in self.components]
        if not math.isclose(math.fsum(masses), total, abs_tol=0.01, rel_tol=1e-7):
            raise ValueError("Proliferation fitted curves must sum to the modeled event count")
        for i in range(k):
            if not math.isclose(masses[i] / max(total, 1), self.fractions[i], abs_tol=1e-6):
                raise ValueError("Generation fractions do not match fitted component areas")
            expected = math.fsum(self.observed[j] * self.weights[i][j] for j in range(n))
            if not math.isclose(expected, self.expected_counts[i], abs_tol=0.01, rel_tol=1e-6):
                raise ValueError("Expected events do not match generation probabilities")
        expected_stats = proliferation_statistics([v * total for v in self.fractions])
        for key, expected in expected_stats.model_dump().items():
            actual = getattr(self.statistics, key)
            if (expected is None) != (actual is None) or (
                expected is not None
                and not math.isclose(actual, expected, abs_tol=1e-7, rel_tol=1e-7)
            ):
                raise ValueError("Proliferation statistics do not match modeled generation counts")
        return self


class ProliferationResult(Model):
    id: Id
    request: ProliferationRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: dict
    columns: list[Name] = Field(min_length=2, max_length=14)
    fits: list[ProliferationFit] = Field(min_length=1, max_length=128)
    calibration: dict = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)

    @property
    def data(self):
        return [fit.data for fit in self.fits]

    @model_validator(mode="after")
    def valid_result(self):
        import hashlib
        import json

        if (
            hashlib.sha256(json.dumps(self.input_snapshot, sort_keys=True).encode()).hexdigest()
            != self.input_hash
        ):
            raise ValueError(
                "Proliferation input snapshot does not match its scientific fingerprint"
            )
        k = self.request.generations + 1
        if (
            len(set(self.columns)) != k + 1
            or len(self.columns) != k + 1
            or (
                len({fit.sample_id for fit in self.fits}) != len(self.fits)
                or {fit.sample_id for fit in self.fits}
                != {v.sample_id for v in self.request.inputs}
            )
        ):
            raise ValueError(
                "Proliferation results must match their input samples and unique outputs"
            )
        if any(
            len(fit.observed) != self.request.bins or len(fit.fractions) != k for fit in self.fits
        ):
            raise ValueError("Proliferation histograms must use the requested bins and generations")
        if self.request.histogram_space == "log2" and any(fit.range_min <= 0 for fit in self.fits):
            raise ValueError("Logarithmic proliferation ranges must be positive")
        return self


class ConcatenationSource(Model):
    index: int = Field(ge=1, le=128)
    sample_id: Id
    sample_name: Name
    gate_id: Id | None = None
    offset: int = Field(ge=0)
    count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    parameters: dict[Name, Name]
    snapshot: dict


class ConcatenationProvenance(Model):
    version: Literal[1] = 1
    workspace_id: Id
    revision: int = Field(ge=0)
    created_at: str
    values: Literal["raw", "compensated", "scale"]
    origins_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sources: list[ConcatenationSource] = Field(min_length=1, max_length=128)
    keywords: dict[str, list[str | None]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_sources(self):
        offset = 0
        indices = set()
        for source in self.sources:
            if (
                source.index in indices
                or source.offset != offset
                or source.count > source.event_count
            ):
                raise ValueError(
                    "Concatenation sources must have unique indices and contiguous rows"
                )
            indices.add(source.index)
            offset += source.count
        return self


class EventExportProvenance(Model):
    version: Literal[1] = 1
    exported_at: str
    values: Literal["raw", "compensated", "scale"]
    data_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    metadata_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_snapshot: dict


class Sample(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    event_count: int = Field(ge=0)
    channels: list[Channel] = Field(min_length=1, max_length=512)
    metadata: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    compensation_id: Id | None = None
    sha256: str = ""
    source: str = ""
    derived_parameters: list[DerivedParameter] = Field(default_factory=list, max_length=128)
    computed_parameters: list[ComputedParameter] = Field(default_factory=list, max_length=128)
    unmixed_parameters: list[Name] = Field(default_factory=list, max_length=512)
    aliases: dict[Name, Name] = Field(default_factory=dict, max_length=128)
    concatenation: ConcatenationProvenance | None = None
    event_export: EventExportProvenance | None = None

    @model_serializer(mode="wrap")
    def serialize_legacy(self, handler):
        value = handler(self)
        if self.concatenation is None:
            value.pop("concatenation", None)
        if self.event_export is None:
            value.pop("event_export", None)
        if not self.aliases:
            value.pop("aliases", None)
        return value

    @property
    def acquisition_channels(self) -> list[Channel]:
        virtual = (
            {d.name for d in self.derived_parameters}
            | {p.name for p in self.computed_parameters}
            | set(self.unmixed_parameters)
            | self.aliases.keys()
        )
        return [c for c in self.channels if c.name not in virtual]

    @model_validator(mode="after")
    def unique_channels(self):
        if (
            self.concatenation
            and sum(s.count for s in self.concatenation.sources) != self.event_count
        ):
            raise ValueError("Concatenation source counts must equal the sample event count")
        if len({c.name for c in self.channels}) != len(self.channels):
            raise ValueError("Channel names must be unique")
        derived = {d.name: d for d in self.derived_parameters}
        if len(derived) != len(self.derived_parameters):
            raise ValueError("Derived parameter names must be unique")
        computed = {p.name: p for p in self.computed_parameters}
        if len(computed) != len(self.computed_parameters) or computed.keys() & derived.keys():
            raise ValueError("Computed parameter names must be unique")
        if len(set(self.unmixed_parameters)) != len(self.unmixed_parameters) or set(
            self.unmixed_parameters
        ) & (derived.keys() | computed.keys()):
            raise ValueError(
                "Unmixed output names must be unique and separate from derived/computed parameters"
            )
        other_virtual = derived.keys() | computed.keys() | set(self.unmixed_parameters)
        if self.aliases.keys() & other_virtual:
            raise ValueError("Aliases must be separate from other virtual parameters")
        extra_names = other_virtual | self.aliases.keys()
        base_names = {c.name for c in self.channels[: len(self.channels) - len(extra_names)]}
        if base_names & extra_names or not extra_names <= {c.name for c in self.channels}:
            raise ValueError("Derived channels must be appended after acquisition channels")
        if not set(self.aliases.values()) <= (base_names | set(self.unmixed_parameters)):
            raise ValueError("Aliases must reference acquired channels or unmixed outputs directly")
        visited, visiting = set(), set()

        def visit(name):
            if name in visiting:
                raise ValueError("Derived parameter dependencies contain a cycle")
            if (
                name in visited
                or name in base_names
                or name in computed
                or name in self.unmixed_parameters
                or name in self.aliases
            ):
                return
            if name not in derived:
                raise ValueError(f"Formula references missing channel {name}")
            visiting.add(name)
            for dep in parse(derived[name].expression)[1]:
                visit(dep)
            visiting.remove(name)
            visited.add(name)

        for name in derived:
            visit(name)
        return self


class GateDimension(Model):
    channel: Name
    transform: Transform = Field(default_factory=Transform)
    compensation_ref: Literal["sample", "uncompensated", "FCS"] | Id = "sample"
    minimum: float | None = None
    maximum: float | None = None
    ratio_channels: tuple[Name, Name] | None = None
    ratio_a: float = 1
    ratio_b: float = 0
    ratio_c: float = 0
    ratio_bound_min: float | None = None
    ratio_bound_max: float | None = None

    @model_validator(mode="after")
    def valid_bounds(self):
        if (
            self.ratio_bound_min is not None
            and self.ratio_bound_max is not None
            and self.ratio_bound_min > self.ratio_bound_max
        ):
            raise ValueError("Ratio bound minimum cannot exceed its maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Dimension minimum cannot exceed its maximum")
        return self


class PlotDimension(GateDimension):
    """A scientific coordinate definition, without a population's gate limits."""

    @model_validator(mode="after")
    def no_gate_limits(self):
        if self.minimum is not None or self.maximum is not None:
            raise ValueError("Plot dimensions cannot include gate minimum or maximum limits")
        return self


class MagneticGate(Model):
    max_shift: float = Field(default=2, gt=0, le=4)
    algorithm: Literal["local-window-count-v1"] = "local-window-count-v1"


class SpiderGeometry(Model):
    center: tuple[float, float]
    scale: tuple[Annotated[float, Field(gt=0)], Annotated[float, Field(gt=0)]]
    angles: tuple[float, float, float, float] = (0, math.pi / 2, math.pi, 3 * math.pi / 2)

    @model_validator(mode="after")
    def ordered_arms(self):
        tau = 2 * math.pi
        if any(not 0 <= value < tau for value in self.angles):
            raise ValueError("Spider angles must be between zero and 2π radians")
        offsets = [(angle - self.angles[0]) % tau for angle in self.angles]
        gaps = [offsets[i + 1] - offsets[i] for i in range(3)] + [tau - offsets[3]]
        if min(gaps) < 1e-6:
            raise ValueError("Spider arms must retain their circular order and distinct directions")
        return self


class CurlyGeometry(Model):
    center: tuple[float, float]
    coefficients: tuple[Annotated[float, Field(ge=0)], Annotated[float, Field(ge=0)]] = (1, 1)
    convention: Literal["sqrt-positive-intensity-v1"] = "sqrt-positive-intensity-v1"


class GatePartition(Model):
    id: Id = Field(default_factory=new_id)
    kind: Literal["bisector", "quadrant", "spider", "curly"]
    member: Literal[1, 2, 3, 4]

    @model_validator(mode="after")
    def valid_member(self):
        if self.kind == "bisector" and self.member > 2:
            raise ValueError("A bisector has negative and positive members only")
        return self

    def high(self, axis: int) -> bool:
        if self.kind == "bisector":
            return self.member == 2
        return self.member in ({2, 3} if axis == 0 else {1, 2})


class PopulationMembership(Model):
    id: Id = Field(default_factory=new_id)
    sample_id: Id
    raw_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    event_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    acquisition_channels: list[Name] = Field(min_length=1, max_length=512)
    encoding: Literal["packed-little"] = "packed-little"

    @model_validator(mode="after")
    def valid_count(self):
        if self.selected_count > self.event_count:
            raise ValueError("Captured population count cannot exceed acquired events")
        return self


class Gate(Model):
    id: Id = Field(default_factory=new_id)
    sample_id: Id
    name: Name
    parent_id: Id | None = None
    kind: Literal[
        "rectangle",
        "polygon",
        "ellipse",
        "range",
        "quadrant",
        "boolean",
        "hyperrectangle",
        "ellipsoid",
        "container",
        "quality",
        "membership",
        "spider",
        "curly",
    ]
    x: str | None = None
    y: str | None = None
    x_transform: Transform = Field(default_factory=Transform)
    y_transform: Transform = Field(default_factory=Transform)
    bounds: list[float] = Field(default_factory=list)
    vertices: list[tuple[float, float]] = Field(default_factory=list, max_length=2000)
    holes: list[Annotated[list[tuple[float, float]], Field(min_length=3, max_length=2000)]] = Field(
        default_factory=list, max_length=128
    )
    center: tuple[float, float] | None = None
    radii: tuple[float, float] | None = None
    angle: float = 0
    quadrant: Literal[1, 2, 3, 4] = 1
    operation: Literal["and", "or", "not", "xor"] = "and"
    operands: list[Id] = Field(default_factory=list)
    color: str = Field(default="#38d9ba", pattern=r"^#[0-9a-fA-F]{6}$")
    dimensions: list[GateDimension] = Field(default_factory=list, max_length=512)
    covariance: list[list[float]] = Field(default_factory=list)
    coordinates: list[float] = Field(default_factory=list, max_length=512)
    distance_square: float = Field(default=1, gt=0)
    complement: bool = False
    operand_complements: list[bool] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    quality_id: Id | None = None
    quality_excluded_bins: list[Annotated[int, Field(ge=0, le=1023)]] = Field(
        default_factory=list, max_length=1024
    )
    quality_exclusions: list[QualityExclusion] = Field(default_factory=list)
    quality_keep: bool = True
    membership: PopulationMembership | None = None
    magnetic: MagneticGate | None = None
    partition: GatePartition | None = None
    spider: SpiderGeometry | None = None
    curly: CurlyGeometry | None = None

    @model_serializer(mode="wrap")
    def omit_inactive_magnetic(self, serializer):
        value = serializer(self)
        if self.magnetic is None:
            value.pop("magnetic", None)
        if self.partition is None:
            value.pop("partition", None)
        if not self.holes:
            value.pop("holes", None)
        if self.spider is None:
            value.pop("spider", None)
        if self.curly is None:
            value.pop("curly", None)
        if self.membership is None:
            value.pop("membership", None)
        return value

    @model_validator(mode="after")
    def valid_shape(self):
        if self.kind == "membership":
            if (
                self.membership is None
                or self.parent_id
                or self.x
                or self.y
                or self.dimensions
                or self.operands
                or self.quality_id
                or self.quality_excluded_bins
                or self.quality_exclusions
                or not self.quality_keep
                or self.magnetic
                or self.partition
                or self.spider
                or self.curly
                or self.bounds
                or self.vertices
                or self.holes
                or self.center
                or self.radii
                or self.coordinates
                or self.covariance
            ):
                raise ValueError("Captured populations require membership data without geometry")
            return self
        if self.membership is not None:
            raise ValueError("Event membership data require a captured population")
        if self.kind == "curly":
            if (
                self.curly is None
                or self.spider is not None
                or self.partition is None
                or self.partition.kind != "curly"
                or len(self.dimensions) != 2
                or self.complement
                or self.magnetic
                or any(d.minimum is not None or d.maximum is not None for d in self.dimensions)
                or self.bounds
                or self.vertices
                or self.holes
            ):
                raise ValueError(
                    "Curly populations require a linked two-dimensional noise-boundary family"
                )
            if any(
                d.transform.bound_min is not None
                or d.transform.bound_max is not None
                or d.transform.kind in {"wsp_log", "wsp_biex"}
                for d in self.dimensions
            ):
                raise ValueError("Curly centers require invertible, unclipped axis transforms")
            return self
        if self.curly is not None or (self.partition and self.partition.kind == "curly"):
            raise ValueError("Curly noise geometry requires a curly population")
        if self.kind == "spider":
            if (
                self.spider is None
                or self.partition is None
                or self.partition.kind != "spider"
                or len(self.dimensions) != 2
                or self.complement
                or self.magnetic
                or any(d.minimum is not None or d.maximum is not None for d in self.dimensions)
                or self.bounds
                or self.vertices
                or self.holes
            ):
                raise ValueError(
                    "Spider populations require a linked two-dimensional divider family"
                )
            return self
        if self.spider is not None or (self.partition and self.partition.kind == "spider"):
            raise ValueError("Spider divider geometry requires a spider population")
        if self.holes:
            if self.kind != "polygon":
                raise ValueError("Excluded polygon rings require a polygon gate")
            if any(len(set(ring)) < 3 for ring in self.holes):
                raise ValueError("Each excluded ring requires three distinct vertices")
            if len(self.vertices) + sum(map(len, self.holes)) > 2000:
                raise ValueError("The complete polygon outline exceeds 2,000 vertices")
        if self.partition:
            size = 1 if self.partition.kind == "bisector" else 2
            if (
                self.kind != "hyperrectangle"
                or len(self.dimensions) != size
                or self.complement
                or self.magnetic
            ):
                raise ValueError("Linked partitions require uncompounded open intervals")
            for axis, dimension in enumerate(self.dimensions):
                high = self.partition.high(axis)
                threshold = dimension.minimum if high else dimension.maximum
                open_end = dimension.maximum if high else dimension.minimum
                if threshold is None or open_end is not None:
                    raise ValueError("Each partition axis requires one threshold and one open end")
        if self.kind != "quality" and (
            self.quality_id
            or self.quality_excluded_bins
            or self.quality_exclusions
            or not self.quality_keep
        ):
            raise ValueError("QC review fields require a quality gate")
        if self.kind == "quality":
            if not self.quality_id or self.complement:
                raise ValueError("QC gates require a result and use retained/rejected selection")
            if len(set(self.quality_excluded_bins)) != len(self.quality_excluded_bins) or len(
                set(self.quality_exclusions)
            ) != len(self.quality_exclusions):
                raise ValueError("QC gate review choices must be unique")
            return self
        if self.kind == "boolean":
            if not self.operands or (self.operation == "not" and len(self.operands) != 1):
                raise ValueError("Boolean gates require operands; NOT requires exactly one")
            if self.operand_complements and len(self.operand_complements) != len(self.operands):
                raise ValueError("Operand complement flags must match Boolean operands")
            return self
        if self.kind == "container":
            return self
        if self.dimensions:
            if self.kind not in {"hyperrectangle", "polygon", "ellipsoid"}:
                raise ValueError(
                    "Explicit dimensions require a hyperrectangle, polygon or ellipsoid"
                )
            if self.kind == "ellipsoid":
                import numpy as np

                values = np.asarray(self.covariance, dtype=float)
                n = len(self.dimensions)
                if n < 2 or len(self.coordinates) != n or values.shape != (n, n):
                    raise ValueError("Ellipsoid coordinates/covariance must match its dimensions")
                # GatingML conformance includes general, non-symmetric quadratic
                # forms. Preserve their exact matrices rather than repairing them.
                if not np.all(np.isfinite(values)) or np.linalg.cond(values) > 1e12:
                    raise ValueError("Ellipsoid covariance must be finite, invertible and stable")
            if self.kind == "polygon":
                if len(self.dimensions) != 2 or len(set(self.vertices)) < 3:
                    raise ValueError("Polygon needs two dimensions and three distinct vertices")
            return self
        if self.kind in {"hyperrectangle", "ellipsoid"}:
            raise ValueError("Select explicit gate dimensions")
        if not self.x or (self.kind != "range" and not self.y):
            raise ValueError("Select the gate's channel dimensions")
        if self.kind in {"range", "rectangle"}:
            size = 2 if self.kind == "range" else 4
            if len(self.bounds) != size or self.bounds[0] >= self.bounds[1]:
                raise ValueError("Gate bounds must have increasing minimum and maximum")
            if size == 4 and self.bounds[2] >= self.bounds[3]:
                raise ValueError("Y bounds must have increasing minimum and maximum")
        if self.kind == "polygon":
            if len(set(self.vertices)) < 3:
                raise ValueError("A polygon requires at least three distinct vertices")
            # A self-crossing outline may have zero signed shoelace area while
            # enclosing real populations under the engine's even-odd rule.
            # Reject collinear outlines, using independently scaled axes to
            # avoid rejecting tiny valid shapes or overflowing large coordinates.
            sx = max(abs(point[0]) for point in self.vertices) or 1
            sy = max(abs(point[1]) for point in self.vertices) or 1
            points = [(a / sx, b / sy) for a, b in self.vertices]
            a, b = points[0]
            x, y = max(points, key=lambda point: math.hypot(point[0] - a, point[1] - b))
            length = math.hypot(x - a, y - b)
            if not any(
                abs((x - a) * (v - b) - (y - b) * (u - a))
                > 1e-14 * length * math.hypot(u - a, v - b)
                for u, v in points
            ):
                raise ValueError("Polygon area must be nonzero")
        if self.kind == "ellipse":
            if not self.center or not self.radii or min(self.radii) <= 0:
                raise ValueError("Ellipse requires a center and positive radii")
        if self.kind == "quadrant" and len(self.bounds) != 2:
            raise ValueError("Quadrant gates require X and Y thresholds")
        if any(not math.isfinite(v) for v in self.bounds):
            raise ValueError("Gate coordinates must be finite")
        return self

    @model_validator(mode="after")
    def valid_magnetic_shape(self):
        if self.magnetic is not None:
            from .magnetic import frame

            frame(self)
        return self


class Compensation(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    detectors: list[Name] = Field(min_length=1, max_length=512)
    outputs: list[Name] = Field(default_factory=list, max_length=512)
    matrix: list[list[float]]
    kind: Literal["spillover", "spectral"] = "spillover"
    source: str = "Manual"
    background: list[float] = Field(default_factory=list, max_length=512)
    weights: list[float] = Field(default_factory=list, max_length=512)
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_dimensions(self):
        if len(set(self.detectors)) != len(self.detectors):
            raise ValueError("Detector names must be unique")
        if self.background and len(self.background) != len(self.detectors):
            raise ValueError("Background must contain one value per detector")
        if self.weights and (len(self.weights) != len(self.detectors) or min(self.weights) <= 0):
            raise ValueError("Least-squares weights must be positive and match the detectors")
        if self.kind == "spillover" and (self.background or self.weights):
            raise ValueError("Background and weights apply only to spectral unmixing")
        if self.kind == "spillover":
            if self.outputs and self.outputs != self.detectors:
                raise ValueError("Spillover output names must match detectors")
            self.outputs = self.detectors.copy()
        if not self.outputs or len(set(self.outputs)) != len(self.outputs):
            raise ValueError("Output names must be unique and nonempty")
        if len(self.matrix) != len(self.outputs) or any(
            len(row) != len(self.detectors) for row in self.matrix
        ):
            raise ValueError("Matrix rows are sources/outputs; columns are measured detectors")
        return self


class Group(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    sample_ids: list[Id] = Field(default_factory=list)
    color: str = "#38d9ba"


ReportColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class GraphTextStyle(Model):
    font_size_pt: float | None = Field(default=None, ge=4, le=144, strict=True)
    font_family: Literal["sans", "serif", "mono"] | None = None
    font_weight: Literal["normal", "bold"] | None = None
    font_style: Literal["normal", "italic"] | None = None
    color: ReportColor | None = None

    @model_serializer(mode="wrap")
    def omit_inherited_properties(self, serializer):
        return {key: value for key, value in serializer(self).items() if value is not None}


class GraphTypography(Model):
    axis_labels: GraphTextStyle | None = None
    tick_labels: GraphTextStyle | None = None
    gate_labels: GraphTextStyle | None = None
    statistics: GraphTextStyle | None = None
    legend: GraphTextStyle | None = None
    title: GraphTextStyle | None = None

    @model_serializer(mode="wrap")
    def omit_inherited_roles(self, serializer):
        return {key: value for key, value in serializer(self).items() if value}


class GraphGateStyle(Model):
    fill_opacity: float | None = Field(default=None, ge=0, le=1, strict=True)
    fill_color: ReportColor | None = None
    line_width_px: float | None = Field(default=None, ge=0.25, le=12, strict=True)
    show_labels: bool | None = Field(default=None, strict=True)

    @model_serializer(mode="wrap")
    def omit_inherited_properties(self, serializer):
        return {key: value for key, value in serializer(self).items() if value is not None}


class GraphOptions(Model):
    smooth: bool | None = None
    sigma: float = Field(default=1, ge=0, le=4)
    contour_spacing: Literal["2", "5", "10", "log"] | None = None
    show_outliers: bool = True
    palette: Literal["ocean", "gray", "spectrum", "viridis"] = "ocean"
    axis_extent: Literal["robust", "full"] = "robust"
    point_limit: int = Field(default=12000, ge=100, le=100000)
    typography: GraphTypography | None = None
    gate_style: GraphGateStyle | None = None

    @model_serializer(mode="wrap")
    def omit_default_presentation(self, serializer):
        value = serializer(self)
        for key in ("typography", "gate_style"):
            if not value.get(key):
                value.pop(key, None)
        return value


class ThreeDView(Model):
    z: Name
    z_dimension: PlotDimension | None = None
    color_dimension: PlotDimension | None = None
    size_dimension: PlotDimension | None = None
    z_transform: Transform | None = None
    color_by: Name | None = None
    size_by: Name | None = None
    color_transform: Transform | None = None
    size_transform: Transform | None = None
    color_bounds: tuple[float, float] | None = None
    size_bounds: tuple[float, float] | None = None
    compensation: Literal["coordinate", "uncompensated"] = "coordinate"
    all_events: bool = True
    yaw: float = Field(default=-0.65, ge=-math.pi, le=math.pi)
    pitch: float = Field(default=0.45, ge=-1.5, le=1.5)
    zoom: float = Field(default=1, ge=0.1, le=10)
    pan: tuple[float, float] = (0, 0)
    point_size: float = Field(default=2, ge=0.5, le=12)
    opacity: float = Field(default=0.7, ge=0.05, le=1)
    show_cube: bool = True
    show_labels: bool = True

    @model_serializer(mode="wrap")
    def omit_default_dimensions(self, serializer):
        value = serializer(self)
        for field in ("z_dimension", "color_dimension", "size_dimension"):
            if getattr(self, field) is None:
                value.pop(field, None)
        return value

    @model_validator(mode="after")
    def valid_view(self):
        for name, dimension in (
            (self.z, self.z_dimension),
            (self.color_by, self.color_dimension),
            (self.size_by, self.size_dimension),
        ):
            if dimension and dimension.channel != name:
                raise ValueError("3D coordinate definitions must match their selected parameters")
        if any(abs(value) > 5 for value in self.pan):
            raise ValueError("3D pan must remain within the supported camera range")
        for limits in (self.color_bounds, self.size_bounds):
            if limits and limits[0] >= limits[1]:
                raise ValueError("3D color and size bounds must be finite and increasing")
        return self


class PlotLayer(Model):
    id: Id = Field(default_factory=new_id)
    sample_id: Id
    gate_id: Id | None = None
    coordinate_gate_id: Id | None = None
    backgate_id: Id | None = None
    label: str = Field(default="", max_length=500)
    color: ReportColor = "#b34772"
    locked_control: bool = False

    @model_serializer(mode="wrap")
    def omit_default_backgate(self, serializer):
        value = serializer(self)
        if self.backgate_id is None:
            value.pop("backgate_id", None)
        return value


class PlotDefinition(Model):
    id: Id = Field(default_factory=new_id)
    sample_id: Id
    gate_id: Id | None = None
    pooled: bool = False
    group_id: Id | None = None
    sample_filter: str = Field(default="", max_length=256)
    coordinate_gate_id: Id | None = None
    backgate_id: Id | None = None
    x: Name
    y: Name | None = None
    x_dimension: PlotDimension | None = None
    y_dimension: PlotDimension | None = None
    mode: Literal[
        "density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor", "3d"
    ] = "density"
    graph_options: GraphOptions = Field(default_factory=GraphOptions)
    three_d: ThreeDView | None = None
    title: str = Field(default="", max_length=1000)
    overlays: list[PlotLayer] = Field(default_factory=list, max_length=15)
    locked_control: bool = False
    color: ReportColor = "#087e8b"
    x_transform: Transform | None = None
    y_transform: Transform | None = None
    bounds: list[float] | None = None
    bins: int = Field(default=96, ge=16, le=384)
    normalization: Literal["count", "percent_population", "unit_area", "peak"] = "count"
    show_gates: bool = True

    @model_serializer(mode="wrap")
    def omit_default_dimensions(self, serializer):
        value = serializer(self)
        if not self.pooled:
            for field in ("pooled", "group_id", "sample_filter"):
                value.pop(field, None)
        for field in ("x_dimension", "y_dimension", "backgate_id"):
            if getattr(self, field) is None:
                value.pop(field, None)
        return value

    @model_validator(mode="after")
    def report_plot(self):
        for name, dimension in ((self.x, self.x_dimension), (self.y, self.y_dimension)):
            if dimension and dimension.channel != name:
                raise ValueError("Plot coordinate definitions must match their selected parameters")
        if self.y is None and self.mode != "cdf":
            if self.mode == "3d":
                raise ValueError("A 3D plot requires three parameters")
            self.mode = "histogram"
        if self.mode == "3d" and self.three_d is None:
            raise ValueError("Select the 3D Z parameter and camera view")
        if self.mode in {"histogram", "cdf"} and self.y is not None:
            raise ValueError("Histograms and CDFs have one parameter")
        if self.mode != "histogram" and self.normalization != "count":
            raise ValueError("Display normalization applies to histogram reports")
        if self.bounds is not None:
            expected = 6 if self.mode == "3d" else 2 if self.mode in {"histogram", "cdf"} else 4
            if len(self.bounds) != expected or any(
                self.bounds[i] >= self.bounds[i + 1] for i in range(0, expected, 2)
            ):
                raise ValueError("Report axis bounds must be finite and increasing")
        if len({layer.id for layer in self.overlays}) != len(self.overlays):
            raise ValueError("Overlay layer IDs must be unique")
        return self


class ReportPage(Model):
    width_mm: float = Field(default=210, ge=30, le=1200)
    height_mm: float = Field(default=297, ge=30, le=1200)
    margin_mm: float = Field(default=12, ge=0, le=100)
    background: ReportColor = "#ffffff"

    @model_validator(mode="after")
    def usable_page(self):
        if self.margin_mm * 2 >= min(self.width_mm, self.height_mm):
            raise ValueError("Page margins must leave a positive content area")
        return self


class ComparisonPresentation(Model):
    mode: Literal["histogram", "cdf", "difference"] = "histogram"
    smoothing: float = Field(default=0, ge=0, le=16)
    control_color: str = Field(default="#38d9ba", pattern=r"^#[a-fA-F0-9]{6}$")
    target_color: str = Field(default="#b595f6", pattern=r"^#[a-fA-F0-9]{6}$")
    difference_scale: float = Field(default=1, ge=0.1, le=10)
    show_individuals: bool = False


class ReportTableCellStyle(Model):
    row: int | None = Field(default=None, ge=0, le=49999, strict=True)
    column: int = Field(ge=0, le=515, strict=True)
    align: Literal["left", "center", "right"] | None = None
    vertical_align: Literal["top", "middle", "bottom"] | None = None
    background: ReportColor | None = None
    color: ReportColor | None = None
    font_size_pt: float | None = Field(default=None, ge=4, le=144)
    font_family: Literal["sans-serif", "serif", "monospace"] | None = None
    font_weight: Literal[400, 600, 700, 800] | None = None
    font_style: Literal["normal", "italic"] | None = None
    text_decoration: Literal["none", "underline"] | None = None
    line_spacing: float | None = Field(default=None, ge=1, le=3)

    @model_serializer(mode="wrap")
    def compact_style(self, serializer):
        return {key: value for key, value in serializer(self).items() if value is not None}


class ReportTableGeometry(Model):
    column_width_mm: float | None = Field(default=None, gt=0, le=1200)
    row_height_mm: float | None = Field(default=None, gt=0, le=1200)
    header_height_mm: float | None = Field(default=None, gt=0, le=1200)
    column_widths_mm: dict[
        Annotated[int, Field(ge=0, le=515)], Annotated[float, Field(gt=0, le=1200)]
    ] = Field(default_factory=dict, max_length=516)
    row_heights_mm: dict[
        Annotated[int, Field(ge=0, le=49999)], Annotated[float, Field(gt=0, le=1200)]
    ] = Field(default_factory=dict, max_length=50000)
    padding_x_mm: float = Field(default=0.7, ge=0, le=20)
    padding_y_mm: float = Field(default=0, ge=0, le=20)
    align: Literal["left", "center", "right"] = "left"
    vertical_align: Literal["top", "middle", "bottom"] = "top"
    cell_styles: list[ReportTableCellStyle] = Field(default_factory=list, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def canonical_geometry_indices(cls, value):
        if isinstance(value, dict):
            for field in ("column_widths_mm", "row_heights_mm"):
                entries = value.get(field, {})
                if not isinstance(entries, dict):
                    continue
                for key in entries:
                    if type(key) is int:
                        continue
                    if not isinstance(key, str) or not key.isascii() or not key.isdecimal():
                        raise ValueError("Table geometry positions must be integer indices")
                    if str(int(key)) != key:
                        raise ValueError(
                            "Table geometry positions must use canonical integer indices"
                        )
        return value

    @model_validator(mode="after")
    def unique_cells(self):
        keys = [(style.row, style.column) for style in self.cell_styles]
        if len(set(keys)) != len(keys):
            raise ValueError("Style each report table cell only once")
        return self


class ReportElement(Model):
    id: Id = Field(default_factory=new_id)
    kind: Literal["plot", "table", "biology", "population_comparison", "plate", "text", "shape"]
    page: int = Field(default=0, ge=0, le=31)
    x_mm: float = Field(default=12, ge=0, le=1200)
    y_mm: float = Field(default=35, ge=0, le=1200)
    width_mm: float = Field(default=90, gt=0, le=1200)
    height_mm: float = Field(default=85, gt=0, le=1200)
    rotation: float = Field(default=0, ge=-360, le=360)
    group_id: Id | None = None
    position_locked: bool = False
    iterate: bool = True
    title: str = Field(default="", max_length=1000)
    plot: PlotDefinition | None = None
    table_id: Id | None = None
    table_view: Literal["data", "pivot", "comparisons"] = "data"
    auto_paginate: bool = False
    row_start: int = Field(default=0, ge=0)
    row_count: int = Field(default=12, ge=1, le=200)
    column_ids: list[Id] = Field(default_factory=list, max_length=64)
    column_start: int = Field(default=0, ge=0, le=511)
    columns_per_page: int = Field(default=4, ge=1, le=16)
    pivot_counts: bool = True
    comparison_fields: list[
        Literal[
            "n_a",
            "n_b",
            "pairs",
            "excluded_a",
            "excluded_b",
            "mean_a",
            "mean_b",
            "mean_difference",
            "median_difference",
            "statistic",
            "p_value",
            "adjusted_p_value",
            "confidence_interval",
        ]
    ] = Field(
        default_factory=lambda: [
            "n_a",
            "n_b",
            "mean_a",
            "mean_b",
            "mean_difference",
            "p_value",
            "adjusted_p_value",
            "confidence_interval",
        ],
        min_length=1,
        max_length=13,
    )
    table_geometry: ReportTableGeometry | None = None
    platform: Literal["cell-cycle", "proliferation", "kinetics"] | None = None
    result_id: Id | None = None
    sample_id: Id | None = None
    gate_id: Id | None = None
    compensated: bool = True
    follow_replacement: bool = True
    comparison_parameter_id: Id | None = None
    comparison_view: ComparisonPresentation | None = None
    plate_id: Id | None = None
    text: str = Field(default="", max_length=20000)
    font_size_pt: float = Field(default=10, ge=4, le=144)
    font_family: Literal["sans-serif", "serif", "monospace"] = "sans-serif"
    font_weight: Literal[400, 600, 700, 800] = 400
    font_style: Literal["normal", "italic"] = "normal"
    text_decoration: Literal["none", "underline"] = "none"
    line_spacing: float = Field(default=1.35, ge=1, le=3)
    align: Literal["left", "center", "right"] = "left"
    color: ReportColor = "#233449"
    fill: ReportColor = "#ffffff"
    stroke: ReportColor = "#087e8b"
    stroke_width_mm: float = Field(default=0.4, ge=0, le=10)
    opacity: float = Field(default=1, ge=0, le=1)
    shape: Literal["rectangle", "ellipse", "line", "arrow"] = "rectangle"

    @model_serializer(mode="wrap")
    def compatible_report_definition(self, handler):
        values = handler(self)
        for key in ("comparison_parameter_id", "comparison_view", "table_geometry"):
            if values.get(key) is None:
                values.pop(key, None)
        for key, default in (
            ("font_style", "normal"),
            ("text_decoration", "none"),
            ("line_spacing", 1.35),
        ):
            if values.get(key) == default:
                values.pop(key, None)
        return values

    @model_validator(mode="after")
    def content(self):
        if self.table_geometry is not None and self.kind != "table":
            raise ValueError("Table geometry requires a report table")
        if self.kind == "plot" and self.plot is None:
            raise ValueError("A report plot requires its source definition")
        if self.kind == "table" and self.table_id is None:
            raise ValueError("A report table requires a saved table")
        if self.kind == "biology" and not all((self.platform, self.result_id, self.sample_id)):
            raise ValueError("A biological figure requires a platform, result and sample")
        if self.kind == "population_comparison" and not all(
            (self.result_id, self.sample_id, self.comparison_parameter_id)
        ):
            raise ValueError(
                "A comparison figure requires a saved result, population and parameter"
            )
        if self.kind == "plate" and self.plate_id is None:
            raise ValueError("A plate figure requires a saved plate")
        if len(set(self.column_ids)) != len(self.column_ids):
            raise ValueError("Report table column IDs must be unique")
        if len(set(self.comparison_fields)) != len(self.comparison_fields):
            raise ValueError("Report comparison fields must be unique")
        return self


class ReportBatch(Model):
    mode: Literal["off", "sample", "keyword", "panel"] = "off"
    group_id: Id | None = None
    sample_ids: list[Id] = Field(default_factory=list, max_length=1024)
    iterator_keyword: str = Field(default="", max_length=500)
    discriminator_keyword: str = Field(default="", max_length=500)
    panel_size: int = Field(default=2, ge=1, le=32)
    tile_columns: int = Field(default=1, ge=1, le=8)
    tile_rows: int = Field(default=1, ge=1, le=8)
    order: Literal["across", "down"] = "across"
    overrides: dict[str, dict[Id, Id]] = Field(default_factory=dict, max_length=1024)
    population_overrides: dict[str, dict[Id, Id | None]] = Field(
        default_factory=dict, max_length=1024
    )

    @model_validator(mode="after")
    def batch_settings(self):
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("Report batch samples must be unique")
        if self.mode == "keyword" and not self.iterator_keyword:
            raise ValueError("Keyword batching requires an iterator keyword")
        if any(
            len(key) > 1000 or len(value) > 128
            for mapping in (self.overrides, self.population_overrides)
            for key, value in mapping.items()
        ):
            raise ValueError("Report mapping overrides exceed their supported bounds")
        return self


class ReportTemplateOrigin(Model):
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_workspace_id: Id
    source_layout_id: Id
    bindings: dict[
        Annotated[str, Field(min_length=1, max_length=400)],
        Annotated[str, Field(min_length=1, max_length=160)] | None,
    ] = Field(default_factory=dict, max_length=16384)
    batch_scope: Literal["destination", "template"]


class LayoutDefinition(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    description: str = Field(default="", max_length=20000)
    plots: list[PlotDefinition] = Field(default_factory=list, max_length=128)
    pages: list[ReportPage] = Field(
        default_factory=lambda: [ReportPage()], min_length=1, max_length=32
    )
    elements: list[ReportElement] = Field(default_factory=list, max_length=256)
    batch: ReportBatch = Field(default_factory=ReportBatch)
    export_policy: Literal["current", "snapshots", "placeholders"] = "current"
    show_header: bool = True
    show_footer: bool = True
    template_origin: ReportTemplateOrigin | None = None

    @model_serializer(mode="wrap")
    def omit_inactive_template_origin(self, serializer):
        value = serializer(self)
        if self.template_origin is None:
            value.pop("template_origin", None)
        return value

    @model_validator(mode="after")
    def geometry(self):
        if self.elements and self.plots:
            raise ValueError("Migrate legacy grid plots before adding positioned report elements")
        if len({element.id for element in self.elements}) != len(self.elements):
            raise ValueError("Report element IDs must be unique")
        for element in self.elements:
            if element.page >= len(self.pages):
                raise ValueError("Report element references a missing page")
            page = self.pages[element.page]
            if element.x_mm + element.width_mm > page.width_mm + 1e-7 or (
                element.y_mm + element.height_mm > page.height_mm + 1e-7
            ):
                raise ValueError("Report element must fit its page")
        return self


class TableColumn(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    kind: Literal["statistic", "metadata", "formula", "biology", "population_comparison"] = (
        "statistic"
    )
    statistic: Literal[
        "count",
        "percent_parent",
        "percent_total",
        "finite_count",
        "nonfinite_count",
        "mean",
        "median",
        "std",
        "variance",
        "cv",
        "robust_cv",
        "mad",
        "geometric_mean",
        "geometric_std",
        "min",
        "max",
        "percentile",
    ] = "count"
    channel: str = Field(default="", max_length=160)
    percentile: float = Field(default=50.0, ge=0, le=100)
    channel_overrides: dict[Id, Name] = Field(default_factory=dict, max_length=1024)
    population_path: list[Name] | None = Field(default=None, max_length=128)
    population_overrides: dict[Id, Id | None] = Field(default_factory=dict, max_length=1024)
    population_unavailable: list[Id] = Field(default_factory=list, max_length=1024)
    compensation_overrides: dict[Id, Id | Literal["sample", "uncompensated", "FCS"]] = Field(
        default_factory=dict, max_length=1024
    )
    control_sample_id: Id | None = None
    control_unavailable: bool = False
    metadata_source: Literal["tags", "metadata", "keywords"] = "tags"
    metadata_key: str = Field(default="", max_length=256)
    metadata_numeric: bool = False
    expression: str = Field(default="", max_length=2048)
    formula_refs: dict[str, Id] = Field(default_factory=dict, max_length=128)
    platform: Literal["cell-cycle", "proliferation", "kinetics"] = "proliferation"
    result_id: Id | None = None
    kinetics_range_id: Id | None = None
    comparison_parameter_id: Id | None = None
    biology_metric: str = Field(default="precursor_frequency", max_length=80)
    generation: int = Field(default=0, ge=0, le=12)
    allow_stale: bool = False
    follow_replacement: bool = True
    hidden: bool = False
    decimals: int = Field(default=2, ge=0, le=12)
    heatmap: bool = False

    @model_serializer(mode="wrap")
    def omit_inactive_comparison_parameter(self, serializer):
        value = serializer(self)
        if self.comparison_parameter_id is None:
            value.pop("comparison_parameter_id", None)
        return value

    @model_validator(mode="after")
    def valid_column(self):
        if self.kind == "population_comparison":
            if not self.result_id:
                raise ValueError("Select a saved population comparison")
            if self.biology_metric not in {
                "ks_distance",
                "ks_p_value",
                "ks_at_coordinate",
                "overton_cumulative_percent",
                "enhanced_dmax_percent",
                "ens_percent",
                "peak_normalized_excess_percent",
                "chi_squared",
                "tx",
                "finite_count",
                "selected_count",
                "control_finite_count",
                "control_selected_count",
                "shared_events",
            }:
                raise ValueError("Unknown population comparison statistic")
        if (
            self.kind == "statistic"
            and self.statistic not in {"count", "percent_parent", "percent_total"}
            and not self.channel
        ):
            raise ValueError("A channel statistic requires a parameter")
        if self.kind == "metadata" and not self.metadata_key:
            raise ValueError("A metadata column requires a keyword")
        if self.kind == "formula":
            parse_table_formula(self.expression)
        if self.kind == "biology":
            if not self.result_id:
                raise ValueError("Select a saved biological model")
            metrics = {
                "fraction",
                "model_count",
                "expected_count",
                "assigned_count",
                "fitted_count",
                "normalized_rmsd",
                "converged",
                "undivided_mean",
                "peak_ratio",
                "dye_cv",
                "background",
                "background_sd",
                "precursor_frequency",
                "percent_divided",
                "division_index",
                "proliferation_index",
                "expansion_index",
                "replication_index",
                "observed_divided_fraction",
                "precursor_equivalents",
                "responding_precursor_equivalents",
                "division_equivalents",
                "g1_mean",
                "g2_mean",
                "g1_cv",
                "g2_cv",
            }
            if self.platform == "kinetics":
                metrics = {
                    "peak",
                    "peak_time",
                    "mean",
                    "slope",
                    "auc",
                    "duration",
                    "covered_duration",
                    "population_count",
                    "finite_count",
                    "selected_count",
                    "responder_count",
                    "responder_percent",
                    "curve_points",
                    "threshold",
                    "baseline_count",
                    "represented_count",
                    "time_count",
                }
                if (
                    self.biology_metric
                    not in {"threshold", "baseline_count", "represented_count", "time_count"}
                    and not self.kinetics_range_id
                ):
                    raise ValueError("Select a saved kinetics time range")
            if self.biology_metric not in metrics:
                raise ValueError("Unknown biological model statistic")
        return self


class TablePivot(Model):
    rows: list[Id] = Field(default_factory=list, max_length=4)
    columns: list[Id] = Field(default_factory=list, max_length=3)
    measures: list[Id] = Field(min_length=1, max_length=32)
    aggregation: Literal["mean", "median", "sum", "count", "std", "min", "max"] = "mean"


class TableComparison(Model):
    group_column: Id
    group_a: str = Field(min_length=1, max_length=256)
    group_b: str = Field(min_length=1, max_length=256)
    measures: list[Id] = Field(min_length=1, max_length=64)
    method: Literal["welch", "mann_whitney", "paired_t", "wilcoxon"] = "welch"
    pair_column: Id | None = None
    adjustment: Literal["holm", "benjamini_hochberg", "none"] = "holm"
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)

    @model_validator(mode="after")
    def valid_comparison(self):
        if self.group_a == self.group_b:
            raise ValueError("Compare two distinct group values")
        if self.method in {"paired_t", "wilcoxon"} and not self.pair_column:
            raise ValueError("A paired test requires a pairing column")
        return self


class TableDefinition(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    channel: str = Field(default="", max_length=160)
    filter: str = Field(default="", max_length=256)
    columns: list[TableColumn] = Field(default_factory=list, max_length=128)
    row_mode: Literal["samples", "populations"] = "populations"
    group_id: Id | None = None
    sample_ids: list[Id] = Field(default_factory=list, max_length=4096)
    sample_order: Literal["workspace", "selection"] = "workspace"
    compensated: bool = True
    sort_by: str = "sample"
    descending: bool = False
    pivot: TablePivot | None = None
    comparison: TableComparison | None = None
    provenance: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def valid_columns(self):
        columns = {c.id: c for c in self.columns}
        names = {c.name: c.id for c in self.columns}
        if len(columns) != len(self.columns) or len(
            {c.name.casefold() for c in self.columns}
        ) != len(self.columns):
            raise ValueError("Table column identifiers and names must be unique")
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("Select each table sample only once")
        if self.sort_by not in {"input", "sample", "population", *columns}:
            raise ValueError("The sort column must exist in the table")
        for column in self.columns:
            if column.name in columns and column.name != column.id:
                raise ValueError("A column name cannot match another column identifier")
            if column.kind != "formula":
                continue
            _, references = parse_table_formula(column.expression)
            bindings = {}
            for alias in references:
                identifier = column.formula_refs.get(alias) or (
                    alias if alias in columns else names.get(alias)
                )
                if identifier not in columns:
                    raise ValueError(f"Formula references missing column {alias}")
                bindings[alias] = identifier
            column.formula_refs = bindings
        visited, visiting = set(), set()

        def visit(identifier):
            if identifier in visiting:
                raise ValueError("Table formula dependencies contain a cycle")
            if identifier in visited:
                return
            visiting.add(identifier)
            for target in columns[identifier].formula_refs.values():
                visit(target)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in columns:
            visit(identifier)
        refs = []
        if self.pivot:
            refs.extend([*self.pivot.rows, *self.pivot.columns, *self.pivot.measures])
            for values in (self.pivot.rows, self.pivot.columns, self.pivot.measures):
                if len(set(values)) != len(values):
                    raise ValueError("Pivot columns cannot be repeated")
            if set(self.pivot.rows) & set(self.pivot.columns):
                raise ValueError("A pivot dimension cannot be both a row and a column")
        if self.comparison:
            if self.row_mode != "samples":
                raise ValueError("Statistical comparisons require one row per sample")
            refs.extend([self.comparison.group_column, *self.comparison.measures])
            if self.comparison.pair_column:
                refs.append(self.comparison.pair_column)
            if len(set(self.comparison.measures)) != len(self.comparison.measures):
                raise ValueError("Comparison measures cannot be repeated")
        if not set(refs) <= columns.keys():
            raise ValueError("Pivot and comparison columns must exist in the table")
        return self


PLATE_SHAPES = {
    6: (2, 3),
    12: (3, 4),
    24: (4, 6),
    48: (6, 8),
    96: (8, 12),
    384: (16, 24),
    1536: (32, 48),
}
PlateFormat = Literal[6, 12, 24, 48, 96, 384, 1536, "custom"]


class PlateGeometry(Model):
    rows: int = Field(ge=1, le=96, strict=True)
    columns: int = Field(ge=1, le=96, strict=True)

    @model_validator(mode="after")
    def bounded_wells(self):
        if self.rows * self.columns > 1536:
            raise ValueError("A plate supports at most 1536 well positions")
        return self


def plate_dimensions(format: PlateFormat, geometry: PlateGeometry | None = None):
    if format == "custom":
        if geometry is None:
            raise ValueError("Custom plates require explicit row and column dimensions")
        return geometry.rows, geometry.columns
    if geometry is not None:
        raise ValueError("Explicit row and column dimensions require the custom plate format")
    return PLATE_SHAPES[format]


def plate_position(value: str) -> tuple[int, int]:
    """Parse an alphanumeric well, including zero padding and rows after Z."""
    import re

    match = re.fullmatch(r"([A-Za-z]{1,2})[\s_:-]*0*([1-9][0-9]{0,2})", value.strip())
    if match is None:
        raise ValueError(f"Invalid well ID {value!r}; use an alphanumeric well such as A01")
    row = 0
    for letter in match[1].upper():
        row = row * 26 + ord(letter) - ord("A") + 1
    return row - 1, int(match[2]) - 1


def plate_well(row: int, column: int) -> str:
    letters = ""
    index = row + 1
    while index:
        index, digit = divmod(index - 1, 26)
        letters = chr(ord("A") + digit) + letters
    return f"{letters}{column + 1:02d}"


class PlateView(Model):
    mode: Literal["heatmap", "split", "categories", "faces"] = "heatmap"
    primary: Id | None = None
    secondary: Id | None = None
    category_keyword: str = Field(default="", max_length=160)
    category_source: Literal["tags", "metadata"] = "tags"
    category_colors: dict[str, str] = Field(default_factory=dict, max_length=4096)
    domains: dict[Id, tuple[float, float]] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def valid_domains(self):
        if any(
            not math.isfinite(a) or not math.isfinite(b) or a >= b for a, b in self.domains.values()
        ):
            raise ValueError("Plate display bounds must be finite and increasing")
        import re

        if any(
            len(label) > 2048 or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None
            for label, color in self.category_colors.items()
        ):
            raise ValueError("Category colors require text labels and six-digit hex colors")
        return self


class PlateDefinition(Model):
    id: Id = Field(default_factory=new_id)
    name: Name = "Plate"
    plate_key: str = Field(default="", max_length=512)
    format: PlateFormat = 96
    geometry: PlateGeometry | None = None
    assignments: dict[str, list[Id]] = Field(default_factory=dict, max_length=1536)
    annotations: dict[str, dict[str, str | None]] = Field(default_factory=dict, max_length=1536)
    columns: list[TableColumn] = Field(
        default_factory=lambda: [TableColumn(name="Events", statistic="count", decimals=0)],
        min_length=1,
        max_length=10,
    )
    compensated: bool = True
    aggregate: Literal["median", "mean", "sum"] = "median"
    missing_replicates: Literal["strict", "available"] = "strict"
    view: PlateView = Field(default_factory=PlateView)
    provenance: dict = Field(default_factory=dict)

    @property
    def dimensions(self):
        return plate_dimensions(self.format, self.geometry)

    @model_serializer(mode="wrap")
    def serialize_geometry(self, serializer):
        value = serializer(self)
        if self.geometry is None:
            value.pop("geometry", None)
        return value

    @model_validator(mode="after")
    def valid_plate(self):
        rows, columns = self.dimensions

        def normalize(items):
            result = {}
            for key, value in items.items():
                row, col = plate_position(key)
                if row >= rows or col >= columns:
                    raise ValueError(f"Well {key} is outside this {rows} × {columns} plate")
                canonical = plate_well(row, col)
                if canonical in result:
                    raise ValueError(f"Duplicate normalized well ID {canonical}")
                result[canonical] = value
            return result

        self.assignments = normalize(self.assignments)
        self.annotations = normalize(self.annotations)
        members = [s for ids in self.assignments.values() for s in ids]
        if len(members) != len(set(members)):
            raise ValueError("Each acquisition may occupy only one well in a plate")
        if len(members) > 4096 or any(len(ids) > 64 for ids in self.assignments.values()):
            raise ValueError("A plate supports 4096 acquisitions and 64 replicates per well")
        size = 0
        for values in self.annotations.values():
            if len(values) > 64:
                raise ValueError("A well supports at most 64 staged annotation keys")
            for key, value in values.items():
                if not key.strip() or len(key) > 160 or any(ord(c) < 32 for c in key):
                    raise ValueError(
                        "Annotation keys must be nonempty text of at most 160 characters"
                    )
                if value is not None and (len(value) > 2048 or "\x00" in value):
                    raise ValueError("Annotation values support 2048 characters without NUL")
                size += len(key.encode()) + len((value or "").encode())
        if size > 4 * 1024 * 1024:
            raise ValueError("Staged plate annotations exceed four MiB")
        # Reuse formula binding/cycle and population/statistic validation.
        definition = TableDefinition(name=self.name, columns=self.columns, row_mode="samples")
        self.columns = definition.columns
        identifiers = {c.id for c in self.columns}
        if not set(self.view.domains) <= identifiers or any(
            i is not None and i not in identifiers for i in [self.view.primary, self.view.secondary]
        ):
            raise ValueError("Plate visualization references an unavailable measurement")
        return self


class InterchangeRecord(Model):
    id: Id = Field(default_factory=new_id)
    format: Literal["gatingml", "flowjo"]
    name: Name
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str
    mappings: dict[str, list[Id]]
    gate_ids: list[Id] = Field(default_factory=list)
    table_ids: list[Id] = Field(default_factory=list, max_length=1000)
    report: dict = Field(default_factory=dict)


class ComparisonParameter(PlotDimension):
    id: Id = Field(default_factory=new_id)
    label: str = Field(default="", max_length=256)


class PopulationComparisonRequest(Model):
    revision: int = Field(ge=0)
    name: Name
    algorithm: Literal["population_comparison"] = "population_comparison"
    inputs: list[AnalysisInput] = Field(min_length=1, max_length=128)
    controls: list[AnalysisInput] = Field(min_length=1, max_length=128)
    parameters: list[ComparisonParameter] = Field(min_length=1, max_length=64)
    probability_bins: int = Field(default=64, ge=2, le=4096)
    histogram_bins: int = Field(default=256, ge=16, le=1024)
    minimum_bin_events: int = Field(default=10, ge=1, le=10000)
    positive_direction: Literal["higher", "lower"] = "higher"
    joint: bool = True
    control_baselines: bool = True
    replace_result_id: Id | None = None

    @model_validator(mode="after")
    def unique_populations_and_parameters(self):
        for sources in [self.inputs, self.controls]:
            if len({(s.sample_id, s.gate_id) for s in sources}) != len(sources):
                raise ValueError("Comparison populations cannot be repeated within a cohort")
        if len({p.id for p in self.parameters}) != len(self.parameters):
            raise ValueError("Comparison parameter IDs must be unique")
        definitions = [p.model_dump_json(exclude={"id", "label"}) for p in self.parameters]
        if len(set(definitions)) != len(definitions):
            raise ValueError("Comparison coordinate definitions cannot be repeated")
        return self


class PopulationComparisonRow(Model):
    source: AnalysisInput
    source_name: str = Field(max_length=256)
    population_name: str = Field(max_length=500)
    role: Literal["target", "control_baseline"] = "target"
    parameter_id: Id | None = None
    selected_count: int = Field(ge=0)
    finite_count: int = Field(ge=0)
    control_selected_count: int = Field(ge=0)
    control_finite_count: int = Field(ge=0)
    shared_events: int = Field(default=0, ge=0)
    status: Literal["available", "unavailable"]
    error: str | None = Field(default=None, max_length=1200)
    metrics: dict = Field(default_factory=dict, max_length=64)
    probability: dict = Field(default_factory=dict, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def consistent_event_counts(self):
        if self.finite_count > self.selected_count:
            raise ValueError("Finite comparison events must belong to the selected population")
        if self.control_finite_count > self.control_selected_count:
            raise ValueError("Finite control events must belong to the pooled control")
        if self.shared_events > min(self.finite_count, self.control_finite_count):
            raise ValueError("Shared comparison events must belong to both finite populations")
        if (self.status == "unavailable") != bool(self.error):
            raise ValueError("Unavailable comparisons must explain the missing statistic")
        return self


class PopulationComparisonData(Model):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1)
    parameters: int = Field(ge=1, le=64)
    targets: int = Field(ge=1, le=128)
    controls: int = Field(ge=1, le=128)


class PopulationComparisonResult(Model):
    id: Id
    request: PopulationComparisonRequest
    created_at: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_snapshot: dict
    data: PopulationComparisonData
    rows: list[PopulationComparisonRow] = Field(max_length=16384)
    joint_rows: list[PopulationComparisonRow] = Field(max_length=256)
    diagnostics: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=1024)
    versions: dict[str, str]
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def consistent_sources_and_coordinates(self):
        parameters = {p.id for p in self.request.parameters}
        target_keys = {(s.sample_id, s.gate_id) for s in self.request.inputs}
        control_keys = {(s.sample_id, s.gate_id) for s in self.request.controls}
        keys = set()
        for row in [*self.rows, *self.joint_rows]:
            source = (row.source.sample_id, row.source.gate_id)
            allowed = target_keys if row.role == "target" else control_keys
            if source not in allowed or (row.parameter_id and row.parameter_id not in parameters):
                raise ValueError("Comparison rows must match their actual inputs and parameters")
            key = (*source, row.role, row.parameter_id)
            if key in keys:
                raise ValueError("Comparison rows cannot be duplicated")
            keys.add(key)
        expected = {
            (*source, "target", parameter) for source in target_keys for parameter in parameters
        }
        if not expected <= keys:
            raise ValueError("Every target and parameter needs an explicit comparison outcome")
        if self.request.joint and len(parameters) > 1:
            if not {(*source, "target", None) for source in target_keys} <= keys:
                raise ValueError("Every target needs an explicit joint comparison outcome")
        if any(row.parameter_id is None for row in self.rows) or any(
            row.parameter_id is not None for row in self.joint_rows
        ):
            raise ValueError("Joint and univariate comparison rows must be stored separately")
        if (self.data.parameters, self.data.targets, self.data.controls) != (
            len(parameters),
            len(target_keys),
            len(control_keys),
        ):
            raise ValueError("Comparison artifact dimensions must match the original request")
        return self


class Workspace(Model):
    id: Id = Field(default_factory=new_id)
    name: Name
    description: str = ""
    revision: int = 0
    samples: list[Sample] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    compensations: list[Compensation] = Field(default_factory=list)
    layouts: list[LayoutDefinition] = Field(default_factory=list)
    tables: list[TableDefinition] = Field(default_factory=list, max_length=1000)
    plates: list[PlateDefinition] = Field(default_factory=list, max_length=128)
    analyses: list[AnalysisResult] = Field(default_factory=list)
    interchanges: list[InterchangeRecord] = Field(default_factory=list)
    quality_results: list[QualityResult] = Field(default_factory=list, max_length=1000)
    cell_cycle_results: list[CellCycleResult] = Field(default_factory=list, max_length=1000)
    proliferation_results: list[ProliferationResult] = Field(default_factory=list, max_length=1000)
    kinetics_results: list[KineticsResult] = Field(default_factory=list, max_length=1000)
    comparison_results: list[PopulationComparisonResult] = Field(
        default_factory=list, max_length=1000
    )
    created_at: str = ""
    updated_at: str = ""

    @model_serializer(mode="wrap")
    def omit_inactive_comparisons(self, serializer):
        value = serializer(self)
        if not self.comparison_results:
            value.pop("comparison_results", None)
        return value

    @model_validator(mode="after")
    def valid_references(self):
        samples = {s.id: s for s in self.samples}
        gates = {g.id: g for g in self.gates}
        matrices = {c.id: c for c in self.compensations}
        analyses = {
            a.id: a
            for a in [
                *self.analyses,
                *self.cell_cycle_results,
                *self.proliferation_results,
                *self.kinetics_results,
            ]
        }
        if len(analyses) != len(self.analyses) + len(self.cell_cycle_results) + len(
            self.proliferation_results
        ) + len(self.kinetics_results):
            raise ValueError("Scientific result IDs must be unique across platforms")
        comparison_ids = {r.id for r in self.comparison_results}
        if comparison_ids & (analyses.keys() | {r.id for r in self.quality_results}):
            raise ValueError("Comparison result IDs must be unique across scientific platforms")
        qualities = {q.id: q for q in self.quality_results}
        for items in [
            self.samples,
            self.gates,
            self.compensations,
            self.groups,
            self.layouts,
            self.tables,
            self.plates,
            self.analyses,
            self.interchanges,
            self.quality_results,
            self.cell_cycle_results,
            self.proliferation_results,
            self.kinetics_results,
            self.comparison_results,
        ]:
            if len({v.id for v in items}) != len(items):
                raise ValueError("Duplicate object IDs")
        for s in self.samples:
            if s.compensation_id:
                if s.compensation_id not in matrices:
                    raise ValueError("Sample references a missing compensation matrix")
                if not set(matrices[s.compensation_id].detectors) <= {
                    c.name for c in s.acquisition_channels
                }:
                    raise ValueError(f"Compensation channels do not match {s.name}")
                if not set(matrices[s.compensation_id].outputs) <= {c.name for c in s.channels}:
                    raise ValueError("Compensation output parameters must exist in this sample")
                if matrices[s.compensation_id].kind == "spectral" and not set(
                    matrices[s.compensation_id].outputs
                ) <= set(s.unmixed_parameters):
                    raise ValueError("Spectral outputs must be declared as unmixed parameters")
            for p in s.computed_parameters:
                result = analyses.get(p.analysis_id)
                if not result or p.index >= len(result.columns):
                    raise ValueError("Computed parameter references a missing analysis output")
                if p.name != result.columns[p.index] or not any(
                    d.sample_id == s.id and d.event_count == s.event_count for d in result.data
                ):
                    raise ValueError("Computed parameter does not match its analysis data")
        for g in self.groups:
            if not set(g.sample_ids) <= samples.keys():
                raise ValueError("Group references missing samples")
        for plate in self.plates:
            if not {i for ids in plate.assignments.values() for i in ids} <= samples.keys():
                raise ValueError("Plate references missing samples")
        for result in self.quality_results:
            sample = samples.get(result.request.sample_id)
            if sample is None or sample.event_count != result.data.event_count:
                raise ValueError("QC result must match its original sample and event count")
        for result in [
            *self.cell_cycle_results,
            *self.proliferation_results,
            *self.kinetics_results,
        ]:
            for data in result.data:
                sample = samples.get(data.sample_id)
                if sample is not None and sample.event_count != data.event_count:
                    raise ValueError(
                        "Biological result must match its original samples and event counts"
                    )
        membership_descriptors = {}
        for g in self.gates:
            if g.membership is not None:
                from .population_snapshot import validate_binding

                if g.sample_id not in samples:
                    raise ValueError("Captured population references a missing sample")
                validate_binding(samples[g.sample_id], g.membership)
                existing = membership_descriptors.get(g.membership.id)
                if existing is not None and existing != g.membership:
                    raise ValueError("Captured population IDs require identical descriptors")
                membership_descriptors[g.membership.id] = g.membership
            if g.sample_id not in samples:
                raise ValueError("Gate references a missing sample")
            if g.kind == "quality":
                result = qualities.get(g.quality_id)
                if result is None or result.request.sample_id != g.sample_id:
                    raise ValueError("QC gates require a result from the same sample")
                if any(b >= len(result.bins) for b in g.quality_excluded_bins):
                    raise ValueError("QC gate references a missing acquisition bin")
            if g.parent_id and (
                g.parent_id not in gates or gates[g.parent_id].sample_id != g.sample_id
            ):
                raise ValueError("Gate parent must be in the same sample")
            if any(o not in gates or gates[o].sample_id != g.sample_id for o in g.operands):
                raise ValueError("Boolean operands must exist in the same sample")
            channels = {c.name for c in samples[g.sample_id].channels}
            for d in g.dimensions:
                if not set(d.ratio_channels or (d.channel,)) <= channels:
                    raise ValueError(f"Gate {g.name} references missing dimension channels")
                if d.compensation_ref not in {"sample", "uncompensated", "FCS"}:
                    if d.compensation_ref not in matrices:
                        raise ValueError(f"Gate {g.name} references a missing compensation matrix")
                    if not set(matrices[d.compensation_ref].detectors) <= {
                        c.name for c in samples[g.sample_id].acquisition_channels
                    }:
                        raise ValueError(
                            f"Gate {g.name} compensation detectors do not match its sample"
                        )
            if (
                not g.dimensions
                and g.kind not in {"boolean", "container", "quality", "membership"}
                and (g.x not in channels or (g.y and g.y not in channels))
            ):
                raise ValueError(f"Gate {g.name} references a missing channel")
        partitions: dict[str, list[Gate]] = {}
        for gate in self.gates:
            if gate.partition:
                partitions.setdefault(gate.partition.id, []).append(gate)
        for members in partitions.values():
            first = members[0]
            expected = {1, 2} if first.partition.kind == "bisector" else {1, 2, 3, 4}
            if len(members) != len(expected) or {g.partition.member for g in members} != expected:
                raise ValueError("Linked partitions require every distinct family member")

            def signature(gate):
                return (
                    gate.partition.kind,
                    gate.sample_id,
                    gate.parent_id,
                    gate.spider,
                    gate.curly,
                    [
                        (
                            d.model_dump(
                                exclude={"minimum", "maximum"}
                                | ({"channel"} if d.ratio_channels else set())
                            ),
                            d.minimum if gate.partition.high(i) else d.maximum,
                        )
                        for i, d in enumerate(gate.dimensions)
                    ],
                )

            if any(signature(gate) != signature(first) for gate in members[1:]):
                raise ValueError("Linked partition members must share their parent and coordinates")
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(gate_id: str):
            if len(visiting) > 256:
                raise ValueError("Gate dependency depth exceeds the supported 256 levels")
            if gate_id in visiting:
                raise ValueError("Gate dependencies contain a cycle")
            if gate_id in visited:
                return
            visiting.add(gate_id)
            g = gates[gate_id]
            for dep in ([g.parent_id] if g.parent_id else []) + g.operands:
                visit(dep)
            visiting.remove(gate_id)
            visited.add(gate_id)

        for gate_id in gates:
            visit(gate_id)
        return self
