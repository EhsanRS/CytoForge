"""Scientific dependency closure and freshness of live report measurements."""

import hashlib
from functools import cache

from . import analysis, cellcycle, kinetics, proliferation, quality
from .formulas import parse


class SourceAudit:
    def __init__(self, workspace):
        from . import population_comparison

        self.workspace = workspace
        self.samples = {sample.id: sample for sample in workspace.samples}
        self.gates = {gate.id: gate for gate in workspace.gates}
        self.models = {}
        for field, module in (
            ("analyses", analysis),
            ("cell_cycle_results", cellcycle),
            ("proliferation_results", proliferation),
            ("kinetics_results", kinetics),
            ("quality_results", quality),
            ("comparison_results", population_comparison),
        ):
            self.models.update(
                (result.id, (result, module)) for result in getattr(workspace, field)
            )
        self.stale = cache(self.stale)
        self.parameter_stale = cache(self.parameter_stale)
        self.population_stale = cache(self.population_stale)
        self.validated_files = set()

    def validate_file(self, path, expected):
        key = (str(path), expected)
        if key in self.validated_files:
            return
        try:
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as error:
            raise ValueError(
                "Scientific source data is missing; restore its project archive"
            ) from error
        if actual != expected:
            raise ValueError("Scientific source data failed its SHA-256 integrity check")
        self.validated_files.add(key)

    def validate_model(self, engine, identifier):
        item = self.models.get(identifier)
        if item is None:
            raise ValueError("A scientific source model is unavailable")
        result, module = item
        if result.request.algorithm == "population_comparison":
            module.load_artifact(engine.store, self.workspace.id, result)
            module.verify_snapshot_sources(self.workspace, result, engine)
        elif module is quality:
            self.validate_file(
                engine.store.quality_path(self.workspace.id, result.id), result.data.sha256
            )
        else:
            for data in result.data:
                self.validate_file(
                    engine.store.analysis_path(self.workspace.id, result.id, data.sample_id),
                    data.sha256,
                )
                if getattr(data, "fitted_ids_sha256", None):
                    self.validate_file(
                        engine.store.fitted_ids_path(self.workspace.id, result.id, data.sample_id),
                        data.fitted_ids_sha256,
                    )

    def validate_frame(self, engine, frame):
        """Verify bytes again on export, including sources already held in engine caches."""

        def closure(value):
            if value.get("kind") == "virtual_group":
                for member in value["sources"]:
                    closure(member)
                return
            identifier = value["sample_id"]
            self.validate_file(
                engine.store.data_path(self.workspace.id, identifier),
                value.get("sample_sha256", value.get("sha256")),
            )
            for model in value.get("models", {}):
                self.validate_model(engine, model)

        kind = frame["kind"]
        if kind == "plot":
            for layer in frame["layers"]:
                closure(layer["source"])
        elif kind == "text":
            for measurement in frame["statistics"].values():
                closure(measurement["source"])
        elif kind in {"table", "plate"}:
            provenance = frame["provenance"] if kind == "table" else frame["data"]["provenance"]
            for sample in provenance.get("samples", []):
                closure(sample)
            for model in provenance.get("models", []):
                self.validate_model(engine, model["id"])
        elif kind == "population_comparison":
            self.validate_model(engine, frame["result_id"])

    def stale(self, identifier):
        item = self.models.get(identifier)
        return item is None or item[1].is_stale(self.workspace, item[0])

    def parameter_stale(self, sample_id, name):
        sample = self.samples[sample_id]
        if name in sample.aliases:
            return self.parameter_stale(sample_id, sample.aliases[name])
        computed = next((p for p in sample.computed_parameters if p.name == name), None)
        if computed and self.stale(computed.analysis_id):
            return True
        derived = next((p for p in sample.derived_parameters if p.name == name), None)
        return bool(
            derived
            and any(self.parameter_stale(sample_id, dep) for dep in parse(derived.expression)[1])
        )

    def population_stale(self, identifier):
        if identifier is None:
            return False
        gate = self.gates[identifier]
        names = {name for name in (gate.x, gate.y) if name}
        for dimension in gate.dimensions:
            names.update(dimension.ratio_channels or (dimension.channel,))
        return (
            any(self.parameter_stale(gate.sample_id, name) for name in names)
            or bool(gate.quality_id and self.stale(gate.quality_id))
            or any(self.population_stale(dep) for dep in [gate.parent_id, *gate.operands])
        )

    def closure(self, sample_id, names=(), populations=(), dimensions=()):
        sample = self.samples[sample_id]
        parameters, gates, models, matrices = {}, {}, {}, {}
        embedded = False

        def observe_model(identifier):
            if identifier in models:
                return
            item = self.models.get(identifier)
            if item is None:
                models[identifier] = dict(id=identifier, missing=True, stale=True)
                return
            result = item[0]
            # Input snapshots and immutable artifact hashes make historical data traceable.
            models[identifier] = dict(result=result.model_dump(), stale=self.stale(identifier))

        def observe_parameter(name):
            if name in parameters:
                return
            parameters[name] = dict(name=name)
            if name in sample.aliases:
                parameters[name]["alias_source"] = sample.aliases[name]
                observe_parameter(sample.aliases[name])
            for field in ("channels", "derived_parameters", "computed_parameters"):
                definition = next((p for p in getattr(sample, field) if p.name == name), None)
                if definition:
                    parameters[name][field] = definition.model_dump()
                    if field == "derived_parameters":
                        for dependency in parse(definition.expression)[1]:
                            observe_parameter(dependency)
                    elif field == "computed_parameters":
                        observe_model(definition.analysis_id)

        def observe_matrix(identifier):
            if identifier and identifier not in {"sample", "uncompensated", "FCS"}:
                matrix = next((m for m in self.workspace.compensations if m.id == identifier), None)
                if matrix:
                    matrices[identifier] = matrix.model_dump()

        def observe_population(identifier):
            nonlocal embedded
            if identifier is None or identifier in gates:
                return
            gate = self.gates[identifier]
            gates[identifier] = gate.model_dump()
            for name in (gate.x, gate.y):
                if name:
                    observe_parameter(name)
            for dimension in gate.dimensions:
                for name in dimension.ratio_channels or (dimension.channel,):
                    observe_parameter(name)
                observe_matrix(dimension.compensation_ref)
                embedded |= dimension.compensation_ref == "FCS"
            if gate.quality_id:
                observe_model(gate.quality_id)
            for dependency in (gate.parent_id, *gate.operands):
                observe_population(dependency)

        observe_matrix(sample.compensation_id)
        for name in names:
            observe_parameter(name)
        for dimension in dimensions:
            if dimension is None:
                continue
            for name in dimension.get("ratio_channels") or (dimension["channel"],):
                observe_parameter(name)
            observe_matrix(dimension["compensation_ref"])
            embedded |= dimension["compensation_ref"] == "FCS"
        for identifier in populations:
            observe_population(identifier)
        return dict(
            sample_id=sample.id,
            sample_name=sample.name,
            sample_sha256=sample.sha256,
            event_count=sample.event_count,
            source=sample.source,
            parameters=parameters,
            populations=gates,
            models=models,
            compensations=matrices,
            embedded_spillover={key: sample.metadata.get(key) for key in ("spillover", "spill")}
            if embedded
            else None,
            stale=any(self.parameter_stale(sample_id, name) for name in parameters)
            or any(self.population_stale(identifier) for identifier in populations),
        )
