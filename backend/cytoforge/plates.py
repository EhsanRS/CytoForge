"""Plate mapping, staged annotations, dilution plans and full-event measurements."""

import csv
import hashlib
import io
import json
import math
import re
from decimal import Decimal, localcontext
from typing import Literal
from xml.sax.saxutils import escape

import numpy as np
from pydantic import Field, model_serializer, model_validator

from . import tables
from .models import (
    PLATE_SHAPES,
    Group,
    Id,
    Model,
    Name,
    PlateDefinition,
    PlateFormat,
    PlateGeometry,
    TableDefinition,
    new_id,
    plate_dimensions,
    plate_position,
    plate_well,
)
from .store import ConflictError, now

MAX_CSV_BYTES = 4 * 1024 * 1024


class PlateEdit(Model):
    revision: int = Field(ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    plate: PlateDefinition


class PlateBatch(Model):
    revision: int = Field(ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    plates: list[PlateDefinition] = Field(min_length=1, max_length=128)


class PlateImport(PlateEdit):
    text: str = Field(max_length=MAX_CSV_BYTES)
    filename: str = Field(default="annotations.csv", max_length=256)
    well_column: str = Field(default="Well ID", min_length=1, max_length=160)
    plate_column: str = Field(default="", max_length=160)
    clear_blanks: bool = False


class PlateApply(PlateEdit):
    wells: list[str] = Field(default_factory=list, max_length=1536)
    keys: list[str] = Field(default_factory=list, max_length=64)
    mode: Literal["replace", "fill_missing"] = "replace"
    include_identity: bool = False
    review_hash: str = ""


class PlateDiscovery(Model):
    revision: int = Field(ge=0)
    group_id: Id | None = None
    format: PlateFormat | None = None
    geometry: PlateGeometry | None = None
    well_key: str = Field(default="WELL ID", min_length=1, max_length=160)
    plate_key: str = Field(default="PLATE ID", min_length=1, max_length=160)
    source: Literal["auto", "tags", "metadata"] = "auto"
    default_plate: Name = "Unidentified plate"
    include_replicates: bool = False

    @model_validator(mode="after")
    def valid_geometry(self):
        if self.format is not None:
            plate_dimensions(self.format, self.geometry)
        elif self.geometry is not None:
            raise ValueError("Choose the custom format when mapping explicit dimensions")
        return self

    @model_serializer(mode="wrap")
    def serialize_geometry(self, serializer):
        value = serializer(self)
        if self.geometry is None:
            value.pop("geometry", None)
        return value


class PlateSeries(PlateEdit):
    keyword: str = Field(default="Concentration", min_length=1, max_length=160)
    unit: str = Field(default="", max_length=80)
    unit_keyword: str = Field(default="Concentration unit", min_length=1, max_length=160)
    start_well: str = "A01"
    steps: int = Field(default=8, ge=1, le=48)
    replicates: int = Field(default=1, ge=1, le=48)
    direction: Literal["rows", "columns"] = "columns"
    operation: Literal["multiply", "add"] = "multiply"
    start: float = Field(default=1, ge=0)
    factor: float = Field(default=0.5, gt=0)
    increment: float = 1


class PlateSelection(PlateEdit):
    wells: list[str] = Field(min_length=1, max_length=1536)
    name: Name = "Plate selection"


class PlateResize(PlateEdit):
    format: PlateFormat
    geometry: PlateGeometry | None = None

    @model_validator(mode="after")
    def valid_geometry(self):
        plate_dimensions(self.format, self.geometry)
        return self


def check_revision(workspace, revision):
    if workspace.revision != revision:
        raise ConflictError("Workspace changed. Refresh the plate before continuing.")


def selected_wells(plate, wells):
    rows, cols = plate.dimensions
    canonical = []
    for key in wells:
        r, c = plate_position(key)
        if r >= rows or c >= cols:
            raise ValueError(f"Well {key} is outside the plate")
        canonical.append(plate_well(r, c))
    if len(canonical) != len(set(canonical)):
        raise ValueError("Select each well only once")
    return canonical


def validate_members(workspace, plate):
    members = {s for ids in plate.assignments.values() for s in ids}
    if not members <= {s.id for s in workspace.samples}:
        raise ValueError("The plate references an unavailable acquisition")


def save_plate(workspace, plate):
    validate_members(workspace, plate)
    workspace.plates = [p for p in workspace.plates if p.id != plate.id] + [
        plate.model_copy(deep=True)
    ]


def save_batch(workspace, body):
    if len({plate.id for plate in body.plates}) != len(body.plates):
        raise ValueError("Batch plate identifiers must be unique")
    for plate in body.plates:
        validate_members(workspace, plate)
    for plate in body.plates:
        save_plate(workspace, plate)


def record(plate, action, details):
    history = list(plate.provenance.get("history", []))[-63:]
    history.append(dict(action=action, at=now(), **details))
    plate.provenance["history"] = history


def keyword(sample, key, source="auto"):
    def normalize(value):
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    target = normalize(key)
    for values in (
        [sample.tags, sample.metadata]
        if source == "auto"
        else [sample.tags if source == "tags" else sample.metadata]
    ):
        found = {v.strip() for k, v in values.items() if normalize(k) == target and v.strip()}
        if len(found) > 1:
            raise ValueError(f"Conflicting normalized values for keyword {key}")
        if found:
            return next(iter(found))
    return ""


def discover(workspace, request):
    check_revision(workspace, request.revision)
    scope = {s.id for s in workspace.samples}
    if request.group_id:
        group = next((g for g in workspace.groups if g.id == request.group_id), None)
        if group is None:
            raise ValueError("The sample group is unavailable")
        scope &= set(group.sample_ids)
    grouped, issues = {}, []
    for sample in workspace.samples:
        if sample.id not in scope:
            continue
        try:
            well = keyword(sample, request.well_key, request.source)
            if not well:
                raise ValueError("Well keyword is missing")
            r, c = plate_position(well)
            if request.format != "custom" and (r >= 32 or c >= 48):
                raise ValueError("Well is outside the supported 1536-well geometry")
            if r >= 96 or c >= 96:
                raise ValueError("Well is outside the supported custom plate dimensions")
            name = keyword(sample, request.plate_key, request.source) or request.default_plate
            if len(name) > 512:
                raise ValueError("Plate identifier exceeds 512 characters")
            grouped.setdefault(name, {}).setdefault(plate_well(r, c), []).append(sample.id)
        except ValueError as exc:
            issues.append(
                dict(severity="error", sample_id=sample.id, sample=sample.name, message=str(exc))
            )
    result = []
    for name, wells in grouped.items():
        maximum = [max(p[i] for p in map(plate_position, wells)) for i in (0, 1)]
        shape = request.format or next(
            n
            for n in (96, 384, 1536)
            if maximum[0] < PLATE_SHAPES[n][0] and maximum[1] < PLATE_SHAPES[n][1]
        )
        rows, cols = plate_dimensions(shape, request.geometry)
        assignments = {}
        for well, ids in wells.items():
            r, c = plate_position(well)
            if r >= rows or c >= cols:
                issues.append(
                    dict(
                        severity="error",
                        plate=name,
                        well=well,
                        message=f"Well is outside the selected {rows} × {cols} plate",
                        sample_ids=ids,
                    )
                )
            elif len(ids) > 1 and not request.include_replicates:
                issues.append(
                    dict(
                        severity="error",
                        plate=name,
                        well=well,
                        sample_ids=ids,
                        message="Duplicate well: choose an acquisition "
                        "or explicitly include replicates",
                    )
                )
            else:
                assignments[well] = ids
        plate = PlateDefinition(
            name=name[:160],
            plate_key=name,
            format=shape,
            geometry=request.geometry,
            assignments=assignments,
        )
        record(
            plate,
            "keyword mapping",
            dict(
                settings=request.model_dump(exclude={"revision"}),
                unresolved=[i for i in issues if i.get("plate") == name],
            ),
        )
        result.append(plate.model_dump())
    return dict(
        plates=result,
        issues=issues,
        revision=workspace.revision,
        assigned_acquisitions=sum(len(v) for p in result for v in p["assignments"].values()),
    )


def import_csv(request):
    content = request.text.encode("utf-8")
    if len(content) > MAX_CSV_BYTES or "\x00" in request.text:
        raise ValueError("Plate CSV exceeds four MiB or contains NUL")
    try:
        reader = csv.reader(io.StringIO(request.text.lstrip("\ufeff"), newline=""), strict=True)
        headings = [h.strip() for h in next(reader)]
        if not headings or any(not h for h in headings) or len(headings) > 66:
            raise ValueError("CSV requires named columns and at most 64 annotation columns")
        if len({h.casefold() for h in headings}) != len(headings):
            raise ValueError("CSV has duplicate column headings")
        lookup = {h.casefold(): i for i, h in enumerate(headings)}
        if request.well_column.casefold() not in lookup:
            raise ValueError(f"CSV is missing the well column {request.well_column}")
        well_index = lookup[request.well_column.casefold()]
        plate_index = lookup.get(request.plate_column.casefold()) if request.plate_column else None
        if request.plate_column and plate_index is None:
            raise ValueError(f"CSV is missing the plate column {request.plate_column}")
        records = [(reader.line_num, row) for row in reader if any(v.strip() for v in row)]
    except (csv.Error, StopIteration) as exc:
        raise ValueError(f"Invalid or empty plate CSV: {exc}") from exc
    if len(records) > 20000:
        raise ValueError("CSV supports at most 20,000 records")
    if plate_index is not None and not request.plate.plate_key:
        identities = {row[plate_index].strip() for _, row in records if len(row) == len(headings)}
        if len(identities) > 1:
            raise ValueError("Set the plate identifier before importing a CSV with multiple plates")
    assignments, issues, skipped = {}, [], 0
    for line, row in records:
        if len(row) != len(headings):
            issues.append(
                dict(severity="error", line=line, message="Row width does not match the header")
            )
            continue
        if (
            plate_index is not None
            and request.plate.plate_key
            and row[plate_index].strip() != request.plate.plate_key
        ):
            skipped += 1
            continue
        try:
            well = selected_wells(request.plate, [row[well_index]])[0]
            values = {
                h: (None if not row[i].strip() else row[i])
                for i, h in enumerate(headings)
                if i not in {well_index, plate_index} and (row[i].strip() or request.clear_blanks)
            }
            assignments.setdefault(well, []).append((line, values))
        except ValueError as exc:
            issues.append(dict(severity="error", line=line, message=str(exc)))
    plate = request.plate.model_copy(deep=True)
    staged = []
    for well, records in assignments.items():
        if len(records) > 1:
            issues.append(
                dict(
                    severity="error",
                    well=well,
                    lines=[r[0] for r in records],
                    message="Duplicate normalized well rows; none of these rows were staged",
                )
            )
            continue
        line, values = records[0]
        plate.annotations.setdefault(well, {}).update(values)
        staged.append(dict(well=well, line=line, values=values))
    record(
        plate,
        "CSV annotation staging",
        dict(
            filename=request.filename,
            sha256=hashlib.sha256(content).hexdigest(),
            well_column=request.well_column,
            plate_column=request.plate_column,
            clear_blanks=request.clear_blanks,
            staged_rows=len(staged),
            skipped_other_plates=skipped,
            issues=issues,
        ),
    )
    plate = PlateDefinition.model_validate(plate.model_dump())
    return dict(
        plate=plate.model_dump(),
        records=staged,
        issues=issues,
        skipped_other_plates=skipped,
        staged_rows=len(staged),
    )


def dilution_series(request):
    if request.unit and request.keyword == request.unit_keyword:
        raise ValueError("Concentration and unit keywords must be distinct")
    plate = request.plate.model_copy(deep=True)
    rows, cols = plate.dimensions
    row, col = plate_position(request.start_well)
    if request.direction == "columns":
        end = (row + request.replicates, col + request.steps)
    else:
        end = (row + request.steps, col + request.replicates)
    if end[0] > rows or end[1] > cols:
        raise ValueError("The dilution rectangle extends beyond this plate")
    records = []
    with localcontext() as context:
        context.prec = 40
        start, factor, increment = map(
            lambda x: Decimal(str(x)), [request.start, request.factor, request.increment]
        )
        for step in range(request.steps):
            value = (
                start * factor**step
                if request.operation == "multiply"
                else start + increment * step
            )
            if value < 0 or not math.isfinite(float(value)) or (value and float(value) == 0):
                raise ValueError(
                    "Dilution values must remain nonnegative and representable as finite numbers"
                )
            text = str(value.normalize()) if value else "0"
            for rep in range(request.replicates):
                r, c = (
                    (row + rep, col + step)
                    if request.direction == "columns"
                    else (row + step, col + rep)
                )
                well = plate_well(r, c)
                values = {request.keyword: text}
                if request.unit:
                    values[request.unit_keyword] = request.unit
                plate.annotations.setdefault(well, {}).update(values)
                records.append(
                    dict(well=well, step=step, replicate=rep, value=text, unit=request.unit)
                )
    record(
        plate,
        "dilution series staging",
        request.model_dump(exclude={"plate", "revision", "base_revision"}),
    )
    return dict(
        plate=PlateDefinition.model_validate(plate.model_dump()).model_dump(), records=records
    )


def annotation_review(workspace, request):
    validate_members(workspace, request.plate)
    selected = (
        selected_wells(request.plate, request.wells)
        if request.wells
        else list(request.plate.annotations)
    )
    if request.include_identity and not request.wells:
        selected = list(set(selected) | set(request.plate.assignments))
    samples = {s.id: s for s in workspace.samples}
    changes, empty, unchanged = [], [], 0
    for well in sorted(selected, key=plate_position):
        planned = {
            k: v
            for k, v in request.plate.annotations.get(well, {}).items()
            if not request.keys or k in request.keys
        }
        if request.include_identity:
            planned.update(
                {"WELL ID": well, "PLATE ID": request.plate.plate_key or request.plate.name}
            )
        ids = request.plate.assignments.get(well, [])
        if not ids and planned:
            empty.append(well)
        for identifier in ids:
            sample = samples[identifier]
            for key, value in planned.items():
                old = sample.tags.get(key)
                if request.mode == "fill_missing" and old not in {None, ""}:
                    unchanged += 1
                    continue
                if old == value:
                    unchanged += 1
                    continue
                changes.append(
                    dict(
                        well=well,
                        sample_id=identifier,
                        sample=sample.name,
                        key=key,
                        before=old,
                        after=value,
                    )
                )
    identity = dict(
        plate=request.plate.model_dump(),
        wells=selected,
        mode=request.mode,
        keys=request.keys,
        include_identity=request.include_identity,
        changes=changes,
    )
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return dict(
        changes=changes,
        change_count=len(changes),
        sample_count=len({c["sample_id"] for c in changes}),
        empty_wells=empty,
        unchanged=unchanged,
        review_hash=digest,
        revision=workspace.revision,
    )


def apply_annotations(workspace, request):
    review = annotation_review(workspace, request)
    if not request.review_hash or request.review_hash != review["review_hash"]:
        raise ConflictError("Annotation changes differ from the reviewed preview. Review again.")
    save_plate(workspace, request.plate)
    samples = {s.id: s for s in workspace.samples}
    for change in review["changes"]:
        tags = samples[change["sample_id"]].tags
        if change["after"] is None:
            tags.pop(change["key"], None)
        else:
            tags[change["key"]] = change["after"]
    record(
        workspace.plates[-1],
        "apply staged annotations",
        dict(
            mode=request.mode,
            change_count=review["change_count"],
            sample_count=review["sample_count"],
            review_hash=review["review_hash"],
        ),
    )


def make_group(workspace, request):
    wells = selected_wells(request.plate, request.wells)
    members = list(dict.fromkeys(i for w in wells for i in request.plate.assignments.get(w, [])))
    if not members:
        raise ValueError("Select at least one well with an acquisition")
    save_plate(workspace, request.plate)
    workspace.groups.append(Group(name=request.name, sample_ids=members))


def resize(request):
    plate = request.plate.model_dump()
    rows, cols = plate_dimensions(request.format, request.geometry)
    removed = sorted(
        {
            w
            for field in (plate["assignments"], plate["annotations"])
            for w in field
            if plate_position(w)[0] >= rows or plate_position(w)[1] >= cols
        },
        key=plate_position,
    )
    for field in ("assignments", "annotations"):
        plate[field] = {w: v for w, v in plate[field].items() if w not in removed}
    plate["format"] = request.format
    plate["geometry"] = request.geometry.model_dump() if request.geometry else None
    result = PlateDefinition.model_validate(plate)
    details = dict(format=request.format, removed_wells=removed)
    if request.geometry:
        details["geometry"] = request.geometry.model_dump()
    record(result, "resize plate", details)
    return dict(
        plate=result.model_dump(),
        removed_wells=removed,
        removed_acquisitions=sum(len(request.plate.assignments.get(w, [])) for w in removed),
        removed_annotation_keys=sum(len(request.plate.annotations.get(w, {})) for w in removed),
    )


def template(plate):
    result = plate.model_dump()
    result["assignments"] = {}
    result["provenance"] = {}
    warnings = []
    for col in result["columns"]:
        if col["population_overrides"] or col["control_sample_id"] or col["result_id"]:
            warnings.append(
                f"{col['name']}: sample/model-specific bindings require review after import"
            )
    return dict(format="cytoforge-plate-template", version=1, plate=result, warnings=warnings)


def import_template(content):
    if len(content) > MAX_CSV_BYTES:
        raise ValueError("Plate template exceeds four MiB")
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid plate template JSON") from exc
    if (
        not isinstance(data, dict)
        or data.get("format") != "cytoforge-plate-template"
        or data.get("version") != 1
    ):
        raise ValueError("Choose a CytoForge version-one plate template")
    plate = PlateDefinition.model_validate(data.get("plate"))
    if any(plate.assignments.values()):
        raise ValueError("A plate template must not carry foreign acquisition assignments")
    plate.id = new_id()
    record(plate, "import plate template", dict(sha256=hashlib.sha256(content).hexdigest()))
    return dict(plate=plate.model_dump(), warnings=template(plate)["warnings"])


def annotation_csv(plate):
    keys = sorted({k for values in plate.annotations.values() for k in values})
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)

    def safe(value):
        value = "" if value is None else str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value

    used = {key.casefold() for key in keys}

    def mapping_header(name):
        header = name
        suffix = 1
        while header.casefold() in used:
            header = f"{name} (mapping {suffix})"
            suffix += 1
        used.add(header.casefold())
        return header

    writer.writerow([mapping_header("Plate ID"), mapping_header("Well ID"), *map(safe, keys)])
    rows, cols = plate.dimensions
    for r in range(rows):
        for c in range(cols):
            well = plate_well(r, c)
            if well in plate.assignments or well in plate.annotations:
                writer.writerow(
                    [
                        safe(plate.plate_key or plate.name),
                        well,
                        *[safe(plate.annotations.get(well, {}).get(k)) for k in keys],
                    ]
                )
    return stream.getvalue()


def aggregate(values, method):
    scale = max((abs(v) for v in values), default=0)
    if not scale:
        return 0.0
    normal = np.asarray(values) / scale
    with np.errstate(over="ignore", invalid="ignore"):
        value = float(
            (
                np.median(normal)
                if method == "median"
                else np.mean(normal)
                if method == "mean"
                else np.sum(normal)
            )
            * scale
        )
    return value if math.isfinite(value) else None


COLORS = [
    "#5ce0b6",
    "#77b9fa",
    "#e7ad67",
    "#be92e8",
    "#ee849c",
    "#aecb75",
    "#5dc8d4",
    "#c2a3dc",
    "#d9c385",
    "#9bb3c6",
    "#d688b5",
    "#b7ccca",
]


def heat_color(fraction):
    if fraction is None:
        return "#273344"
    low, high = (30, 54, 77), (92, 224, 182)
    return "#" + "".join(
        f"{round(a + (b - a) * fraction):02x}" for a, b in zip(low, high, strict=True)
    )


def evaluate(workspace, engine, plate):
    validate_members(workspace, plate)
    members = list(dict.fromkeys(i for ids in plate.assignments.values() for i in ids))
    definition = TableDefinition(
        id=plate.id,
        name=plate.name,
        columns=plate.columns,
        sample_ids=members,
        row_mode="samples",
        compensated=plate.compensated,
    )
    # An empty selection in tables means all samples; an empty plate means no samples.
    result = (
        tables.evaluate_table(workspace, engine, definition, 0, tables.MAX_ROWS)
        if members
        else None
    )
    by_sample = {r["sample_id"]: r for r in result["rows"]} if result else {}
    samples = {s.id: s for s in workspace.samples}
    rows, cols = plate.dimensions
    wells = []
    for r in range(rows):
        for c in range(cols):
            well = plate_well(r, c)
            ids = plate.assignments.get(well, [])
            values, status = {}, {}
            for column in plate.columns:
                cells = [by_sample[i] for i in ids]
                valid = [
                    row["values"].get(column.id)
                    for row in cells
                    if row["values"].get(column.id) is not None
                ]
                reasons = [
                    row["status"].get(column.id) for row in cells if row["status"].get(column.id)
                ]
                if not cells:
                    value = None
                    issue = "No acquisition is assigned"
                elif len(valid) != len(cells) and plate.missing_replicates == "strict":
                    value = None
                    issue = f"{len(valid)} of {len(cells)} replicates available: " + "; ".join(
                        dict.fromkeys(reasons)
                    )
                elif not valid:
                    value = None
                    issue = "; ".join(dict.fromkeys(reasons)) or "Statistic is undefined"
                elif all(isinstance(v, (float, int)) for v in valid):
                    value = aggregate(valid, plate.aggregate)
                    issue = (
                        "Replicate aggregate exceeds finite numeric range"
                        if value is None
                        else f"{len(valid)} of {len(cells)} replicates available"
                        if len(valid) != len(cells)
                        else None
                    )
                else:
                    distinct = list(dict.fromkeys(map(str, valid)))
                    value = distinct[0] if len(distinct) == 1 else "<mixed>"
                    issue = "Replicate keyword values differ" if len(distinct) > 1 else None
                values[column.id] = value
                status[column.id] = issue
            keywords = {}
            all_keys = {k for i in ids for k in samples[i].tags}
            for key in all_keys:
                labels = {samples[i].tags.get(key, "") for i in ids}
                keywords[key] = next(iter(labels)) if len(labels) == 1 else "<mixed>"
            category_labels = {
                getattr(samples[i], plate.view.category_source).get(plate.view.category_keyword, "")
                for i in ids
            }
            category_value = (
                next(iter(category_labels))
                if len(category_labels) == 1
                else "<mixed>"
                if category_labels
                else None
            )
            wells.append(
                dict(
                    well=well,
                    row=r,
                    column=c,
                    sample_ids=ids,
                    samples=[samples[i].name for i in ids],
                    values=values,
                    status=status,
                    keywords=keywords,
                    category_value=category_value,
                    staged=plate.annotations.get(well, {}),
                    acquisitions=[by_sample[i] for i in ids],
                )
            )
    domains = {}
    for column in plate.columns:
        numeric = [
            w["values"][column.id]
            for w in wells
            if isinstance(w["values"][column.id], (float, int))
        ]
        minimum, maximum = (min(numeric), max(numeric)) if numeric else (None, None)
        bounds = plate.view.domains.get(column.id)
        domains[column.id] = dict(
            min=minimum,
            max=maximum,
            display_min=bounds[0] if bounds else minimum,
            display_max=bounds[1] if bounds else maximum,
            finite_wells=len(numeric),
            explicit=bool(bounds),
        )
    primary = plate.view.primary or plate.columns[0].id
    secondary = plate.view.secondary or plate.columns[min(1, len(plate.columns) - 1)].id
    labels = sorted({w["category_value"] for w in wells if w["category_value"]})
    categories = {
        label: plate.view.category_colors.get(label)
        or COLORS[int.from_bytes(hashlib.sha256(label.encode()).digest()[:4], "big") % len(COLORS)]
        for label in labels
    }
    numeric_ids = [c.id for c in plate.columns if tables.numeric_column(c)]
    for well in wells:
        normalized = {}
        clipped = {}
        for identifier, domain in domains.items():
            value = well["values"][identifier]
            lo, hi = domain["display_min"], domain["display_max"]
            if not isinstance(value, (int, float)) or lo is None:
                normalized[identifier] = None
                clipped[identifier] = False
            else:
                # Scaled arithmetic avoids overflow in an extreme finite display range.
                scale = max(abs(lo), abs(hi), abs(value), 1e-300)
                normal = (
                    0.0
                    if value < lo
                    else 1.0
                    if value > hi
                    else (value / scale - lo / scale) / (hi / scale - lo / scale)
                    if hi != lo
                    else 0.5
                )
                normalized[identifier] = float(np.clip(normal, 0, 1))
                clipped[identifier] = value < lo or value > hi
        well.update(
            normalized=normalized,
            clipped=clipped,
            color=heat_color(normalized[primary]),
            secondary_color=heat_color(normalized[secondary]),
            category_color=categories.get(well["category_value"], "#273344"),
            face_svg=face_svg(normalized, numeric_ids) if plate.view.mode == "faces" else "",
        )
    return dict(
        plate=plate.model_dump(),
        revision=workspace.revision,
        rows=rows,
        columns=cols,
        wells=wells,
        domains=domains,
        categories=categories,
        primary=primary,
        secondary=secondary,
        face_features=[
            dict(
                feature=feature,
                column_id=identifier,
                name=next(c.name for c in plate.columns if c.id == identifier),
            )
            for feature, identifier in zip(FACE_FEATURES, numeric_ids, strict=False)
        ],
        acquisition_count=len(members),
        mapped_wells=sum(bool(w["sample_ids"]) for w in wells),
        provenance=result["provenance"] if result else {},
        notices=result["notices"] if result else [],
        aggregation="Equal acquisition weights; aggregates use acquisition statistics",
    )


def measurements_csv(result):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = result["plate"]["columns"]

    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    writer.writerow(
        [
            "Plate",
            "Well ID",
            "Sample IDs",
            "Acquisitions",
            *[safe(c["name"]) for c in columns],
            *[safe(c["name"] + " status") for c in columns],
        ]
    )

    for well in result["wells"]:
        writer.writerow(
            [
                safe(result["plate"]["name"]),
                well["well"],
                ";".join(well["sample_ids"]),
                len(well["sample_ids"]),
                *[safe(well["values"][c["id"]]) for c in columns],
                *[well["status"][c["id"]] or "" for c in columns],
            ]
        )
    return output.getvalue()


FACE_FEATURES = [
    "Head width",
    "Head height",
    "Eye size",
    "Eye separation",
    "Eyebrow slope",
    "Nose length",
    "Mouth curvature",
    "Mouth width",
    "Ear size",
    "Head color",
]


def face_svg(normalized, identifiers):
    """Numeric-only SVG; the UI and standalone export share exactly this mapping."""
    if not identifiers or all(normalized.get(i) is None for i in identifiers):
        return '<path d="M8,8 L32,32 M32,8 L8,32" stroke="#8294a6" fill="none"/>'
    values = [normalized.get(i) for i in identifiers]
    values += [0.5] * (10 - len(values))
    v = [0.5 if x is None else x for x in values]
    width, height, eye, separation = 12 + 4 * v[0], 13 + 4 * v[1], 1.5 + 2 * v[2], 5 + 3 * v[3]
    slope, nose, curve, mouth, ear = (
        (v[4] - 0.5) * 4,
        4 + 5 * v[5],
        (v[6] - 0.5) * 12,
        4 + 6 * v[7],
        1 + 3 * v[8],
    )
    color = heat_color(v[9])
    parts = [
        f'<ellipse cx="20" cy="20" rx="{width}" ry="{height}" '
        f'fill="{color}" stroke="#dce7f1" stroke-width=".7"/>',
        f'<ellipse cx="{20 - width}" cy="20" rx="{ear}" ry="3" fill="{color}"/>',
        f'<ellipse cx="{20 + width}" cy="20" rx="{ear}" ry="3" fill="{color}"/>',
    ]
    for side in [-1, 1]:
        x = 20 + side * separation
        parts.append(f'<circle cx="{x}" cy="16" r="{eye}" fill="#102030"/>')
        parts.append(f'<path d="M{x - 3},11 l6,{side * slope}" stroke="#102030"/>')
    parts.append(f'<path d="M20,17 l-2,{nose} h4" fill="none" stroke="#102030"/>')
    parts.append(
        f'<path d="M{20 - mouth},29 Q20,{29 + curve} {20 + mouth},29" '
        'fill="none" stroke="#102030" stroke-width="1.3"/>'
    )
    if any(x is None for x in values):
        parts.append('<circle cx="35" cy="5" r="3" fill="#edb96c"/>')
    return "".join(parts)


def svg(result):
    """Standalone plate figure with the same colors, features and domains as the UI."""
    cell = 52
    mode = result["plate"]["view"]["mode"]
    columns = {column["id"]: column for column in result["plate"]["columns"]}

    def measure_label(identifier, prefix=""):
        domain = result["domains"][identifier]
        lo, hi = domain["display_min"], domain["display_max"]
        limits = f"{lo:.6g} to {hi:.6g}" if lo is not None else "undefined"
        return f"{prefix}{columns[identifier]['name']}: {limits}"

    if mode == "categories":
        legends = [(label, color) for label, color in result["categories"].items()]
    elif mode == "faces":
        legends = [
            (measure_label(feature["column_id"], feature["feature"] + ": "), None)
            for feature in result["face_features"]
        ]
    else:
        identifiers = [result["primary"]]
        if mode == "split":
            identifiers.append(result["secondary"])
        legends = [(measure_label(identifier), None) for identifier in identifiers]
    width = 80 + cell * result["columns"]
    legend_top = 75 + cell * result["rows"]
    height = legend_top + 75 + 18 * len(legends)

    def text(value):
        return escape(str(value), {'"': "&quot;"})

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#101b28"/>',
        '<g font-family="Arial,sans-serif" fill="#e1ebf5">',
        f'<text x="20" y="26" font-size="16">{text(result["plate"]["name"])}</text>',
    ]
    for c in range(result["columns"]):
        parts.append(
            f'<text x="{65 + c * cell}" y="56" text-anchor="middle" font-size="11">{c + 1}</text>'
        )
    for r in range(result["rows"]):
        parts.append(
            f'<text x="25" y="{91 + r * cell}" text-anchor="middle" font-size="11">'
            f"{plate_well(r, 0)[:-2]}</text>"
        )
    for well in result["wells"]:
        x, y = 65 + cell * well["column"], 85 + cell * well["row"]
        color = well["category_color"] if mode == "categories" else well["color"]
        parts.append(
            f"<g><title>{text(well['well'])}; {text(well['samples'])}; "
            f"{text(well['values'])}; {text(well['status'])}</title>"
        )
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="19" fill="{color}" stroke="#8294a6" stroke-width=".7"/>'
        )
        if mode == "faces":
            parts.append(f'<g transform="translate({x - 20},{y - 20})">{well["face_svg"]}</g>')
        elif mode == "split":
            parts.append(
                f'<path d="M{x},{y - 19} A19,19 0 0 1 {x},{y + 19} Z" '
                f'fill="{well["secondary_color"]}"/>'
            )
        parts.append(
            f'<text x="{x}" y="{y + 26 if mode == "faces" else y + 4}" text-anchor="middle" '
            f'font-size="9" fill="#ffffff">{text(well["well"])}</text></g>'
        )
    for index, (label, color) in enumerate(legends):
        y = legend_top + 18 * index
        # Retain the full label in the tooltip even when its visible text is abbreviated.
        maximum = max(25, int((width - 60) / 6))
        visible = label if len(label) <= maximum else label[: maximum - 1] + "…"
        parts.append(f"<g><title>{text(label)}</title>")
        if color:
            parts.append(f'<rect x="20" y="{y - 9}" width="10" height="10" fill="{color}"/>')
        parts.append(
            f'<text x="{36 if color else 20}" y="{y}" font-size="11">{text(visible)}</text></g>'
        )
    footer = f"{result['plate']['aggregate']} of acquisition statistics; equal acquisition weights"
    parts.append(f'<text x="20" y="{height - 43}" font-size="11">{text(footer)}</text>')
    if mode == "faces":
        parts.append(
            f'<text x="20" y="{height - 29}" font-size="10">'
            "Yellow dot: missing face features; feature order follows numeric measurements.</text>"
        )
    parts.append(
        f'<text x="20" y="{height - 17}" font-size="10">Gray: undefined or unassigned. '
        f"Revision {result['revision']}. Full-event statistics.</text></g></svg>"
    )
    return "".join(parts)
