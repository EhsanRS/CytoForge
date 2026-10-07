from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import (
    autospill,
    autospread,
    biology,
    cellcycle,
    channel_aliases,
    concatenation,
    event_exports,
    kinetics,
    plates,
    population_comparison,
    population_comparison_management,
    population_comparison_views,
    proliferation,
    quality,
    report_templates,
    reports,
    tables,
    virtual_group_gates,
    virtual_groups,
)
from .autogating import AutoGateRequest
from .autogating import preview as preview_automatic_gate
from .compensation import ControlCalculation, assign_matrix, calculate_controls
from .demo import create_demo
from .fileio import close_array, mapped_array
from .import_sessions import MAX_BATCH_BYTES, MAX_UPLOAD_BYTES, ImportSessions
from .imports import ImportCancelled, stream_csv, stream_fcs
from .interchange import (
    MAX_XML_BYTES,
    ImportApply,
    ImportDocument,
    apply_document,
    needs_partial_acknowledgement,
    parse_document,
    suggest_samples,
)
from .jobs import JobManager
from .models import (
    AnalysisRequest,
    CellCycleRequest,
    CellCycleResult,
    Channel,
    ComparisonPresentation,
    Compensation,
    DerivedParameter,
    Gate,
    Group,
    Id,
    KineticsRequest,
    KineticsResult,
    Model,
    Name,
    PlotDimension,
    PopulationComparisonRequest,
    PopulationComparisonResult,
    ProliferationRequest,
    ProliferationResult,
    QualityApply,
    QualityRequest,
    Sample,
    TableDefinition,
    Transform,
    Workspace,
    new_id,
)
from .partitions import replace_gate
from .plot_coordinates import resolve_dimension
from .plot_navigation import NavigationRequest, plan_navigation
from .plotting import plot_payload
from .science import Engine, validate_matrix
from .store import ConflictError, Store, now


class Edit(Model):
    revision: int = Field(ge=0)


class BiologyRename(Edit):
    name: str = Field(min_length=1, max_length=125)


class ComparisonRename(Edit):
    name: Name


class BiologyRemove(Edit):
    cascade: bool = False
    review_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CreateWorkspace(Model):
    name: Name
    description: str = ""


class WorkspaceEdit(Edit):
    name: Name
    description: str = ""


class GateEdit(Edit):
    gate: Gate


class PopulationCapture(Edit):
    sample_id: Id
    gate_id: Id | None = None
    name: Name
    compensated: bool = True


class PlotCoordinateEdit(Edit):
    dimension: PlotDimension
    scope: virtual_groups.Scope | None = None


class GateShapePreview(GateEdit):
    scope: virtual_groups.Scope | None = None
    mode: Literal["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor"] = (
        "density"
    )
    bins: int = Field(default=96, ge=16, le=384)
    bounds: list[float] | None = Field(default=None, min_length=2, max_length=4)


class GateBatch(Edit):
    gates: list[Gate] = Field(min_length=1, max_length=2000)


class GroupEdit(Edit):
    group: Group


class SampleEdit(Edit):
    name: Name
    tags: dict[str, str] = Field(default_factory=dict)


class ApplyGates(Edit):
    source_sample_id: Id
    target_sample_ids: list[Id] = Field(min_length=1)
    replace: bool = False


class MatrixEdit(Edit):
    compensation: Compensation
    sample_ids: list[Id] = Field(default_factory=list)
    acknowledge_unconverged: bool = False
    save_control_populations: bool = False


class AutoSpillApply(Edit):
    sample_ids: list[Id] = Field(default_factory=list, max_length=2000)
    acknowledge_unconverged: bool = False
    save_cleanup_gates: bool = True


class AutoSpillPreview(Edit):
    result_id: Id
    primary: Name
    secondary: Name


class PlateTemplateImport(Edit):
    text: str = Field(max_length=4 * 1024 * 1024)


class AssignMatrix(Edit):
    compensation_id: Id | None = None
    sample_ids: list[Id] = Field(min_length=1)


class TransformEdit(Edit):
    channel: str
    transform: Transform
    sample_ids: list[Id] = Field(min_length=1)


class SaveDefinition(Edit):
    definition: dict


class TableEvaluate(Model):
    definition: TableDefinition
    revision: int | None = Field(default=None, ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=2000)
    include_hidden: bool = False


class DerivedEdit(Edit):
    parameter: DerivedParameter
    sample_ids: list[Id] = Field(min_length=1)


def create_app(data_dir: Path | None = None, frontend_dir: Path | None = None) -> FastAPI:
    root = data_dir or Path(os.environ.get("CYTOFORGE_DATA_DIR", Path.cwd() / "data"))
    store = Store(root)
    engine = Engine(store)
    jobs = JobManager(store)
    imports = ImportSessions(store)
    concatenations = concatenation.Sessions(store)
    exports = event_exports.Sessions(store)
    token = os.environ.get("CYTOFORGE_API_TOKEN") or secrets.token_urlsafe(32)
    uploads = root / "tmp"
    uploads.mkdir(exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        jobs.start()
        try:
            yield
        finally:
            imports.close()
            await run_in_threadpool(concatenations.close)
            await run_in_threadpool(exports.close)
            await run_in_threadpool(jobs.close)
            store.close()

    app = FastAPI(title="CytoForge local engine", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.engine, app.state.token = store, engine, token
    app.state.jobs = jobs
    app.state.import_sessions = imports
    app.state.concatenations = concatenations
    app.state.event_exports = exports
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_security(request: Request, call_next: Callable):
        if request.url.path.startswith("/api/") and request.url.path not in {
            "/api/health",
            "/api/bootstrap",
        }:
            supplied = request.headers.get("X-CytoForge-Token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "Local session token required"}, status_code=401)
        origin = request.headers.get("origin")
        trusted = {
            f"http://{request.headers.get('host')}",
            f"https://{request.headers.get('host')}",
        }
        dev_origin = os.environ.get("CYTOFORGE_FRONTEND_ORIGIN")
        if dev_origin:
            trusted.add(dev_origin)
        if origin and origin not in trusted:
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self' ws://127.0.0.1:*; "
            "font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(_: Request, exc: KeyError):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "cache_bytes": engine.cache.bytes}

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"token": token, "version": "0.1.0"}

    @app.get("/api/workspaces")
    def workspaces():
        return store.list()

    @app.post("/api/workspaces", status_code=201)
    def create(body: CreateWorkspace):
        return store.create(Workspace(**body.model_dump()))

    @app.post("/api/demo", status_code=201)
    def demo():
        return create_demo(store)

    @app.get("/api/workspaces/{workspace_id}")
    def workspace(workspace_id: Id):
        return store.get(workspace_id)

    @app.get("/api/workspaces/{workspace_id}/revision")
    def workspace_revision(workspace_id: Id):
        return {"id": workspace_id, "revision": store.revision(workspace_id)}

    @app.patch("/api/workspaces/{workspace_id}")
    def edit_workspace(workspace_id: Id, body: WorkspaceEdit):
        def change(doc):
            doc.name, doc.description = body.name, body.description

        return store.mutate(workspace_id, "Rename workspace", change, body.revision)

    @app.get("/api/workspaces/{workspace_id}/history")
    def history(workspace_id: Id):
        return store.history(workspace_id)

    @app.post("/api/workspaces/{workspace_id}/plot-navigation/plan")
    def navigation_plan(workspace_id: Id, body: NavigationRequest):
        return plan_navigation(store.get(workspace_id), body, engine)

    @app.post("/api/workspaces/{workspace_id}/undo")
    def undo(workspace_id: Id, body: Edit):
        return store.move_history(workspace_id, -1, body.revision)

    @app.post("/api/workspaces/{workspace_id}/redo")
    def redo(workspace_id: Id, body: Edit):
        return store.move_history(workspace_id, 1, body.revision)

    @app.post("/api/workspaces/{workspace_id}/imports", status_code=201)
    def begin_import(workspace_id: Id, body: Edit):
        return imports.create(workspace_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/imports/{import_id}")
    def import_progress(workspace_id: Id, import_id: Id):
        return imports.get(workspace_id, import_id)

    @app.post("/api/workspaces/{workspace_id}/imports/{import_id}/cancel")
    def cancel_import(workspace_id: Id, import_id: Id):
        return imports.cancel(workspace_id, import_id)

    @app.post("/api/workspaces/{workspace_id}/import")
    async def import_files(
        workspace_id: Id,
        files: Annotated[list[UploadFile], File()],
        revision: int = Query(ge=0),
        allow_duplicates: bool = False,
        import_id: Id | None = None,
    ):
        doc = store.get(workspace_id)
        if doc.revision != revision:
            raise ConflictError("Workspace changed. Reload and retry your import.")
        identifier = import_id or imports.create(workspace_id, revision)["id"]
        directory = imports.root / identifier
        prepared, errors, warnings, datasets = [], [], [], []
        received, started, committed = 0, False, False
        known = {
            (
                s.metadata.get("cytoforge_file_sha256"),
                s.metadata.get("cytoforge_dataset_index", "1"),
                s.metadata.get("cytoforge_dataset_offset", "0"),
            ): s
            for s in doc.samples
            if s.metadata.get("cytoforge_file_sha256")
        }

        def progress(**changes):
            imports.update(workspace_id, identifier, **changes)

        def check():
            imports.check(workspace_id, identifier)

        def commit_samples():
            imports.committing(workspace_id, identifier, [r.sample.id for r in prepared])
            for result in prepared:
                result.path.replace(store.data_path(workspace_id, result.sample.id))
                if result.origins_path:
                    result.origins_path.replace(store.origins_path(workspace_id, result.sample.id))
            if os.name == "posix":
                descriptor = os.open(store.events_dir / workspace_id, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

            def change(current):
                for result in prepared:
                    current.samples.append(result.sample)
                    if result.compensation:
                        current.compensations.append(result.compensation)

            return store.mutate(workspace_id, f"Import {len(prepared)} samples", change, revision)

        try:
            imports.begin(
                workspace_id, identifier, revision, len(files), sum(f.size or 0 for f in files)
            )
            started = True
            for file_index, upload in enumerate(files, 1):
                name = (upload.filename or "Unnamed.fcs").replace("\\", "/").rsplit("/", 1)[-1]
                path = directory / f"{new_id()}.upload"
                progress(
                    status="receiving",
                    stage="Receiving files",
                    file=name,
                    file_index=file_index,
                    dataset_index=0,
                    events_read=0,
                    event_total=0,
                )
                try:
                    size, digest = 0, hashlib.sha256()
                    with path.open("wb") as handle:
                        while chunk := await upload.read(1024 * 1024):
                            check()
                            size += len(chunk)
                            received += len(chunk)
                            if size > MAX_UPLOAD_BYTES:
                                raise ValueError("File exceeds the current 1 GiB upload limit")
                            if received > MAX_BATCH_BYTES:
                                raise ValueError(
                                    "Import batch exceeds the current 4 GiB transfer limit"
                                )
                            digest.update(chunk)
                            handle.write(chunk)
                            progress(bytes_received=received)
                    source_hash = digest.hexdigest()
                    suffix = Path(name).suffix.lower()

                    def identity(index, offset, source_hash=source_hash):
                        return source_hash, str(index), str(offset)

                    def skip(index, offset):
                        return not allow_duplicates and identity(index, offset) in known

                    progress(status="reading")
                    if suffix == ".fcs":
                        results = await run_in_threadpool(
                            stream_fcs, path, name, directory, skip, progress, check
                        )
                    elif suffix == ".csv" and skip(1, 0):
                        warnings.append(
                            {"file": name, "message": "Identical file already imported; skipped"}
                        )
                        old = known[identity(1, 0)]
                        datasets.append(
                            {
                                "file": name,
                                "name": old.name,
                                "sample_id": old.id,
                                "dataset_index": 1,
                                "dataset_count": 1,
                                "event_count": old.event_count,
                                "channel_count": len(old.acquisition_channels),
                                "skipped": True,
                            }
                        )
                        continue
                    elif suffix == ".csv":
                        results = await run_in_threadpool(
                            stream_csv, path, name, directory, progress, check
                        )
                    else:
                        raise ValueError("Choose .fcs or numeric .csv files")
                    # Nothing from this file becomes visible until its whole chain is valid.
                    for result in results:
                        key = identity(result.index, result.offset)
                        sample = known[key] if result.skipped else result.sample
                        datasets.append(
                            {
                                "file": name,
                                "name": sample.name,
                                "sample_id": sample.id,
                                "dataset_index": result.index,
                                "dataset_count": result.count,
                                "event_count": sample.event_count,
                                "channel_count": len(sample.acquisition_channels),
                                "skipped": result.skipped,
                            }
                        )
                        if result.skipped:
                            message = (
                                "Identical file already imported; skipped"
                                if result.count == 1
                                else f"Dataset {result.index} already imported; skipped"
                            )
                            warnings.append({"file": name, "message": message})
                        else:
                            sample.metadata.update(
                                cytoforge_file_sha256=source_hash, cytoforge_import_filename=name
                            )
                            prepared.append(result)
                            known[key] = sample
                            warnings.extend(
                                {"file": name, "message": note} for note in result.warnings
                            )
                    progress(samples_prepared=len(prepared))
                except ImportCancelled:
                    raise
                except Exception as exc:
                    errors.append({"file": name, "message": str(exc)[:1000]})
                finally:
                    path.unlink(missing_ok=True)
                    await upload.close()
            if prepared:
                doc = await run_in_threadpool(commit_samples)
                committed = True
            else:
                imports.committing(workspace_id, identifier, [])
                doc = store.get(workspace_id)
            imports.finish(
                workspace_id,
                identifier,
                "succeeded",
                stage="Import finished",
                imported=len(prepared),
                errors=errors,
                warnings=warnings,
                datasets=datasets,
            )
            return {
                "workspace": doc,
                "imported": len(prepared),
                "errors": errors,
                "warnings": warnings,
                "datasets": datasets,
                "cancelled": False,
                "import_id": identifier,
            }
        except ImportCancelled:
            if started:
                imports.finish(
                    workspace_id,
                    identifier,
                    "cancelled",
                    stage="Import cancelled; no samples added",
                    imported=0,
                    errors=[],
                    warnings=[],
                    datasets=[],
                )
            return {
                "workspace": store.get(workspace_id),
                "imported": 0,
                "errors": [],
                "warnings": [],
                "datasets": [],
                "cancelled": True,
                "import_id": identifier,
            }
        except BaseException as exc:
            if started and not committed:
                imports.finish(
                    workspace_id,
                    identifier,
                    "failed",
                    stage="Import failed; no samples added",
                    imported=0,
                    errors=[{"file": "", "message": str(exc)[:1000]}],
                )
            raise
        finally:
            for upload in files:
                await upload.close()
            if started:
                await run_in_threadpool(imports.clean, workspace_id, identifier)

    @app.post("/api/workspaces/{workspace_id}/interchange/preview")
    async def preview_interchange(
        workspace_id: Id,
        file: Annotated[UploadFile, File()],
        revision: int = Query(ge=0),
    ):
        doc = store.get(workspace_id)
        if doc.revision != revision:
            raise ConflictError("Workspace changed. Reload before preparing the import.")
        try:
            content = await file.read(MAX_XML_BYTES + 1)
            name = (file.filename or "gates.xml").replace("\\", "/").rsplit("/", 1)[-1][:160]
            plan = await run_in_threadpool(parse_document, content, name)
            suggest_samples(plan, doc)
            preview_id = new_id()
            # Original XML and its immutable plan are local, scoped to this workspace/revision.
            source_path = uploads / f"{preview_id}.xml"
            source_path.write_bytes(content)
            (uploads / f"{preview_id}.json").write_text(
                json.dumps(
                    {
                        "workspace_id": workspace_id,
                        "revision": revision,
                        "document": plan.model_dump(),
                    }
                ),
                encoding="utf-8",
            )
            return {
                "preview_id": preview_id,
                "revision": revision,
                "document": plan,
                "requires_acknowledgement": needs_partial_acknowledgement(plan),
            }
        finally:
            await file.close()

    @app.post("/api/workspaces/{workspace_id}/interchange/apply")
    def apply_interchange(workspace_id: Id, body: ImportApply):
        plan_path, xml_path = (
            uploads / f"{body.preview_id}.json",
            uploads / f"{body.preview_id}.xml",
        )
        if not plan_path.exists() or not xml_path.exists():
            raise ValueError("Import preview expired. Upload the source again.")
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if saved["workspace_id"] != workspace_id or saved["revision"] != body.revision:
            raise ConflictError("Import preview belongs to a different workspace or revision")
        plan = ImportDocument.model_validate(saved["document"])
        content = xml_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != plan.sha256:
            raise ValueError("Original import XML failed its integrity check")
        target_paths = []
        try:

            def change(doc):
                record = apply_document(doc, plan, body, engine)
                target = store.interchange_path(workspace_id, record.id)
                target_paths.append(target)
                with target.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if os.name == "posix":
                    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)

            result = store.mutate(
                workspace_id, f"Import gates from {plan.name}", change, body.revision
            )
            target_paths.clear()
            plan_path.unlink(missing_ok=True)
            xml_path.unlink(missing_ok=True)
            return result
        finally:
            for path in target_paths:
                path.unlink(missing_ok=True)

    @app.get("/api/workspaces/{workspace_id}/interchange/{record_id}/{kind}")
    def download_interchange(workspace_id: Id, record_id: Id, kind: str):
        doc = store.get(workspace_id)
        record = next((r for r in doc.interchanges if r.id == record_id), None)
        if record is None:
            raise KeyError("Import record not found")
        if kind == "report":
            return attachment(
                record.model_dump_json(indent=2).encode(), "import-report.json", "application/json"
            )
        if kind != "source":
            raise ValueError("Choose source or report")
        path = store.interchange_path(workspace_id, record_id)
        if not path.exists():
            raise ValueError("Original gate XML is missing. Restore the project archive.")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise ValueError("Original gate XML failed its integrity check")
        return attachment(content, record.name, "application/xml")

    @app.post("/api/workspaces/{workspace_id}/concatenations")
    def prepare_concatenation(workspace_id: Id, body: concatenation.Request):
        return concatenations.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/concatenations/{session_id}")
    def concatenation_status(workspace_id: Id, session_id: Id):
        return concatenations.get(workspace_id, session_id)

    @app.post("/api/workspaces/{workspace_id}/concatenations/{session_id}/cancel")
    def cancel_concatenation(workspace_id: Id, session_id: Id):
        return concatenations.cancel(workspace_id, session_id)

    @app.post("/api/workspaces/{workspace_id}/concatenations/{session_id}/apply")
    def apply_concatenation(workspace_id: Id, session_id: Id, body: concatenation.Apply):
        return concatenations.apply(workspace_id, session_id, body)

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/origins")
    def event_origins(
        workspace_id: Id,
        sample_id: Id,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=2000),
    ):
        sample = engine.sample(store.get(workspace_id), sample_id)
        values = concatenation.validate_origins(store, workspace_id, sample)
        sources = {s.index: s for s in sample.concatenation.sources}
        return dict(
            total=sample.event_count,
            offset=offset,
            rows=[
                dict(
                    event_id=str(offset + index),
                    source_index=int(row[0]),
                    sample_id=sources[int(row[0])].sample_id,
                    sample_name=sources[int(row[0])].sample_name,
                    source_event_id=str(int(row[1])),
                )
                for index, row in enumerate(values[offset : offset + limit])
            ],
        )

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/export/origins")
    def export_event_origins(workspace_id: Id, sample_id: Id):
        from fastapi.responses import StreamingResponse

        sample = engine.sample(store.get(workspace_id), sample_id)
        values = concatenation.validate_origins(store, workspace_id, sample)
        sources = {s.index: s for s in sample.concatenation.sources}

        def rows():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "event_id",
                    "source_index",
                    "source_sample_id",
                    "source_sample_name",
                    "source_event_id",
                ]
            )
            yield output.getvalue()
            for start in range(0, sample.event_count, 2000):
                output.seek(0)
                output.truncate()
                for index, row in enumerate(values[start : start + 2000], start):
                    source = sources[int(row[0])]
                    writer.writerow(
                        [
                            index,
                            int(row[0]),
                            source.sample_id,
                            csv_safe(source.sample_name),
                            int(row[1]),
                        ]
                    )
                yield output.getvalue()

        return StreamingResponse(
            rows(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="event-origins.csv"',
            },
        )

    @app.patch("/api/workspaces/{workspace_id}/samples/{sample_id}")
    def edit_sample(workspace_id: Id, sample_id: Id, body: SampleEdit):
        def change(doc):
            sample = engine.sample(doc, sample_id)
            sample.name, sample.tags = body.name, body.tags

        return store.mutate(workspace_id, "Edit sample metadata", change, body.revision)

    @app.delete("/api/workspaces/{workspace_id}/samples/{sample_id}")
    def remove_sample(workspace_id: Id, sample_id: Id, revision: int = Query(ge=0)):
        def change(doc):
            engine.sample(doc, sample_id)
            doc.samples = [s for s in doc.samples if s.id != sample_id]
            doc.quality_results = [
                q for q in doc.quality_results if q.request.sample_id != sample_id
            ]
            doc.gates = [g for g in doc.gates if g.sample_id != sample_id]
            for group in doc.groups:
                group.sample_ids = [s for s in group.sample_ids if s != sample_id]
            for plate in doc.plates:
                plate.assignments = {
                    well: [s for s in ids if s != sample_id]
                    for well, ids in plate.assignments.items()
                    if any(s != sample_id for s in ids)
                }

        # Immutable event data is retained for undo/recovery.
        return store.mutate(workspace_id, "Remove sample", change, revision)

    @app.post("/api/workspaces/{workspace_id}/gates")
    def add_gate(workspace_id: Id, body: GateEdit):
        def change(doc):
            doc.gates = replace_gate(doc.gates, body.gate, create=True)

        return store.mutate(workspace_id, f"Create {body.gate.name}", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/gates/capture")
    def capture_population(workspace_id: Id, body: PopulationCapture):
        from .population_snapshot import capture

        created = []

        def change(doc):
            gate = capture(
                doc,
                body.sample_id,
                body.gate_id,
                body.name,
                body.compensated,
                Engine(store, cache_bytes=32 * 1024**2),
            )
            created.append(gate.membership.id)
            doc.gates.append(gate)

        try:
            return store.mutate(workspace_id, f"Capture {body.name}", change, body.revision)
        except Exception:
            referenced = store.referenced_memberships(workspace_id)
            for identifier in created:
                if identifier not in referenced:
                    store.membership_path(workspace_id, identifier).unlink(missing_ok=True)
            raise

    @app.post("/api/workspaces/{workspace_id}/virtual-groups/inspect")
    def inspect_virtual_group(workspace_id: Id, body: virtual_groups.Scope):
        doc = store.get(workspace_id)
        view = virtual_groups.PooledEngine(
            doc, engine, body.anchor_id, body.group_id, body.sample_filter
        )
        view.validate(body.channels, [body.gate_id])
        return {
            **view.descriptor(body.gate_id),
            "revision": doc.revision,
            "analysis_inputs": [
                dict(sample_id=s, gate_id=g) for s, g in view.mapping(body.gate_id)
            ],
        }

    @app.post("/api/workspaces/{workspace_id}/virtual-groups/gates/preview")
    def preview_virtual_group_gates(workspace_id: Id, body: virtual_group_gates.Request):
        return virtual_group_gates.plan(store.get(workspace_id), engine, body)[1]

    @app.post("/api/workspaces/{workspace_id}/virtual-groups/gates/apply")
    def apply_virtual_group_gates(workspace_id: Id, body: virtual_group_gates.Apply):
        return store.mutate(
            workspace_id,
            "Apply gates to pooled group",
            lambda doc: virtual_group_gates.apply(doc, engine, body),
            body.revision,
        )

    @app.put("/api/workspaces/{workspace_id}/gates/{gate_id}")
    def update_gate(workspace_id: Id, gate_id: Id, body: GateEdit):
        def change(doc):
            original = next((g for g in doc.gates if g.id == gate_id), None)
            if not original:
                raise KeyError("Gate not found")
            if body.gate.id != gate_id or body.gate.sample_id != original.sample_id:
                raise ValueError("Gate ID and sample cannot be changed")
            doc.gates = replace_gate(doc.gates, body.gate)

        return store.mutate(workspace_id, f"Edit {body.gate.name}", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/gates/automatic-preview")
    def automatic_gate_preview(workspace_id: Id, body: AutoGateRequest):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError("Workspace changed. Restart the automatic gate preview.")
        preview_engine = engine
        if body.scope:
            if body.scope.anchor_id != body.sample_id or body.scope.gate_id != body.parent_id:
                raise ValueError("The automatic gate must match its pooled population")
            preview_engine = virtual_groups.PooledEngine(
                doc, engine, body.sample_id, body.scope.group_id, body.scope.sample_filter
            )
        result = preview_automatic_gate(doc, preview_engine, body)
        if body.scope:
            result["pooled"] = preview_engine.descriptor(body.parent_id)
            result["gate"]["provenance"]["pooled_group"] = result["pooled"]
        return result

    @app.post("/api/workspaces/{workspace_id}/gates/preview-shape")
    def preview_gate_shape(workspace_id: Id, body: GateShapePreview):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError("Workspace changed. Review the gate before previewing its shape.")
        gate = body.gate
        original = next((g for g in doc.gates if g.id == gate.id), None)
        if original and original.sample_id != gate.sample_id:
            raise ValueError("Gate sample cannot be changed")
        if gate.kind not in {
            "range",
            "rectangle",
            "polygon",
            "ellipse",
            "quadrant",
            "hyperrectangle",
            "ellipsoid",
            "spider",
            "curly",
        }:
            raise ValueError("Visual shape editing requires a geometric gate")
        if gate.dimensions and len(gate.dimensions) > 2:
            raise ValueError("Visual shape editing supports one or two gate dimensions")
        if body.scope:
            if body.scope.anchor_id != gate.sample_id:
                raise ValueError("The pooled gate belongs to another representative sample")
            candidate, _ = virtual_group_gates.plan(
                doc,
                engine,
                virtual_group_gates.Request(
                    **body.scope.model_dump(),
                    revision=body.revision,
                    action="edit" if original else "create",
                    gates=[gate],
                ),
            )
        else:
            candidate = doc.model_copy(
                update={"gates": replace_gate(doc.gates, gate, create=original is None)}
            )
        candidate = Workspace.model_validate(candidate.model_dump())
        # Drafts retain the saved ID/revision. Their masks must never populate the
        # live engine's revision-keyed cache, including any affected descendants.
        preview_engine = Engine(store, cache_bytes=64 * 1024 * 1024)
        if body.scope:
            preview_engine = virtual_groups.PooledEngine(
                candidate,
                preview_engine,
                gate.sample_id,
                body.scope.group_id,
                body.scope.sample_filter,
            )
        sample = preview_engine.sample(candidate, gate.sample_id)
        x = gate.dimensions[0].channel if gate.dimensions else gate.x
        y = (
            (gate.dimensions[1].channel if len(gate.dimensions) == 2 else None)
            if gate.dimensions
            else (None if gate.kind == "range" else gate.y)
        )
        payload = plot_payload(
            candidate,
            preview_engine,
            gate.sample_id,
            x,
            y,
            gate.parent_id,
            gate.id,
            gate.dimensions[0].transform if gate.dimensions else gate.x_transform,
            gate.dimensions[1].transform if len(gate.dimensions) == 2 else gate.y_transform,
            body.bins,
            body.bounds,
            body.mode,
            overlay_gate_ids={gate.id},
            native_gate_coordinates=True,
        )
        if body.scope:
            payload = virtual_groups.plot(
                candidate,
                preview_engine.base,
                gate.sample_id,
                body.scope.group_id,
                body.scope.sample_filter,
                x=x,
                y=y,
                gate_id=gate.parent_id,
                coordinate_gate_id=gate.id,
                x_transform=gate.dimensions[0].transform if gate.dimensions else gate.x_transform,
                y_transform=(
                    gate.dimensions[1].transform if len(gate.dimensions) == 2 else gate.y_transform
                ),
                bins=body.bins,
                bounds=body.bounds,
                mode=body.mode,
                overlay_gate_ids={gate.id},
                native_gate_coordinates=True,
            )
        count = int(np.count_nonzero(preview_engine.mask(candidate, sample, gate.id)))
        _, magnetic = preview_engine.resolve_gate(candidate, sample, gate)
        partition_counts = [
            {
                "id": member.id,
                "name": member.name,
                "member": member.partition.member,
                "count": int(np.count_nonzero(preview_engine.mask(candidate, sample, member.id))),
            }
            for member in candidate.gates
            if gate.partition and member.partition and member.partition.id == gate.partition.id
        ]
        return JSONResponse(
            {
                "revision": doc.revision,
                "plot": payload,
                "count": count,
                "parent_count": payload["count"],
                "magnetic": magnetic,
                "partition_counts": partition_counts,
            }
        )

    @app.post("/api/workspaces/{workspace_id}/gates/preview-magnetic")
    def preview_magnetic_gate(workspace_id: Id, body: GateEdit):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError(
                "Workspace changed. Review the gate before previewing its position."
            )
        if body.gate.magnetic is None:
            raise ValueError("Enable magnetic gating before previewing its position")
        original = next((g for g in doc.gates if g.id == body.gate.id), None)
        if original and original.sample_id != body.gate.sample_id:
            raise ValueError("Gate sample cannot be changed")
        candidate = doc.model_copy(
            update={"gates": [*[g for g in doc.gates if g.id != body.gate.id], body.gate]}
        )
        # Validate channels, matrices and the complete dependency graph before
        # evaluating a draft. This read-only route never creates history.
        Workspace.model_validate(candidate.model_dump())
        sample = engine.sample(doc, body.gate.sample_id)
        moved, report = engine.resolve_gate(doc, sample, body.gate)
        return {"gate": moved, "magnetic": report, "revision": doc.revision}

    @app.post("/api/workspaces/{workspace_id}/gates/batch")
    def add_gates(workspace_id: Id, body: GateBatch):
        def change(doc):
            doc.gates.extend(body.gates)

        return store.mutate(workspace_id, "Create populations", change, body.revision)

    @app.delete("/api/workspaces/{workspace_id}/gates/{gate_id}")
    def delete_gate(workspace_id: Id, gate_id: Id, revision: int = Query(ge=0)):
        def change(doc):
            if not any(g.id == gate_id for g in doc.gates):
                raise KeyError("Gate not found")
            remove = {gate_id}
            while True:
                partitions = {g.partition.id for g in doc.gates if g.id in remove and g.partition}
                next_ids = {
                    g.id
                    for g in doc.gates
                    if g.parent_id in remove
                    or set(g.operands) & remove
                    or (g.partition and g.partition.id in partitions)
                }
                if next_ids <= remove:
                    break
                remove |= next_ids
            doc.gates = [g for g in doc.gates if g.id not in remove]

        return store.mutate(workspace_id, "Delete gate and dependent populations", change, revision)

    @app.post("/api/workspaces/{workspace_id}/gates/apply")
    def apply_gates(workspace_id: Id, body: ApplyGates):
        def change(doc):
            engine.sample(doc, body.source_sample_id)
            source = [g for g in doc.gates if g.sample_id == body.source_sample_id]
            if any(g.kind == "quality" for g in source) and set(body.target_sample_ids) - {
                body.source_sample_id
            }:
                raise ValueError(
                    "QC event identities cannot be propagated to another sample. "
                    "Run acquisition QC on each target sample."
                )
            for target_id in set(body.target_sample_ids) - {body.source_sample_id}:
                engine.sample(doc, target_id)
                if body.replace:
                    doc.gates = [g for g in doc.gates if g.sample_id != target_id]
                remap = {g.id: new_id() for g in source}
                partition_remap = {g.partition.id: new_id() for g in source if g.partition}
                for gate in source:
                    doc.gates.append(
                        gate.model_copy(
                            update={
                                "id": remap[gate.id],
                                "sample_id": target_id,
                                "parent_id": remap.get(gate.parent_id),
                                "operands": [remap[o] for o in gate.operands],
                                "partition": (
                                    gate.partition.model_copy(
                                        update={"id": partition_remap[gate.partition.id]}
                                    )
                                    if gate.partition
                                    else None
                                ),
                            },
                            deep=True,
                        )
                    )

        return store.mutate(workspace_id, "Apply gating tree to samples", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/groups")
    def save_group(workspace_id: Id, body: GroupEdit):
        def change(doc):
            doc.groups = [g for g in doc.groups if g.id != body.group.id] + [body.group]

        return store.mutate(workspace_id, f"Save group {body.group.name}", change, body.revision)

    @app.delete("/api/workspaces/{workspace_id}/groups/{group_id}")
    def delete_group(workspace_id: Id, group_id: Id, revision: int = Query(ge=0)):
        def change(doc):
            if not any(g.id == group_id for g in doc.groups):
                raise KeyError("Group not found")
            doc.groups = [g for g in doc.groups if g.id != group_id]

        return store.mutate(workspace_id, "Remove group", change, revision)

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/counts")
    def counts(
        workspace_id: Id,
        sample_id: Id,
        pooled: bool = False,
        group_id: Id | None = None,
        sample_filter: str = "",
    ):
        doc = store.get(workspace_id)
        if pooled:
            return virtual_groups.PooledEngine(
                doc, engine, sample_id, group_id, sample_filter
            ).counts()
        return engine.gate_counts(doc, engine.sample(doc, sample_id))

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/plot")
    def plot(
        workspace_id: Id,
        sample_id: Id,
        x: str,
        y: str | None = None,
        gate_id: Id | None = None,
        backgate_id: Id | None = None,
        x_transform: str | None = None,
        y_transform: str | None = None,
        coordinate_gate_id: Id | None = None,
        mode: str = "density",
        bins: int = Query(default=160, ge=16, le=384),
        bounds: str | None = None,
        graph_options: str | None = None,
        three_d: str | None = None,
        x_dimension: str | None = None,
        y_dimension: str | None = None,
        pooled: bool = False,
        group_id: Id | None = None,
        sample_filter: str = "",
    ):
        # Shared plot payloads already contain JSON-native scalars/containers.
        # Encode once in this worker instead of recursively copying every bin,
        # contour point and overlay again in FastAPI's generic response path.
        if pooled:
            payload = virtual_groups.plot(
                store.get(workspace_id),
                engine,
                sample_id,
                group_id,
                sample_filter,
                x=x,
                y=y,
                gate_id=gate_id,
                coordinate_gate_id=coordinate_gate_id,
                x_transform=Transform.model_validate_json(x_transform) if x_transform else None,
                y_transform=Transform.model_validate_json(y_transform) if y_transform else None,
                bins=bins,
                bounds=json.loads(bounds) if bounds else None,
                mode=mode,
                backgate_id=backgate_id,
                graph_options=json.loads(graph_options) if graph_options else None,
                three_d=json.loads(three_d) if three_d else None,
                x_dimension=PlotDimension.model_validate_json(x_dimension) if x_dimension else None,
                y_dimension=PlotDimension.model_validate_json(y_dimension) if y_dimension else None,
            )
            return JSONResponse(payload)
        payload = plot_payload(
            store.get(workspace_id),
            engine,
            sample_id,
            x,
            y,
            gate_id,
            coordinate_gate_id,
            Transform.model_validate_json(x_transform) if x_transform else None,
            Transform.model_validate_json(y_transform) if y_transform else None,
            bins,
            json.loads(bounds) if bounds else None,
            mode,
            backgate_id,
            json.loads(graph_options) if graph_options else None,
            json.loads(three_d) if three_d else None,
            x_dimension=PlotDimension.model_validate_json(x_dimension) if x_dimension else None,
            y_dimension=PlotDimension.model_validate_json(y_dimension) if y_dimension else None,
        )
        return JSONResponse(payload)

    @app.post("/api/workspaces/{workspace_id}/samples/{sample_id}/coordinates/validate")
    def validate_plot_coordinate(workspace_id: Id, sample_id: Id, body: PlotCoordinateEdit):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError("Workspace changed. Review the coordinate definition again.")
        sample = engine.sample(doc, sample_id)
        if body.scope:
            if body.scope.anchor_id != sample_id:
                raise ValueError("The pooled coordinate belongs to another representative sample")
            scope = body.scope
            view = virtual_groups.PooledEngine(
                doc, engine, sample_id, scope.group_id, scope.sample_filter
            )
            view.validate(
                body.dimension.ratio_channels or [body.dimension.channel], [scope.gate_id]
            )
            for member in view.members:
                resolve_dimension(doc, member, body.dimension.channel, explicit=body.dimension)
        return resolve_dimension(
            doc, sample, body.dimension.channel, explicit=body.dimension
        ).model_dump()

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/plot3d/points")
    def plot3d_points(
        workspace_id: Id,
        sample_id: Id,
        x: str,
        y: str,
        three_d: str,
        revision: int = Query(ge=0),
        data_key: str = Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
        start: int = Query(default=0, ge=0),
        count: int = Query(default=65536, ge=1, le=65536),
        gate_id: Id | None = None,
        backgate_id: Id | None = None,
        coordinate_gate_id: Id | None = None,
        bounds: str | None = None,
        graph_options: str | None = None,
        x_transform: str | None = None,
        y_transform: str | None = None,
        x_dimension: str | None = None,
        y_dimension: str | None = None,
        pooled: bool = False,
        group_id: Id | None = None,
        sample_filter: str = "",
    ):
        from .three_dimensional import point_chunk, prepare

        doc = store.get(workspace_id)
        if doc.revision != revision:
            raise HTTPException(409, "Workspace changed while streaming the 3D view")
        plot_engine = (
            virtual_groups.PooledEngine(doc, engine, sample_id, group_id, sample_filter)
            if pooled
            else engine
        )
        prepared = prepare(
            doc,
            plot_engine,
            sample_id,
            x,
            y,
            json.loads(three_d),
            gate_id,
            coordinate_gate_id,
            Transform.model_validate_json(x_transform) if x_transform else None,
            Transform.model_validate_json(y_transform) if y_transform else None,
            json.loads(bounds) if bounds else None,
            json.loads(graph_options) if graph_options else None,
            backgate_id,
            x_dimension=PlotDimension.model_validate_json(x_dimension) if x_dimension else None,
            y_dimension=PlotDimension.model_validate_json(y_dimension) if y_dimension else None,
        )
        if prepared[0]["data_key"] != data_key:
            raise HTTPException(409, "3D stream does not match the requested axes and population")
        rows = point_chunk(prepared, start, count)
        return Response(
            rows.tobytes(),
            media_type="application/octet-stream",
            headers={
                "X-CytoForge-Revision": str(revision),
                "X-CytoForge-3D-Key": data_key,
                "X-CytoForge-3D-Events": str(len(rows)),
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/statistics")
    def statistics(
        workspace_id: Id,
        sample_id: Id,
        channel: str,
        gate_id: Id | None = None,
        pooled: bool = False,
        group_id: Id | None = None,
        sample_filter: str = "",
    ):
        doc = store.get(workspace_id)
        if pooled:
            view = virtual_groups.PooledEngine(doc, engine, sample_id, group_id, sample_filter)
            view.validate([channel], [gate_id])
            return view.summary(doc, view.view, gate_id, channel)
        return engine.summary(doc, engine.sample(doc, sample_id), gate_id, channel)

    @app.get("/api/workspaces/{workspace_id}/statistics")
    def table(workspace_id: Id, group_id: Id | None = None, channel: str | None = None):
        return build_table(store.get(workspace_id), engine, group_id, channel)

    @app.post("/api/workspaces/{workspace_id}/compensations")
    def save_compensation(workspace_id: Id, body: MatrixEdit):
        validate_matrix(body.compensation)

        def change(doc):
            existing = next((c for c in doc.compensations if c.id == body.compensation.id), None)
            provenance = body.compensation.provenance
            if (
                not existing
                and provenance.get("kind") == "control_calculation"
                and (
                    provenance.get("workspace_id") != doc.id
                    or provenance.get("source_revision") != doc.revision
                )
            ):
                raise ConflictError("Control preview is out of date; recalculate before saving")
            if body.save_control_populations:
                if existing:
                    raise ValueError(
                        "Control populations can be saved when adding a calculated matrix"
                    )
                from .compensation import save_control_populations

                save_control_populations(doc, body.compensation, store)
            if provenance.get("calculated_definition"):
                original = provenance["calculated_definition"]
                current = body.compensation.model_dump()
                provenance["edited_fields"] = [
                    key
                    for key in ("kind", "detectors", "outputs", "matrix", "background", "weights")
                    if current[key] != original.get(key)
                ]
                body.compensation.source = original.get("source", "Control calculation") + (
                    " · edited" if provenance["edited_fields"] else ""
                )
            if provenance.get("kind") == "autospill_calculation" and not existing:
                result = autospill.AutoSpillResult(
                    id=body.compensation.id,
                    request=autospill.AutoSpillRequest.model_validate(provenance["request"]),
                    created_at="",
                    input_hash=provenance["input_hash"],
                    input_snapshot=provenance["input_snapshot"],
                    compensation=body.compensation,
                    diagnostics=provenance["diagnostics"],
                    warnings=provenance["warnings"],
                )
                if provenance.get("workspace_id") != doc.id or autospill.is_stale(doc, result):
                    raise ConflictError("Control preview is out of date; recalculate before saving")
                if not result.diagnostics["converged"] and not body.acknowledge_unconverged:
                    raise ValueError("Acknowledge the unmet residual tolerance before saving")
                provenance["unconverged_acknowledged"] = body.acknowledge_unconverged
            doc.compensations = [c for c in doc.compensations if c.id != body.compensation.id]
            doc.compensations.append(body.compensation)
            targets = set(body.sample_ids) | {
                s.id for s in doc.samples if s.compensation_id == body.compensation.id
            }
            for sample_id in targets:
                assign_matrix(engine.sample(doc, sample_id), body.compensation)

        return store.mutate(workspace_id, f"Save {body.compensation.name}", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/compensations/calculate")
    def calculate_compensation(workspace_id: Id, body: ControlCalculation):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError("Workspace changed; refresh before calculating controls")
        return calculate_controls(doc, body, engine)

    @app.post("/api/workspaces/{workspace_id}/compensations/assign")
    def assign_compensation(workspace_id: Id, body: AssignMatrix):
        def change(doc):
            matrix = next((c for c in doc.compensations if c.id == body.compensation_id), None)
            if body.compensation_id and matrix is None:
                raise ValueError("Compensation matrix not found")
            for sample_id in body.sample_ids:
                assign_matrix(engine.sample(doc, sample_id), matrix)

        return store.mutate(workspace_id, "Change sample compensation", change, body.revision)

    @app.get("/api/workspaces/{workspace_id}/compensations/{matrix_id}/provenance")
    def compensation_provenance(workspace_id: Id, matrix_id: Id):
        matrix = next((c for c in store.get(workspace_id).compensations if c.id == matrix_id), None)
        if matrix is None:
            raise KeyError("Compensation matrix not found")
        return attachment(
            matrix.model_dump_json(indent=2).encode(), "compensation.json", "application/json"
        )

    def autospread_job(doc, job_id, full=True):
        job = jobs.get(doc, job_id, full)
        if job["request"].get("algorithm") != "autospread":
            raise ValueError("This job is not a spreading calculation")
        return job

    def spreading_attachment(doc, result, format):
        stale = autospread.is_stale(doc, result)
        if format == "csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)

            def label(value):
                return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

            writer.writerow(["Primary output / Secondary output", *map(label, result.outputs)])
            for primary, row in zip(result.primaries, result.matrix, strict=True):
                writer.writerow([label(primary), *["" if v is None else v for v in row]])
            response = attachment(
                output.getvalue().encode("utf-8-sig"), "spreading-matrix.csv", "text/csv"
            )
        else:
            response = attachment(
                json.dumps(
                    dict(result=result.model_dump(), stale=stale), indent=2, allow_nan=False
                ).encode(),
                "spreading.json",
                "application/json",
            )
        response.headers["X-CytoForge-Stale"] = str(stale).lower()
        return response

    @app.get("/api/workspaces/{workspace_id}/compensations/autospread/jobs")
    def list_autospread_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), spread=True)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospread/jobs", status_code=202)
    def create_autospread_job(workspace_id: Id, body: autospread.AutoSpreadRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/compensations/autospread/jobs/{job_id}")
    def get_autospread_job(workspace_id: Id, job_id: Id):
        return autospread_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospread/jobs/{job_id}/cancel")
    def cancel_autospread_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        autospread_job(doc, job_id)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospread/jobs/{job_id}/apply")
    def apply_autospread_job(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply_autospread(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/compensations/autospread/jobs/{job_id}/report")
    def autospread_report(workspace_id: Id, job_id: Id, format: Literal["json", "csv"] = "json"):
        doc = store.get(workspace_id)
        job = autospread_job(doc, job_id)
        if job["status"] not in {"succeeded", "applied"}:
            raise ValueError("Spreading reports require a completed calculation")
        return spreading_attachment(
            doc, autospread.AutoSpreadResult.model_validate(job["result"]), format
        )

    @app.get("/api/workspaces/{workspace_id}/compensations/{matrix_id}/spreading")
    def saved_spreading(workspace_id: Id, matrix_id: Id):
        doc = store.get(workspace_id)
        matrix = next((c for c in doc.compensations if c.id == matrix_id), None)
        if matrix is None:
            raise KeyError("Compensation matrix not found")
        if not matrix.provenance.get("autospread"):
            return dict(result=None, stale=False)
        result = autospread.saved_result(matrix)
        return dict(result=result.model_dump(), stale=autospread.is_stale(doc, result))

    @app.get("/api/workspaces/{workspace_id}/compensations/{matrix_id}/spreading/report")
    def saved_spreading_report(
        workspace_id: Id, matrix_id: Id, format: Literal["json", "csv"] = "json"
    ):
        doc = store.get(workspace_id)
        matrix = next((c for c in doc.compensations if c.id == matrix_id), None)
        if matrix is None:
            raise KeyError("Compensation matrix not found")
        return spreading_attachment(doc, autospread.saved_result(matrix), format)

    def autospill_job(doc, job_id, full=True):
        job = jobs.get(doc, job_id, full)
        if job["request"].get("algorithm") != "autospill":
            raise ValueError("This is not an AutoSpill calculation")
        return job

    @app.get("/api/workspaces/{workspace_id}/compensations/autospill/jobs")
    def list_autospill_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), asp=True)

    @app.get("/api/workspaces/{workspace_id}/compensations/autospill/saved")
    def saved_autospill_results(workspace_id: Id):
        doc = store.get(workspace_id)
        return [
            {
                "id": c.id,
                "name": c.name,
                "stale": autospill.is_stale(doc, autospill.saved_result(c)),
            }
            for c in doc.compensations
            if c.provenance.get("kind") == "autospill_calculation"
        ]

    @app.get("/api/workspaces/{workspace_id}/compensations/autospill/saved/{matrix_id}")
    def saved_autospill_result(workspace_id: Id, matrix_id: Id):
        doc = store.get(workspace_id)
        matrix = next((c for c in doc.compensations if c.id == matrix_id), None)
        if matrix is None:
            raise KeyError("Saved AutoSpill matrix not found")
        result = autospill.saved_result(matrix)
        return dict(result=result.model_dump(), stale=autospill.is_stale(doc, result))

    @app.post("/api/workspaces/{workspace_id}/compensations/autospill/preview")
    def autospill_control_preview(workspace_id: Id, body: AutoSpillPreview):
        doc = store.get(workspace_id)
        if body.revision != doc.revision:
            raise ConflictError("Workspace changed; refresh before previewing controls")
        matrix = next((c for c in doc.compensations if c.id == body.result_id), None)
        if matrix is not None:
            result = autospill.saved_result(matrix)
        else:
            job = autospill_job(doc, body.result_id)
            if job["status"] not in {"succeeded", "applied"}:
                raise ValueError("Control previews require a completed calculation")
            result = autospill.AutoSpillResult.model_validate(job["result"])
        return autospill.preview(doc, result, engine, body.primary, body.secondary)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospill/jobs", status_code=202)
    def create_autospill_job(workspace_id: Id, body: autospill.AutoSpillRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/compensations/autospill/jobs/{job_id}")
    def get_autospill_job(workspace_id: Id, job_id: Id):
        return autospill_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospill/jobs/{job_id}/cancel")
    def cancel_autospill_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        autospill_job(doc, job_id)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/compensations/autospill/jobs/{job_id}/apply")
    def apply_autospill_job(workspace_id: Id, job_id: Id, body: AutoSpillApply):
        return jobs.apply_autospill(
            workspace_id,
            job_id,
            body.revision,
            body.sample_ids,
            body.acknowledge_unconverged,
            body.save_cleanup_gates,
        )

    @app.get("/api/workspaces/{workspace_id}/compensations/autospill/jobs/{job_id}/report")
    def autospill_report(workspace_id: Id, job_id: Id):
        job = autospill_job(store.get(workspace_id), job_id)
        if job["status"] not in {"succeeded", "applied"}:
            raise ValueError("The AutoSpill report is available after calculation")
        return attachment(
            json.dumps(job["result"], indent=2).encode(), "autospill.json", "application/json"
        )

    @app.post("/api/workspaces/{workspace_id}/transforms")
    def change_transform(workspace_id: Id, body: TransformEdit):
        def change(doc):
            for sample_id in body.sample_ids:
                sample = engine.sample(doc, sample_id)
                channel = next((c for c in sample.channels if c.name == body.channel), None)
                if not channel:
                    raise ValueError(f"Channel {body.channel} is absent in {sample.name}")
                channel.transform = body.transform
                derived = next(
                    (d for d in sample.derived_parameters if d.name == body.channel), None
                )
                if derived:
                    derived.transform = body.transform

        return store.mutate(workspace_id, "Change display transform", change, body.revision)

    @app.get("/api/workspaces/{workspace_id}/jobs")
    def list_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id))

    @app.post("/api/workspaces/{workspace_id}/jobs", status_code=202)
    def create_job(workspace_id: Id, body: AnalysisRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/jobs/{job_id}")
    def get_job(workspace_id: Id, job_id: Id):
        return jobs.get(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/jobs/{job_id}/cancel")
    def cancel_job(workspace_id: Id, job_id: Id):
        return jobs.cancel(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/jobs/{job_id}/apply")
    def apply_job(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/analyses")
    def analyses(workspace_id: Id):
        from .analysis import is_stale

        doc = store.get(workspace_id)
        return [dict(a.model_dump(), stale=is_stale(doc, a)) for a in doc.analyses]

    @app.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/provenance")
    def analysis_provenance(workspace_id: Id, analysis_id: Id):
        doc = store.get(workspace_id)
        result = next((a for a in doc.analyses if a.id == analysis_id), None)
        if not result:
            raise KeyError("Analysis not found")
        return attachment(
            result.model_dump_json(indent=2).encode(), "analysis.json", "application/json"
        )

    def cell_cycle_job(doc, job_id, full=True):
        job = jobs.get(doc, job_id, full)
        if job["request"].get("algorithm") != "cell_cycle":
            raise ValueError("This is not a cell-cycle job")
        return job

    def cell_cycle_result(doc, result_id):
        result = next((r for r in doc.cell_cycle_results if r.id == result_id), None)
        if result is None:
            job = cell_cycle_job(doc, result_id)
            if job["status"] not in {"succeeded", "applied"}:
                raise ValueError("Cell-cycle results are available after a successful fit")
            result = CellCycleResult.model_validate(job["result"])
        return result

    @app.get("/api/workspaces/{workspace_id}/cell-cycle")
    def cell_cycle_states(workspace_id: Id):
        doc = store.get(workspace_id)
        return [
            {
                "id": result.id,
                "request": {"name": result.request.name},
                "stale": cellcycle.is_stale(doc, result),
            }
            for result in doc.cell_cycle_results
        ]

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/jobs")
    def cell_cycle_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), cc=True)

    @app.post("/api/workspaces/{workspace_id}/cell-cycle/jobs", status_code=202)
    def create_cell_cycle_job(workspace_id: Id, body: CellCycleRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/jobs/{job_id}")
    def get_cell_cycle_job(workspace_id: Id, job_id: Id):
        return cell_cycle_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/cell-cycle/jobs/{job_id}/cancel")
    def cancel_cell_cycle_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        cell_cycle_job(doc, job_id)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/cell-cycle/jobs/{job_id}/apply")
    def apply_cell_cycle_job(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply_cellcycle(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/{result_id}")
    def get_cell_cycle_result(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = cell_cycle_result(doc, result_id)
        return dict(result.model_dump(), stale=cellcycle.is_stale(doc, result))

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/{result_id}/report")
    def cell_cycle_report(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = cell_cycle_result(doc, result_id)
        content = dict(result.model_dump(), stale=cellcycle.is_stale(doc, result))
        return attachment(
            json.dumps(content, indent=2, allow_nan=False).encode(),
            "cell-cycle.json",
            "application/json",
        )

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/{result_id}/statistics")
    def cell_cycle_statistics(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = cell_cycle_result(doc, result_id)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample_id",
                "sample",
                "source_gate_id",
                "model",
                "stale",
                "source_events",
                "fitted_events",
                "G0/G1_fraction",
                "S_fraction",
                "G2/M_fraction",
                "G0/G1_expected_events",
                "S_expected_events",
                "G2/M_expected_events",
                "G0/G1_assigned_events",
                "S_assigned_events",
                "G2/M_assigned_events",
                "G1_mean",
                "G2_mean",
                "G1_CV_percent",
                "G2_CV_percent",
                "G2_G1_ratio",
                "RMSD_events_per_bin",
                "poisson_deviance",
                "converged",
            ]
        )
        samples = {s.id: s.name for s in doc.samples}
        sources = {source.sample_id: source.gate_id for source in result.request.inputs}
        stale = cellcycle.is_stale(doc, result)
        for fit in result.fits:
            writer.writerow(
                [
                    fit.sample_id,
                    samples.get(fit.sample_id, "Removed sample"),
                    sources[fit.sample_id],
                    result.request.method,
                    stale,
                    fit.data.population_count,
                    fit.data.fitted_count,
                    *fit.fractions,
                    *fit.expected_counts,
                    *fit.assigned_counts,
                    fit.parameters["g1_mean"],
                    fit.parameters["g2_mean"],
                    fit.parameters["g1_cv"],
                    fit.parameters["g2_cv"],
                    fit.parameters["peak_ratio"],
                    fit.diagnostics["rmsd_events_per_bin"],
                    fit.diagnostics["poisson_deviance"],
                    fit.diagnostics["converged"],
                ]
            )
        return attachment(stream.getvalue().encode(), "cell-cycle-statistics.csv", "text/csv")

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/{result_id}/events")
    def cell_cycle_events(workspace_id: Id, result_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        result = cell_cycle_result(doc, result_id)
        data = next((d for d in result.data if d.sample_id == sample_id), None)
        if data is None:
            raise ValueError("This sample does not belong to the fitted cell-cycle model")
        values = cellcycle.load_data(store, workspace_id, result, data)
        path = uploads / f"{new_id()}.csv"
        try:
            with path.open("w", newline="", encoding="utf-8") as output:
                output.write(
                    "event_id,G0_G1_probability,S_probability,G2_M_probability,assigned_phase\n"
                )
                for start in range(0, data.event_count, 100000):
                    stop = min(start + 100000, data.event_count)
                    columns = np.c_[np.arange(start, stop), values[start:stop]]
                    np.savetxt(
                        output, columns, fmt=["%u", "%.9g", "%.9g", "%.9g", "%.9g"], delimiter=","
                    )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            path,
            filename="cell-cycle-event-probabilities.csv",
            media_type="text/csv",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/api/workspaces/{workspace_id}/cell-cycle/{result_id}/figure")
    def cell_cycle_figure(workspace_id: Id, result_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        result = cell_cycle_result(doc, result_id)
        fit = next((f for f in result.fits if f.sample_id == sample_id), None)
        if fit is None:
            raise ValueError("This sample does not belong to the fitted cell-cycle model")
        sample = next((s for s in doc.samples if s.id == sample_id), None)
        return attachment(
            cellcycle.figure_svg(
                result,
                fit,
                sample.name if sample else "Removed sample",
                cellcycle.is_stale(doc, result),
            ),
            "cell-cycle.svg",
            "image/svg+xml",
        )

    def proliferation_job(doc, job_id, full=True):
        job = jobs.get(doc, job_id, full)
        if job["request"].get("algorithm") != "proliferation":
            raise ValueError("This is not a proliferation job")
        return job

    def proliferation_result(doc, identifier):
        result = next((r for r in doc.proliferation_results if r.id == identifier), None)
        if result is not None:
            return result
        job = proliferation_job(doc, identifier)
        if job["status"] not in {"succeeded", "applied"} or not job["result"]:
            raise ValueError("This proliferation fit has not completed")
        return ProliferationResult.model_validate(job["result"])

    @app.get("/api/workspaces/{workspace_id}/proliferation")
    def list_proliferation_results(workspace_id: Id):
        doc = store.get(workspace_id)
        return [
            dict(id=r.id, request={"name": r.request.name}, stale=proliferation.is_stale(doc, r))
            for r in doc.proliferation_results
        ]

    @app.get("/api/workspaces/{workspace_id}/proliferation/jobs")
    def list_proliferation_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), pr=True)

    @app.post("/api/workspaces/{workspace_id}/proliferation/jobs", status_code=202)
    def start_proliferation(workspace_id: Id, body: ProliferationRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/proliferation/jobs/{job_id}")
    def get_proliferation_job(workspace_id: Id, job_id: Id):
        return proliferation_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/proliferation/jobs/{job_id}/cancel")
    def cancel_proliferation(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        proliferation_job(doc, job_id, False)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/proliferation/jobs/{job_id}/apply")
    def apply_proliferation(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply_proliferation(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/proliferation/{result_id}")
    def get_proliferation_result(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = proliferation_result(doc, result_id)
        return dict(result.model_dump(), stale=proliferation.is_stale(doc, result))

    @app.get("/api/workspaces/{workspace_id}/proliferation/{result_id}/report")
    def proliferation_report(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = proliferation_result(doc, result_id)
        return attachment(
            json.dumps(
                dict(result.model_dump(), stale=proliferation.is_stale(doc, result)),
                indent=2,
                allow_nan=False,
            ).encode(),
            "proliferation.json",
            "application/json",
        )

    @app.get("/api/workspaces/{workspace_id}/proliferation/{result_id}/statistics")
    def proliferation_statistics_csv(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = proliferation_result(doc, result_id)
        stream, samples = io.StringIO(), {s.id: s.name for s in doc.samples}
        writer = csv.writer(stream)
        keys = list(result.fits[0].statistics.model_dump())
        writer.writerow(
            [
                "sample_id",
                "sample",
                "source_gate_id",
                "stale",
                "distribution",
                "histogram_space",
                "source_events",
                "fitted_events",
                *keys,
                "generation",
                "peak_intensity",
                "model_fraction",
                "model_events",
                "expected_events",
                "assigned_events",
                "undivided_mean",
                "dye_cv_percent",
                "dye_ratio",
                "background",
                "background_sd",
                "RMSD_events_per_bin",
                "converged",
            ]
        )
        inputs = {v.sample_id: v.gate_id for v in result.request.inputs}
        stale = proliferation.is_stale(doc, result)
        for fit in result.fits:
            p = fit.parameters
            for i, fraction in enumerate(fit.fractions):
                writer.writerow(
                    [
                        fit.sample_id,
                        samples.get(fit.sample_id, "Removed sample"),
                        inputs[fit.sample_id],
                        stale,
                        result.request.distribution,
                        result.request.histogram_space,
                        fit.data.population_count,
                        fit.data.fitted_count,
                        *[getattr(fit.statistics, key) for key in keys],
                        i,
                        fit.peak_locations[i],
                        fraction,
                        fraction * fit.data.fitted_count,
                        fit.expected_counts[i],
                        fit.assigned_counts[i],
                        p["undivided_mean"],
                        p["dye_cv"],
                        p["peak_ratio"],
                        p["background"],
                        p["background_sd"],
                        fit.diagnostics["rmsd_events_per_bin"],
                        fit.diagnostics["converged"],
                    ]
                )
        return attachment(stream.getvalue().encode(), "proliferation-statistics.csv", "text/csv")

    @app.get("/api/workspaces/{workspace_id}/proliferation/{result_id}/events")
    def proliferation_events(workspace_id: Id, result_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        result = proliferation_result(doc, result_id)
        data = next((d for d in result.data if d.sample_id == sample_id), None)
        if data is None:
            raise ValueError("This sample does not belong to the fitted proliferation model")
        values = proliferation.load_data(store, workspace_id, result, data)
        path = uploads / f"{new_id()}.csv"
        k = result.request.generations + 1
        try:
            with path.open("w", newline="", encoding="utf-8") as output:
                csv.writer(output).writerow(
                    ["event_id", *[f"G{i}_probability" for i in range(k)], "assigned_generation"]
                )
                for start in range(0, data.event_count, 100000):
                    stop = min(start + 100000, data.event_count)
                    np.savetxt(
                        output,
                        np.c_[np.arange(start, stop), values[start:stop]],
                        fmt=["%u", *(["%.9g"] * (k + 1))],
                        delimiter=",",
                    )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            path,
            filename="proliferation-event-probabilities.csv",
            media_type="text/csv",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/api/workspaces/{workspace_id}/proliferation/{result_id}/figure")
    def proliferation_figure(workspace_id: Id, result_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        result = proliferation_result(doc, result_id)
        fit = next((f for f in result.fits if f.sample_id == sample_id), None)
        if fit is None:
            raise ValueError("This sample does not belong to the fitted proliferation model")
        sample = next((s for s in doc.samples if s.id == sample_id), None)
        return attachment(
            proliferation.figure_svg(
                result,
                fit,
                sample.name if sample else "Removed sample",
                proliferation.is_stale(doc, result),
            ),
            "proliferation.svg",
            "image/svg+xml",
        )

    def kinetics_job(doc, identifier, full=True):
        job = jobs.get(doc, identifier, full)
        if job["request"].get("algorithm") != "kinetics":
            raise ValueError("This is not a kinetics job")
        return job

    def kinetics_result(doc, identifier):
        result = next((r for r in doc.kinetics_results if r.id == identifier), None)
        if result is None:
            job = kinetics_job(doc, identifier)
            if job["status"] not in {"succeeded", "applied"}:
                raise ValueError("Kinetics results are available after a successful calculation")
            result = KineticsResult.model_validate(job["result"])
        return result

    @app.get("/api/workspaces/{workspace_id}/kinetics")
    def list_kinetics_results(workspace_id: Id):
        doc = store.get(workspace_id)
        return [
            dict(id=r.id, request={"name": r.request.name}, stale=kinetics.is_stale(doc, r))
            for r in doc.kinetics_results
        ]

    @app.get("/api/workspaces/{workspace_id}/kinetics/jobs")
    def list_kinetics_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), kin=True)

    @app.post("/api/workspaces/{workspace_id}/kinetics/jobs", status_code=202)
    def start_kinetics(workspace_id: Id, body: KineticsRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/kinetics/jobs/{job_id}")
    def get_kinetics_job(workspace_id: Id, job_id: Id):
        return kinetics_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/kinetics/jobs/{job_id}/cancel")
    def cancel_kinetics_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        kinetics_job(doc, job_id, False)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/kinetics/jobs/{job_id}/apply")
    def save_kinetics_job(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply_kinetics(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}")
    def get_kinetics_result(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = kinetics_result(doc, result_id)
        return dict(result.model_dump(), stale=kinetics.is_stale(doc, result))

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/report")
    def export_kinetics_report(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = kinetics_result(doc, result_id)
        return attachment(
            json.dumps(
                dict(result.model_dump(), stale=kinetics.is_stale(doc, result)),
                indent=2,
                allow_nan=False,
            ).encode(),
            "kinetics.json",
            "application/json",
        )

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/suggested-ranges")
    def suggest_kinetics_ranges(
        workspace_id: Id,
        result_id: Id,
        sample_id: Id,
        maximum: int = Query(default=8, ge=1, le=32),
        prominence: float | None = Query(default=None, gt=0),
    ):
        result = kinetics_result(store.get(workspace_id), result_id)
        fit = next((f for f in result.fits if f.sample_id == sample_id), None)
        if fit is None:
            raise ValueError("This sample does not belong to the kinetics analysis")
        return [r.model_dump() for r in kinetics.suggested_ranges(fit, maximum, prominence)]

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/statistics")
    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/series")
    def export_kinetics_csv(workspace_id: Id, result_id: Id, request: Request):
        doc = store.get(workspace_id)
        result = kinetics_result(doc, result_id)
        series = request.url.path.endswith("/series")
        fields = list(
            kinetics.KineticsBin.model_fields
            if series
            else kinetics.KineticsRangeSummary.model_fields
        )
        samples = {s.id: s.name for s in doc.samples}
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            ["sample_id", "sample", "gate_id", "threshold", "baseline_count", "stale", *fields]
        )
        for fit in result.fits:
            for item in fit.bins if series else fit.ranges:
                writer.writerow(
                    [
                        csv_safe(v)
                        for v in [
                            fit.sample_id,
                            samples.get(fit.sample_id, "Removed sample"),
                            fit.gate_id,
                            fit.threshold,
                            fit.baseline_count,
                            kinetics.is_stale(doc, result),
                            *[getattr(item, k) for k in fields],
                        ]
                    ]
                )
        return attachment(
            output.getvalue().encode(),
            "kinetics-time-series.csv" if series else "kinetics-statistics.csv",
            "text/csv",
        )

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/events")
    def export_kinetics_events(workspace_id: Id, result_id: Id, sample_id: Id):
        result = kinetics_result(store.get(workspace_id), result_id)
        data = next((d for d in result.data if d.sample_id == sample_id), None)
        if data is None:
            raise ValueError("This sample does not belong to the kinetics analysis")
        values = kinetics.load_data(store, workspace_id, result, data)
        path = uploads / f"{new_id()}.csv"
        try:
            with path.open("w", newline="", encoding="utf-8") as output:
                output.write("event_id,aligned_time,signal,source_membership\n")
                for start in range(0, data.event_count, 100000):
                    stop = min(start + 100000, data.event_count)
                    np.savetxt(
                        output,
                        np.c_[np.arange(start, stop), values[start:stop]],
                        fmt=["%u", "%.17g", "%.17g", "%u"],
                        delimiter=",",
                    )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            path,
            filename="kinetics-events.csv",
            media_type="text/csv",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/api/workspaces/{workspace_id}/kinetics/{result_id}/figure")
    def export_kinetics_figure(workspace_id: Id, result_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        result = kinetics_result(doc, result_id)
        fit = next((f for f in result.fits if f.sample_id == sample_id), None)
        if fit is None:
            raise ValueError("This sample does not belong to the kinetics analysis")
        sample = next((s for s in doc.samples if s.id == sample_id), None)
        return attachment(
            kinetics.figure_svg(
                result,
                fit,
                sample.name if sample else "Removed sample",
                kinetics.is_stale(doc, result),
            ),
            "kinetics.svg",
            "image/svg+xml",
        )

    def comparison_job(doc, identifier, full=True):
        job = jobs.get(doc, identifier, full)
        if job["request"].get("algorithm") != "population_comparison":
            raise ValueError("This is not a population comparison job")
        return job

    def comparison_result(doc, identifier):
        result = next((r for r in doc.comparison_results if r.id == identifier), None)
        if result is None:
            job = comparison_job(doc, identifier)
            if job["status"] not in {"succeeded", "applied"}:
                raise ValueError("Comparison results require a successful calculation")
            result = PopulationComparisonResult.model_validate(job["result"])
        return result

    @app.get("/api/workspaces/{workspace_id}/population-comparison")
    def list_comparisons(workspace_id: Id):
        doc = store.get(workspace_id)
        return [
            dict(
                id=r.id,
                request={"name": r.request.name},
                stale=population_comparison.is_stale(doc, r),
            )
            for r in doc.comparison_results
        ]

    @app.get("/api/workspaces/{workspace_id}/population-comparison/jobs")
    def list_comparison_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), comp=True)

    @app.post("/api/workspaces/{workspace_id}/population-comparison/jobs", status_code=202)
    def start_comparison(workspace_id: Id, body: PopulationComparisonRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/population-comparison/jobs/{job_id}")
    def get_comparison_job(workspace_id: Id, job_id: Id):
        return comparison_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/population-comparison/jobs/{job_id}/cancel")
    def cancel_comparison_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        comparison_job(doc, job_id, False)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/population-comparison/jobs/{job_id}/apply")
    def apply_comparison_job(workspace_id: Id, job_id: Id, body: Edit):
        return jobs.apply_comparison(workspace_id, job_id, body.revision)

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}")
    def get_comparison_result(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = comparison_result(doc, result_id)
        population_comparison.load_artifact(store, workspace_id, result)
        return dict(result.model_dump(), stale=population_comparison.is_stale(doc, result))

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}/plot")
    def comparison_plot(
        workspace_id: Id,
        result_id: Id,
        parameter_id: Id,
        target_index: int = Query(default=0, ge=0, le=127),
    ):
        doc = store.get(workspace_id)
        return population_comparison_views.plot_data(
            store, doc, comparison_result(doc, result_id), parameter_id, target_index
        )

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}/figure")
    def comparison_figure(
        workspace_id: Id,
        result_id: Id,
        parameter_id: Id,
        target_index: int = Query(default=0, ge=0, le=127),
        mode: Literal["histogram", "cdf", "difference"] = "histogram",
        smoothing: float = Query(default=0, ge=0, le=16),
        control_color: str = Query(default="#38d9ba", pattern=r"^#[a-fA-F0-9]{6}$"),
        target_color: str = Query(default="#b595f6", pattern=r"^#[a-fA-F0-9]{6}$"),
        difference_scale: float = Query(default=1, ge=0.1, le=10),
        show_individuals: bool = False,
    ):
        doc = store.get(workspace_id)
        result = comparison_result(doc, result_id)
        population_comparison.verify_snapshot_sources(doc, result, engine)
        data = population_comparison_views.plot_data(store, doc, result, parameter_id, target_index)
        return attachment(
            population_comparison_views.figure_svg(
                data,
                result.request.name,
                presentation=ComparisonPresentation(
                    mode=mode,
                    smoothing=smoothing,
                    control_color=control_color,
                    target_color=target_color,
                    difference_scale=difference_scale,
                    show_individuals=show_individuals,
                ),
            ),
            "population-comparison.svg",
            "image/svg+xml",
        )

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}/statistics")
    def comparison_statistics(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = comparison_result(doc, result_id)
        population_comparison.load_artifact(store, workspace_id, result)
        population_comparison.verify_snapshot_sources(doc, result, engine)
        return attachment(
            population_comparison_views.statistics_csv(
                result, population_comparison.is_stale(doc, result)
            ),
            "population-comparison.csv",
            "text/csv",
        )

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}/report")
    def comparison_report(workspace_id: Id, result_id: Id):
        doc = store.get(workspace_id)
        result = comparison_result(doc, result_id)
        population_comparison.load_artifact(store, workspace_id, result)
        population_comparison.verify_snapshot_sources(doc, result, engine)
        return attachment(
            json.dumps(
                dict(result.model_dump(), stale=population_comparison.is_stale(doc, result)),
                indent=2,
                allow_nan=False,
            ).encode(),
            "population-comparison.json",
            "application/json",
        )

    @app.get("/api/workspaces/{workspace_id}/population-comparison/{result_id}/removal")
    def comparison_removal(workspace_id: Id, result_id: Id):
        return population_comparison_management.removal_preview(store.get(workspace_id), result_id)

    @app.delete("/api/workspaces/{workspace_id}/population-comparison/{result_id}")
    def remove_comparison(workspace_id: Id, result_id: Id, body: BiologyRemove):
        return store.mutate(
            workspace_id,
            "Remove population comparison",
            lambda doc: population_comparison_management.remove_result(
                doc,
                result_id,
                cascade=body.cascade,
                review_hash=body.review_hash,
            ),
            body.revision,
        )

    @app.post("/api/workspaces/{workspace_id}/population-comparison/{result_id}/rename")
    def rename_comparison(workspace_id: Id, result_id: Id, body: ComparisonRename):
        def change(doc):
            result = next((r for r in doc.comparison_results if r.id == result_id), None)
            if result is None:
                raise KeyError("Saved comparison not found")
            result.request.name = body.name

        return store.mutate(workspace_id, "Rename population comparison", change, body.revision)

    @app.get("/api/workspaces/{workspace_id}/biology/{platform}/{result_id}/dependencies")
    def biological_model_dependencies(
        workspace_id: Id,
        platform: Literal["cell-cycle", "proliferation", "kinetics"],
        result_id: Id,
    ):
        return biology.removal_preview(store.get(workspace_id), platform, result_id)

    @app.patch("/api/workspaces/{workspace_id}/biology/{platform}/{result_id}")
    def rename_biological_model(
        workspace_id: Id,
        platform: Literal["cell-cycle", "proliferation", "kinetics"],
        result_id: Id,
        body: BiologyRename,
    ):
        return store.mutate(
            workspace_id,
            "Rename biological model",
            lambda doc: biology.rename_model(doc, platform, result_id, body.name),
            body.revision,
        )

    @app.delete("/api/workspaces/{workspace_id}/biology/{platform}/{result_id}")
    def remove_biological_model(
        workspace_id: Id,
        platform: Literal["cell-cycle", "proliferation", "kinetics"],
        result_id: Id,
        body: BiologyRemove,
    ):
        def change(doc):
            preview = biology.removal_preview(doc, platform, result_id)
            if body.review_hash and preview["review_hash"] != body.review_hash:
                raise ConflictError("Model dependencies changed. Review the removal again.")
            biology.remove_model(doc, platform, result_id, body.cascade)

        return store.mutate(workspace_id, "Remove biological model", change, body.revision)

    def quality_job(doc, job_id, full=True):
        job = jobs.get(doc, job_id, full)
        if job["request"].get("algorithm") != "acquisition_qc":
            raise ValueError("This is not an acquisition QC job")
        return job

    def quality_result(doc, quality_id):
        result = next((q for q in doc.quality_results if q.id == quality_id), None)
        if result is None:
            job = quality_job(doc, quality_id)
            if job["status"] not in {"succeeded", "applied"}:
                raise ValueError("QC results are available after a successful diagnostic run")
            from .models import QualityResult

            result = QualityResult.model_validate(job["result"])
        return result

    @app.get("/api/workspaces/{workspace_id}/quality/jobs")
    def quality_jobs(workspace_id: Id):
        return jobs.list(store.get(workspace_id), qc=True)

    @app.post("/api/workspaces/{workspace_id}/quality/jobs", status_code=202)
    def create_quality_job(workspace_id: Id, body: QualityRequest):
        return jobs.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/quality/jobs/{job_id}")
    def get_quality_job(workspace_id: Id, job_id: Id):
        return quality_job(store.get(workspace_id), job_id)

    @app.post("/api/workspaces/{workspace_id}/quality/jobs/{job_id}/cancel")
    def cancel_quality_job(workspace_id: Id, job_id: Id):
        doc = store.get(workspace_id)
        quality_job(doc, job_id)
        return jobs.cancel(doc, job_id)

    @app.post("/api/workspaces/{workspace_id}/quality/jobs/{job_id}/apply")
    def apply_quality_job(workspace_id: Id, job_id: Id, body: QualityApply):
        return jobs.apply_quality(workspace_id, job_id, body)

    @app.get("/api/workspaces/{workspace_id}/quality/{quality_id}")
    def get_quality_result(workspace_id: Id, quality_id: Id):
        doc = store.get(workspace_id)
        result = quality_result(doc, quality_id)
        return dict(result.model_dump(), stale=quality.is_stale(doc, result))

    @app.post("/api/workspaces/{workspace_id}/quality/{quality_id}/review")
    def preview_quality_review(workspace_id: Id, quality_id: Id, body: QualityApply):
        doc = store.get(workspace_id)
        if doc.revision != body.revision:
            raise ConflictError("Workspace changed. Reload before reviewing QC.")
        result = quality_result(doc, quality_id)
        if any(b >= len(result.bins) for b in body.excluded_bins):
            raise ValueError("Review references an acquisition bin that does not exist")
        values = quality.load_data(store, doc.id, result)
        retained = int(quality.selection(values, body.excluded_bins, body.exclusions).sum())
        return {
            "population_count": result.data.population_count,
            "retained_count": retained,
            "rejected_count": result.data.population_count - retained,
            "stale": quality.is_stale(doc, result),
        }

    @app.get("/api/workspaces/{workspace_id}/quality/{quality_id}/report")
    @app.post("/api/workspaces/{workspace_id}/quality/{quality_id}/report")
    def quality_report(workspace_id: Id, quality_id: Id, body: QualityApply | None = None):
        doc = store.get(workspace_id)
        result = quality_result(doc, quality_id)
        draft = None
        if body:
            counts = preview_quality_review(workspace_id, quality_id, body)
            draft = {"choices": body.model_dump(), "counts": counts}
        return attachment(
            json.dumps(
                {
                    "result": result.model_dump(),
                    "stale": quality.is_stale(doc, result),
                    "review_draft": draft,
                    "reviewed_populations": [
                        g.model_dump() for g in doc.gates if g.quality_id == quality_id
                    ],
                },
                allow_nan=False,
                indent=2,
            ).encode(),
            "acquisition-qc.json",
            "application/json",
        )

    @app.post("/api/workspaces/{workspace_id}/quality/{quality_id}/apply")
    def revise_quality_review(workspace_id: Id, quality_id: Id, body: QualityApply):
        doc = store.get(workspace_id)
        result = next((q for q in doc.quality_results if q.id == quality_id), None)
        if result is None:
            raise ValueError("Add this diagnostic job to the workspace before revising its review")
        if quality.is_stale(doc, result):
            raise ConflictError("Scientific inputs changed. Run acquisition QC again.")
        if any(b >= len(result.bins) for b in body.excluded_bins):
            raise ValueError("Review references an acquisition bin that does not exist")
        quality.load_data(store, workspace_id, result)

        def change(document):
            if quality.is_stale(document, result):
                raise ConflictError("Scientific inputs changed. Run acquisition QC again.")
            existing = [g for g in document.gates if g.quality_id == result.id]
            # Reuse IDs, colors and descendants when the review is edited.
            for keep in [True, False] if body.create_rejected else [True]:
                matches = [g for g in existing if g.quality_keep == keep]
                for gate in matches or [
                    Gate(
                        sample_id=result.request.sample_id,
                        name=body.name,
                        kind="quality",
                        quality_id=result.id,
                        quality_keep=keep,
                    )
                ]:
                    gate.name = body.name if keep else f"{body.name[:148]} · Rejected"
                    gate.parent_id = result.request.gate_id
                    gate.quality_excluded_bins = body.excluded_bins
                    gate.quality_exclusions = body.exclusions
                    gate.provenance["reviewed_at"] = now()
                    if gate not in existing:
                        if not keep:
                            gate.color = "#ef8b9b"
                        document.gates.append(gate)
            # Existing rejected gates are kept (and updated) to preserve their descendants.
            if not body.create_rejected:
                for gate in [g for g in existing if not g.quality_keep]:
                    gate.quality_excluded_bins = body.excluded_bins
                    gate.quality_exclusions = body.exclusions

        return store.mutate(workspace_id, "Revise acquisition QC review", change, body.revision)

    @app.get("/api/workspaces/{workspace_id}/quality/{quality_id}/events")
    @app.post("/api/workspaces/{workspace_id}/quality/{quality_id}/events")
    def quality_events(
        workspace_id: Id,
        quality_id: Id,
        gate_id: Id | None = None,
        body: QualityApply | None = None,
    ):
        doc = store.get(workspace_id)
        result = quality_result(doc, quality_id)
        values = quality.load_data(store, doc.id, result)
        if body:
            if doc.revision != body.revision:
                raise ConflictError(
                    "Workspace changed. Reload before exporting QC event identities."
                )
            if any(b >= len(result.bins) for b in body.excluded_bins):
                raise ValueError("Review references an acquisition bin that does not exist")
            retained = quality.selection(values, body.excluded_bins, body.exclusions)
            label = "review_keep"
        elif gate_id:
            gate = next(
                (g for g in doc.gates if g.id == gate_id and g.quality_id == quality_id), None
            )
            if gate is None:
                raise ValueError("Select a reviewed population from this QC result")
            retained = engine.mask(doc, engine.sample(doc, gate.sample_id), gate.id)
            label = "in_reviewed_population"
        else:
            retained = quality.selection(
                values, [b.index for b in result.bins if b.suggested], ["nonfinite", "time"]
            )
            label = "suggested_keep"
        handle = tempfile.NamedTemporaryFile(dir=uploads, suffix=".csv", delete=False)
        path = Path(handle.name)
        handle.close()
        try:
            with path.open("w", newline="") as output:
                output.write(f"event_id,bin_id,in_source,nonfinite,time,saturation,pulse,{label}\n")
                for start in range(0, result.data.event_count, 100000):
                    stop = min(result.data.event_count, start + 100000)
                    flags = values[start:stop, 0]
                    columns = np.column_stack(
                        [
                            np.arange(start, stop, dtype=np.uint32),
                            values[start:stop, 1],
                            (flags & quality.OUTSIDE) == 0,
                            *[(flags & bit) != 0 for bit in quality.FLAGS.values()],
                            retained[start:stop],
                        ]
                    )
                    np.savetxt(output, columns, fmt="%u", delimiter=",")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            path,
            filename="acquisition-qc-events.csv",
            media_type="text/csv",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/workspaces/{workspace_id}/channel-aliases/preview")
    def preview_channel_aliases(workspace_id: Id, body: channel_aliases.Request):
        return channel_aliases.plan(store.get(workspace_id), body)[1]

    @app.post("/api/workspaces/{workspace_id}/channel-aliases/apply")
    def apply_channel_aliases(workspace_id: Id, body: channel_aliases.Apply):
        return store.mutate(
            workspace_id,
            "Harmonize panel channel aliases",
            lambda doc: channel_aliases.apply(doc, body),
            body.revision,
        )

    @app.post("/api/workspaces/{workspace_id}/derived")
    def save_derived(workspace_id: Id, body: DerivedEdit):
        def change(doc):
            for sample_id in set(body.sample_ids):
                sample = engine.sample(doc, sample_id)
                name = body.parameter.name
                old = next((d for d in sample.derived_parameters if d.name == name), None)
                if any(c.name == name for c in sample.channels) and not old:
                    raise ValueError(f"A channel named {name} already exists")
                sample.derived_parameters = [
                    d for d in sample.derived_parameters if d.name != name
                ] + [body.parameter]
                if old:
                    sample.channels = [
                        Channel(
                            name=name,
                            label=body.parameter.label,
                            transform=body.parameter.transform,
                        )
                        if c.name == name
                        else c
                        for c in sample.channels
                    ]
                else:
                    sample.channels.append(
                        Channel(
                            name=name,
                            label=body.parameter.label,
                            transform=body.parameter.transform,
                        )
                    )

        return store.mutate(
            workspace_id, f"Save derived parameter {body.parameter.name}", change, body.revision
        )

    def save_definition(workspace_id: Id, kind: str, body: SaveDefinition):
        if kind not in {"layouts", "tables"}:
            raise HTTPException(404)
        if len(json.dumps(body.definition)) > 1024 * 1024:
            raise ValueError("Definition is too large")
        from .models import LayoutDefinition, TableDefinition

        definition = (LayoutDefinition if kind == "layouts" else TableDefinition).model_validate(
            body.definition
        )

        def change(doc):
            setattr(
                doc,
                kind,
                [v for v in getattr(doc, kind) if v.id != definition.id] + [definition],
            )

        return store.mutate(workspace_id, f"Save {kind[:-1]}", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/tables/save")
    def save_table_definition(workspace_id: Id, body: SaveDefinition):
        return save_definition(workspace_id, "tables", body)

    @app.post("/api/workspaces/{workspace_id}/layouts/save")
    def save_layout_definition(workspace_id: Id, body: SaveDefinition):
        return save_definition(workspace_id, "layouts", body)

    @app.post("/api/workspaces/{workspace_id}/report-templates/export")
    def export_report_template(workspace_id: Id, body: report_templates.TemplateExport):
        template = report_templates.exported(store.get(workspace_id), body)
        filename = "report-template.cytoforge-report.json"
        return Response(
            json.dumps(template, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/workspaces/{workspace_id}/report-templates/parse")
    async def parse_report_template(
        workspace_id: Id,
        revision: Annotated[int, Query(ge=0)],
        file: Annotated[UploadFile, File()],
    ):
        if revision != store.get(workspace_id).revision:
            raise ConflictError("Workspace changed; refresh before loading the report template")
        content = await file.read(report_templates.MAX_TEMPLATE_BYTES + 1)
        return report_templates.parsed(content)

    @app.post("/api/workspaces/{workspace_id}/report-templates/review")
    def review_report_template(workspace_id: Id, body: report_templates.TemplateImport):
        return report_templates.preview(store.get(workspace_id), body, engine)

    @app.post("/api/workspaces/{workspace_id}/report-templates/apply")
    def apply_report_template(workspace_id: Id, body: report_templates.TemplateImport):
        return report_templates.apply(store, workspace_id, body, engine)

    @app.post("/api/workspaces/{workspace_id}/layouts/{layout_id}/delete")
    def delete_layout_definition(workspace_id: Id, layout_id: Id, body: Edit):
        def change(doc):
            if not any(layout.id == layout_id for layout in doc.layouts):
                raise ValueError("Saved report is unavailable")
            doc.layouts = [layout for layout in doc.layouts if layout.id != layout_id]

        return store.mutate(workspace_id, "Remove saved report", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/reports/plan")
    def report_plan(workspace_id: Id, body: reports.ReportRequest):
        return reports.checked_plan(store.get(workspace_id), body, engine)

    @app.post("/api/workspaces/{workspace_id}/reports/render")
    def report_page(workspace_id: Id, body: reports.ReportRequest):
        return reports.render(store.get(workspace_id), engine, body)

    @app.post("/api/workspaces/{workspace_id}/reports/verify")
    def verify_report(workspace_id: Id, body: reports.ReportVerifyRequest):
        workspace = store.get(workspace_id)
        report_plan = reports.checked_plan(workspace, body, engine)
        if not body.review_hash or len({proof.page for proof in body.proofs}) != len(body.proofs):
            raise ValueError("Review distinct report pages before verifying their sources")
        for proof in body.proofs:
            page = reports.render(
                workspace,
                engine,
                body.model_copy(
                    update={"page": proof.page, "prototype_page": None, "validate_sources": True}
                ),
                report_plan,
            )
            if (
                not page["exportable"]
                or page["manifest"]["data_hash"] != proof.data_hash
                or page["manifest"]["svg_sha256"] != proof.svg_sha256
            ):
                raise ConflictError(
                    "Scientific report content changed; prepare and review it again"
                )
        return dict(verified=True, revision=workspace.revision, pages=len(body.proofs))

    @app.post("/api/workspaces/{workspace_id}/reports/export")
    def export_report(workspace_id: Id, body: reports.ReportExportRequest):
        workspace = store.get(workspace_id)
        report_plan = reports.checked_plan(workspace, body, engine)
        if body.definition.batch.mode != "off" and not body.review_hash:
            raise ValueError("Review batch sources before exporting")
        indices = body.pages or (
            [reports.output_index(body, report_plan)]
            if body.format == "svg"
            else list(range(report_plan["page_count"]))
        )
        if not indices or len(indices) > 1024 or len(set(indices)) != len(indices):
            raise ValueError("Choose between one and 1024 distinct report pages")
        if body.format == "svg" and len(indices) != 1:
            raise ValueError("Single SVG export requires one page")
        output = io.BytesIO()
        manifests = []
        total_bytes = 0
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for index in indices:
                page = reports.render(
                    workspace,
                    engine,
                    body.model_copy(
                        update={"page": index, "prototype_page": None, "validate_sources": True}
                    ),
                    report_plan,
                )
                if not page["exportable"]:
                    raise ValueError(
                        "Report has unresolved sources or historical results; "
                        "review its export policy"
                    )
                payload = page["svg"].encode()
                total_bytes += len(payload)
                if total_bytes > 128 * 1024 * 1024:
                    raise ValueError("Export exceeds 128 MiB; export a smaller page selection")
                if body.format == "svg":
                    return attachment(payload, f"report-page-{index + 1}.svg", "image/svg+xml")
                archive.writestr(f"page-{index + 1:04d}.svg", payload)
                manifests.append(page["manifest"])
            archive.writestr(
                "manifest.json",
                json.dumps(
                    dict(review=report_plan, pages=manifests),
                    ensure_ascii=True,
                    allow_nan=False,
                    indent=2,
                ),
            )
        return attachment(output.getvalue(), "report-pages.zip", "application/zip")

    @app.get("/api/workspaces/{workspace_id}/export/statistics.csv")
    def export_statistics(workspace_id: Id, group_id: Id | None = None, channel: str | None = None):
        rows = build_table(store.get(workspace_id), engine, group_id, channel)
        stream = io.StringIO()
        columns = list(dict.fromkeys(k for row in rows for k in row))
        if columns:
            writer = csv.DictWriter(stream, columns)
            writer.writerow({field: csv_safe(field) for field in columns})
            writer.writerows({k: csv_safe(v) for k, v in row.items()} for row in rows)
        return attachment(stream.getvalue().encode(), "statistics.csv", "text/csv")

    @app.post("/api/workspaces/{workspace_id}/plates/discover")
    def discover_plates(workspace_id: Id, body: plates.PlateDiscovery):
        return plates.discover(store.get(workspace_id), body)

    @app.post("/api/workspaces/{workspace_id}/plates/save")
    def save_plate(workspace_id: Id, body: plates.PlateEdit):
        expected = body.base_revision if body.base_revision is not None else body.revision
        return store.mutate(
            workspace_id,
            f"Save plate {body.plate.name}",
            lambda doc: plates.save_plate(doc, body.plate),
            expected,
        )

    @app.delete("/api/workspaces/{workspace_id}/plates/{plate_id}")
    def delete_plate(workspace_id: Id, plate_id: Id, body: Edit):
        def change(doc):
            if not any(p.id == plate_id for p in doc.plates):
                raise KeyError("Plate not found")
            doc.plates = [p for p in doc.plates if p.id != plate_id]

        return store.mutate(workspace_id, "Remove plate definition", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/plates/save-batch")
    def save_plate_batch(workspace_id: Id, body: plates.PlateBatch):
        expected = body.base_revision if body.base_revision is not None else body.revision
        return store.mutate(
            workspace_id,
            "Save discovered plates",
            lambda doc: plates.save_batch(doc, body),
            expected,
        )

    @app.post("/api/workspaces/{workspace_id}/plates/import/csv")
    def import_plate_annotations(workspace_id: Id, body: plates.PlateImport):
        plates.check_revision(store.get(workspace_id), body.revision)
        return plates.import_csv(body)

    @app.post("/api/workspaces/{workspace_id}/plates/series")
    def plate_series(workspace_id: Id, body: plates.PlateSeries):
        plates.check_revision(store.get(workspace_id), body.revision)
        return plates.dilution_series(body)

    @app.post("/api/workspaces/{workspace_id}/plates/resize")
    def resize_plate(workspace_id: Id, body: plates.PlateResize):
        plates.check_revision(store.get(workspace_id), body.revision)
        return plates.resize(body)

    @app.post("/api/workspaces/{workspace_id}/plates/annotations/review")
    def review_plate_annotations(workspace_id: Id, body: plates.PlateApply):
        doc = store.get(workspace_id)
        plates.check_revision(doc, body.revision)
        return plates.annotation_review(doc, body)

    @app.post("/api/workspaces/{workspace_id}/plates/annotations/apply")
    def apply_plate_annotations(workspace_id: Id, body: plates.PlateApply):
        expected = body.base_revision if body.base_revision is not None else body.revision
        return store.mutate(
            workspace_id,
            "Apply staged plate annotations",
            lambda doc: plates.apply_annotations(doc, body),
            expected,
        )

    @app.post("/api/workspaces/{workspace_id}/plates/group")
    def plate_group(workspace_id: Id, body: plates.PlateSelection):
        expected = body.base_revision if body.base_revision is not None else body.revision
        return store.mutate(
            workspace_id,
            f"Create group {body.name} from plate wells",
            lambda doc: plates.make_group(doc, body),
            expected,
        )

    @app.post("/api/workspaces/{workspace_id}/plates/evaluate")
    def evaluate_plate(workspace_id: Id, body: plates.PlateEdit):
        doc = store.get(workspace_id)
        plates.check_revision(doc, body.revision)
        return plates.evaluate(doc, engine, body.plate)

    @app.get("/api/workspaces/{workspace_id}/plates/{plate_id}/evaluate")
    def evaluate_saved_plate(workspace_id: Id, plate_id: Id):
        doc = store.get(workspace_id)
        plate = next((p for p in doc.plates if p.id == plate_id), None)
        if plate is None:
            raise KeyError("Plate not found")
        return plates.evaluate(doc, engine, plate)

    @app.post("/api/workspaces/{workspace_id}/plates/template/import")
    def import_plate_template(workspace_id: Id, body: PlateTemplateImport):
        plates.check_revision(store.get(workspace_id), body.revision)
        return plates.import_template(body.text.encode())

    @app.post("/api/workspaces/{workspace_id}/plates/export/{format}")
    def export_plate(workspace_id: Id, format: str, body: plates.PlateEdit):
        doc = store.get(workspace_id)
        plates.check_revision(doc, body.revision)
        if format == "template":
            return attachment(
                json.dumps(plates.template(body.plate), ensure_ascii=False).encode(),
                "plate-template.json",
                "application/json",
            )
        if format == "annotations":
            return attachment(
                plates.annotation_csv(body.plate).encode(), "plate-annotations.csv", "text/csv"
            )
        if format not in {"json", "csv", "svg"}:
            raise ValueError("Choose annotations, template, JSON, CSV or SVG plate export")
        result = plates.evaluate(doc, engine, body.plate)
        if format == "json":
            return attachment(
                json.dumps(result, ensure_ascii=False).encode(),
                "plate-report.json",
                "application/json",
            )
        if format == "svg":
            return attachment(plates.svg(result).encode(), "plate.svg", "image/svg+xml")
        return attachment(
            plates.measurements_csv(result).encode(), "plate-statistics.csv", "text/csv"
        )

    @app.post("/api/workspaces/{workspace_id}/tables/evaluate")
    def evaluate_custom_table(workspace_id: Id, body: TableEvaluate):
        doc = store.get(workspace_id)
        if body.revision is not None and body.revision != doc.revision:
            raise ConflictError("Workspace changed. Refresh before calculating this table.")
        return tables.evaluate_table(doc, engine, body.definition, body.offset, body.limit)

    @app.get("/api/workspaces/{workspace_id}/tables/{table_id}/evaluate")
    def evaluate_saved_table(workspace_id: Id, table_id: Id, offset: int = 0, limit: int = 2000):
        if offset < 0 or not 1 <= limit <= 2000:
            raise ValueError(
                "Table pages require a positive limit up to 2000 and nonnegative offset"
            )
        doc = store.get(workspace_id)
        definition = next((t for t in doc.tables if t.id == table_id), None)
        if definition is None:
            raise KeyError("Saved table not found")
        return tables.evaluate_table(doc, engine, definition, offset, limit)

    @app.delete("/api/workspaces/{workspace_id}/tables/{table_id}")
    def remove_table(workspace_id: Id, table_id: Id, body: Edit):
        def change(doc):
            if not any(t.id == table_id for t in doc.tables):
                raise KeyError("Saved table not found")
            doc.tables = [t for t in doc.tables if t.id != table_id]

        return store.mutate(workspace_id, "Remove saved table", change, body.revision)

    @app.post("/api/workspaces/{workspace_id}/tables/export/{format}")
    def export_custom_table(workspace_id: Id, format: str, body: TableEvaluate):
        if format not in {"csv", "xlsx", "json"}:
            raise ValueError("Choose CSV, XLSX or JSON table export")
        doc = store.get(workspace_id)
        if body.revision is not None and body.revision != doc.revision:
            raise ConflictError("Workspace changed. Refresh before exporting this table.")
        result = tables.evaluate_table(doc, engine, body.definition, 0, tables.MAX_ROWS)
        if format == "json":
            return attachment(
                json.dumps(result, ensure_ascii=False).encode(), "table.json", "application/json"
            )
        if format == "csv":
            return attachment(
                tables.csv_text(result, body.include_hidden).encode(), "table.csv", "text/csv"
            )
        handle = tempfile.NamedTemporaryFile(dir=uploads, suffix=".xlsx", delete=False)
        path = Path(handle.name)
        handle.close()
        try:
            tables.write_xlsx(path, result, uploads, body.include_hidden)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="table.xlsx",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/workspaces/{workspace_id}/event-exports")
    def prepare_event_export(workspace_id: Id, body: event_exports.Request):
        return exports.submit(store.get(workspace_id), body)

    @app.get("/api/workspaces/{workspace_id}/event-exports")
    def list_event_exports(workspace_id: Id, sample_id: Id):
        doc = store.get(workspace_id)
        engine.sample(doc, sample_id)
        return exports.list(workspace_id, sample_id)

    @app.get("/api/workspaces/{workspace_id}/event-exports/{export_id}")
    def event_export_status(workspace_id: Id, export_id: Id):
        return exports.get(workspace_id, export_id)

    @app.post("/api/workspaces/{workspace_id}/event-exports/{export_id}/cancel")
    def cancel_event_export(workspace_id: Id, export_id: Id):
        return exports.cancel(workspace_id, export_id)

    @app.get("/api/workspaces/{workspace_id}/event-exports/{export_id}/download")
    def download_event_export(workspace_id: Id, export_id: Id):
        from starlette.background import BackgroundTask

        path, filename = exports.download(workspace_id, export_id)
        return FileResponse(
            path,
            filename=filename,
            media_type="text/csv" if path.suffix == ".csv" else "application/octet-stream",
            background=BackgroundTask(exports.release, export_id),
        )

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/export")
    def export_population(
        workspace_id: Id,
        sample_id: Id,
        format: str = "csv",
        gate_id: Id | None = None,
        compensated: bool = True,
    ):
        doc = store.get(workspace_id)
        if format not in {"fcs", "csv"}:
            raise ValueError("Choose CSV or FCS export")
        from starlette.background import BackgroundTask

        directory = Path(tempfile.mkdtemp(prefix="event-export-", dir=uploads))
        try:
            path, _ = event_exports.write_export(
                store,
                engine,
                doc,
                event_exports.Request(
                    revision=doc.revision,
                    sample_id=sample_id,
                    gate_id=gate_id,
                    format=format,
                    values="compensated" if compensated else "raw",
                ),
                directory,
                include_raw_virtual=format == "csv" and not compensated,
            )
        except BaseException:
            shutil.rmtree(directory)
            raise
        return FileResponse(
            path,
            filename="population." + format,
            media_type="text/csv" if format == "csv" else "application/octet-stream",
            background=BackgroundTask(shutil.rmtree, directory),
        )

    @app.get("/api/workspaces/{workspace_id}/samples/{sample_id}/export/gatingml")
    def gatingml_export(workspace_id: Id, sample_id: Id):
        from .gatingml import export_gatingml

        doc = store.get(workspace_id)
        return attachment(
            export_gatingml(doc, engine.sample(doc, sample_id), engine),
            "gates.xml",
            "application/xml",
        )

    @app.get("/api/workspaces/{workspace_id}/export/project")
    def export_project(workspace_id: Id):
        doc = store.get(workspace_id)
        # Stream archive creation to disk; avoid duplicating all datasets in RAM.
        handle = tempfile.NamedTemporaryFile(dir=uploads, suffix=".cytoforge", delete=False)
        archive_path = Path(handle.name)
        handle.close()
        try:
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
            ) as archive:
                document = doc.model_dump(
                    exclude={
                        "quality_results",
                        "cell_cycle_results",
                        "proliferation_results",
                        "kinetics_results",
                        "comparison_results",
                    }
                )
                document["quality_results"] = []
                for result in doc.quality_results:
                    quality.load_data(store, doc.id, result)
                    content = result.model_dump_json().encode()
                    if len(content) > 32 * 1024**2:
                        raise ValueError(
                            "QC report exceeds the portable project's 32 MiB per-report limit"
                        )
                    document["quality_results"].append(
                        {
                            "id": result.id,
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    )
                    archive.writestr(f"quality/{result.id}.json", content)
                    archive.write(store.quality_path(doc.id, result.id), f"quality/{result.id}.npy")
                for field, directory, module in (
                    ("cell_cycle_results", "cell-cycle", cellcycle),
                    ("proliferation_results", "proliferation", proliferation),
                    ("kinetics_results", "kinetics", kinetics),
                ):
                    document[field] = []
                    for result in getattr(doc, field):
                        content = result.model_dump_json().encode()
                        if len(content) > 32 * 1024**2:
                            raise ValueError(
                                f"{directory.title()} report exceeds the 32 MiB per-report limit"
                            )
                        document[field].append(
                            {"id": result.id, "sha256": hashlib.sha256(content).hexdigest()}
                        )
                        archive.writestr(f"{directory}/{result.id}.json", content)
                        for data in result.data:
                            module.load_data(store, doc.id, result, data)
                            archive.write(
                                store.analysis_path(doc.id, result.id, data.sample_id),
                                f"{directory}/{result.id}/{data.sample_id}.npy",
                            )
                if doc.comparison_results:
                    document["comparison_results"] = []
                for result in doc.comparison_results:
                    population_comparison.load_artifact(store, doc.id, result)
                    content = result.model_dump_json().encode()
                    if len(content) > 32 * 1024**2:
                        raise ValueError("Comparison report exceeds the portable 32 MiB limit")
                    document["comparison_results"].append(
                        {"id": result.id, "sha256": hashlib.sha256(content).hexdigest()}
                    )
                    archive.writestr(f"population-comparison/{result.id}.json", content)
                    archive.write(
                        store.comparison_path(doc.id, result.id),
                        f"population-comparison/{result.id}.npz",
                    )
                manifest = json.dumps(
                    {
                        "format": "cytoforge",
                        "version": 2
                        if doc.quality_results
                        or doc.cell_cycle_results
                        or doc.proliferation_results
                        or doc.kinetics_results
                        or doc.comparison_results
                        or any(g.membership for g in doc.gates)
                        else 1,
                        "workspace": document,
                    }
                ).encode()
                if len(manifest) > 32 * 1024**2:
                    raise ValueError(
                        "Workspace manifest exceeds the portable project's 32 MiB limit"
                    )
                archive.writestr(
                    "manifest.json",
                    manifest,
                )
                for sample in doc.samples:
                    archive.write(
                        store.data_path(workspace_id, sample.id), f"events/{sample.id}.npy"
                    )
                    if sample.concatenation:
                        concatenation.validate_origins(store, workspace_id, sample)
                        archive.write(
                            store.origins_path(workspace_id, sample.id),
                            f"origins/{sample.id}.npy",
                        )
                from .population_snapshot import load as load_membership

                memberships = {g.membership.id: g for g in doc.gates if g.membership}
                for identifier, gate in memberships.items():
                    load_membership(
                        engine, doc, engine.sample(doc, gate.sample_id), gate.membership
                    )
                    archive.write(
                        store.membership_path(doc.id, identifier), f"memberships/{identifier}.npy"
                    )
                for record in doc.interchanges:
                    path = store.interchange_path(workspace_id, record.id)
                    if (
                        not path.exists()
                        or hashlib.sha256(path.read_bytes()).hexdigest() != record.sha256
                    ):
                        raise ValueError(
                            "Original gate XML is missing or failed its integrity check"
                        )
                    archive.write(path, f"interchanges/{record.id}.xml")
                for result in doc.analyses:
                    for data in result.data:
                        archive.write(
                            store.analysis_path(workspace_id, result.id, data.sample_id),
                            f"analyses/{result.id}/{data.sample_id}.npy",
                        )
                        if data.fitted_ids_sha256:
                            archive.write(
                                store.fitted_ids_path(workspace_id, result.id, data.sample_id),
                                f"analyses/{result.id}/{data.sample_id}.fit.npy",
                            )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        from starlette.background import BackgroundTask

        return FileResponse(
            archive_path,
            filename="workspace.cytoforge",
            media_type="application/zip",
            background=BackgroundTask(archive_path.unlink, missing_ok=True),
        )

    @app.post("/api/import/project")
    async def import_project(file: Annotated[UploadFile, File()]):
        paths = []
        archive_path = uploads / f"{new_id()}.cytoforge"
        try:
            size = 0
            with archive_path.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 4 * 1024**3:
                        raise ValueError("Archive exceeds 4 GiB")
                    handle.write(chunk)
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                if len({e.filename for e in entries}) != len(entries):
                    raise ValueError("Archive has duplicate entries")
                if sum(e.file_size for e in entries) > 8 * 1024**3:
                    raise ValueError("Archive expansion exceeds 8 GiB")
                if archive.getinfo("manifest.json").file_size > 32 * 1024**2:
                    raise ValueError("Archive manifest is too large")
                manifest = json.loads(archive.read("manifest.json"))
                if not isinstance(manifest, dict) or not isinstance(
                    manifest.get("workspace"), dict
                ):
                    raise ValueError("Invalid CytoForge project manifest")
                archive_version = manifest.get("version")
                if (
                    manifest.get("format") != "cytoforge"
                    or type(archive_version) is not int
                    or (archive_version not in {1, 2})
                ):
                    raise ValueError("Unrecognized CytoForge archive version")
                if manifest["version"] == 2:
                    references = manifest["workspace"].get("quality_results", [])
                    if not isinstance(references, list) or len(references) > 1000:
                        raise ValueError("Invalid portable QC report references")
                    reports = []
                    for reference in references:
                        if not isinstance(reference, dict) or set(reference) != {"id", "sha256"}:
                            raise ValueError("Invalid portable QC report reference")
                        identifier = reference["id"]
                        if (
                            not isinstance(identifier, str)
                            or len(identifier) != 32
                            or any(c not in "0123456789abcdef" for c in identifier)
                        ):
                            raise ValueError("Invalid portable QC report ID")
                        entry = f"quality/{identifier}.json"
                        if archive.getinfo(entry).file_size > 32 * 1024**2:
                            raise ValueError("Portable QC report exceeds 32 MiB")
                        content = archive.read(entry)
                        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                            raise ValueError("QC diagnostic report failed its integrity check")
                        report = json.loads(content)
                        if not isinstance(report, dict) or report.get("id") != identifier:
                            raise ValueError("Portable QC report ID does not match its reference")
                        reports.append(report)
                    manifest["workspace"]["quality_results"] = reports
                    restore_portable_reports(
                        archive, manifest["workspace"], "cell_cycle_results", "cell-cycle"
                    )
                    restore_portable_reports(
                        archive, manifest["workspace"], "proliferation_results", "proliferation"
                    )
                    restore_portable_reports(
                        archive, manifest["workspace"], "kinetics_results", "kinetics"
                    )
                    restore_portable_reports(
                        archive,
                        manifest["workspace"],
                        "comparison_results",
                        "population-comparison",
                    )
                doc = Workspace.model_validate(manifest["workspace"])
                for matrix in doc.compensations:
                    validate_matrix(matrix)
                doc.id = new_id()
                for record in doc.interchanges:
                    entry = f"interchanges/{record.id}.xml"
                    if archive.getinfo(entry).file_size > MAX_XML_BYTES:
                        raise ValueError("Original gate XML exceeds 32 MiB")
                    content = archive.read(entry)
                    if hashlib.sha256(content).hexdigest() != record.sha256:
                        raise ValueError("Original gate XML failed its integrity check")
                    target = store.interchange_path(doc.id, record.id)
                    paths.append(target)
                    target.write_bytes(content)
                for sample in doc.samples:
                    entry = f"events/{sample.id}.npy"
                    target = store.data_path(doc.id, sample.id)
                    paths.append(target)
                    with archive.open(entry) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest, 1024 * 1024)
                    with target.open("rb") as handle:
                        digest = hashlib.file_digest(handle, "sha256").hexdigest()
                    if digest != sample.sha256:
                        raise ValueError(f"Integrity check failed for {sample.name}")
                    with mapped_array(target) as values:
                        if values.dtype.kind != "f" or values.shape != (
                            sample.event_count,
                            len(sample.acquisition_channels),
                        ):
                            raise ValueError(f"Invalid event data for {sample.name}")
                    if sample.concatenation:
                        origin_target = store.origins_path(doc.id, sample.id)
                        paths.append(origin_target)
                        with (
                            archive.open(f"origins/{sample.id}.npy") as source,
                            origin_target.open("wb") as dest,
                        ):
                            shutil.copyfileobj(source, dest, 1024 * 1024)
                        close_array(concatenation.validate_origins(store, doc.id, sample))
                for result in doc.analyses:
                    for data in result.data:
                        target = store.analysis_path(doc.id, result.id, data.sample_id)
                        paths.append(target)
                        with (
                            archive.open(f"analyses/{result.id}/{data.sample_id}.npy") as source,
                            target.open("wb") as dest,
                        ):
                            shutil.copyfileobj(source, dest, 1024 * 1024)
                        with target.open("rb") as handle:
                            digest = hashlib.file_digest(handle, "sha256").hexdigest()
                        if digest != data.sha256:
                            raise ValueError("Analysis data failed its integrity check")
                        if data.fitted_ids_sha256:
                            fitted_target = store.fitted_ids_path(doc.id, result.id, data.sample_id)
                            paths.append(fitted_target)
                            with (
                                archive.open(
                                    f"analyses/{result.id}/{data.sample_id}.fit.npy"
                                ) as source,
                                fitted_target.open("wb") as dest,
                            ):
                                shutil.copyfileobj(source, dest, 1024 * 1024)
                        from .analysis import validate_result_data

                        with mapped_array(target) as values:
                            if values.dtype.kind != "f" or values.shape != (
                                data.event_count,
                                len(result.columns),
                            ):
                                raise ValueError("Invalid event-aligned analysis data")
                            validate_result_data(store, doc.id, result, data, values)
                for result in doc.quality_results:
                    target = store.quality_path(doc.id, result.id)
                    paths.append(target)
                    with (
                        archive.open(f"quality/{result.id}.npy") as source,
                        target.open("wb") as dest,
                    ):
                        shutil.copyfileobj(source, dest, 1024 * 1024)
                    close_array(quality.load_data(store, doc.id, result))
                for field, directory, module in (
                    ("cell_cycle_results", "cell-cycle", cellcycle),
                    ("proliferation_results", "proliferation", proliferation),
                    ("kinetics_results", "kinetics", kinetics),
                ):
                    for result in getattr(doc, field):
                        for data in result.data:
                            target = store.analysis_path(doc.id, result.id, data.sample_id)
                            paths.append(target)
                            with (
                                archive.open(
                                    f"{directory}/{result.id}/{data.sample_id}.npy"
                                ) as source,
                                target.open("wb") as dest,
                            ):
                                shutil.copyfileobj(source, dest, 1024 * 1024)
                            close_array(module.load_data(store, doc.id, result, data))
                for result in doc.comparison_results:
                    entry = f"population-comparison/{result.id}.npz"
                    if archive.getinfo(entry).file_size > population_comparison.MAX_ARTIFACT_BYTES:
                        raise ValueError("Portable comparison plot data exceeds 256 MiB")
                    target = store.comparison_path(doc.id, result.id)
                    paths.append(target)
                    with archive.open(entry) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest, 1024 * 1024)
                    population_comparison.load_artifact(store, doc.id, result)
                from .population_snapshot import load as load_membership

                memberships = {g.membership.id: g for g in doc.gates if g.membership}
                for identifier, gate in memberships.items():
                    entry = f"memberships/{identifier}.npy"
                    if (
                        archive.getinfo(entry).file_size
                        > (gate.membership.event_count + 7) // 8 + 4096
                    ):
                        raise ValueError(
                            "Captured population archive data exceed their event count"
                        )
                    target = store.membership_path(doc.id, identifier)
                    paths.append(target)
                    with archive.open(entry) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest, 1024 * 1024)
                    load_membership(
                        Engine(store, cache_bytes=32 * 1024**2),
                        doc,
                        next(s for s in doc.samples if s.id == gate.sample_id),
                        gate.membership,
                    )
                result = store.create(doc)
                paths.clear()
                return result
        except (zipfile.BadZipFile, KeyError, ValidationError, TypeError, RecursionError) as exc:
            raise ValueError(f"Invalid project archive: {exc}") from exc
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)
            await file.close()

    static = frontend_dir or Path(
        os.environ.get("CYTOFORGE_FRONTEND_DIR", Path.cwd() / "frontend/dist")
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


def restore_portable_reports(archive, document, field, directory):
    references = document.get(field, [])
    if not isinstance(references, list) or len(references) > 1000:
        raise ValueError("Invalid portable scientific report references")
    reports = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"id", "sha256"}:
            raise ValueError("Invalid portable scientific report reference")
        identifier = reference["id"]
        if (
            not isinstance(identifier, str)
            or len(identifier) != 32
            or any(c not in "0123456789abcdef" for c in identifier)
        ):
            raise ValueError("Invalid portable scientific report ID")
        entry = f"{directory}/{identifier}.json"
        if archive.getinfo(entry).file_size > 32 * 1024**2:
            raise ValueError("Portable scientific report exceeds 32 MiB")
        content = archive.read(entry)
        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError("Scientific diagnostic report failed its integrity check")
        report = json.loads(content)
        if not isinstance(report, dict) or report.get("id") != identifier:
            raise ValueError("Portable scientific report ID does not match its reference")
        reports.append(report)
    document[field] = reports


def attachment(content: bytes, filename: str, media: str) -> Response:
    return Response(
        content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def parse_csv(path: Path, name: str) -> tuple[Sample, np.ndarray]:
    from .models import Channel

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        names = next(reader, [])
        if not names or any(not n.strip() for n in names) or len(names) > 512:
            raise ValueError("CSV requires a nonempty header with at most 512 channel names")
        names = [n.strip() for n in names]
        rows = []
        for row_number, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(names):
                raise ValueError(f"CSV row {row_number}: expected {len(names)} values")
            try:
                rows.append([float(v) for v in row])
            except ValueError as exc:
                raise ValueError(f"CSV row {row_number} contains a nonnumeric value") from exc
            if len(rows) * len(names) > 150_000_000:
                raise ValueError("CSV exceeds the current 150-million-value import limit")
    values = np.array(rows, dtype=float).reshape(-1, len(names))
    return Sample(
        name=name, event_count=len(values), channels=[Channel(name=n) for n in names], source="CSV"
    ), values


def build_table(doc, engine, group_id=None, channel=None):
    selected = None
    if group_id:
        group = next((g for g in doc.groups if g.id == group_id), None)
        if not group:
            raise KeyError("Group not found")
        selected = set(group.sample_ids)
    rows = []
    gate_map = {g.id: g for g in doc.gates}

    def gate_path(gate):
        if not gate.parent_id:
            return gate.name
        return gate_path(gate_map[gate.parent_id]) + " / " + gate.name

    for sample in doc.samples:
        if selected is not None and sample.id not in selected:
            continue
        root = {
            "id": None,
            "count": sample.event_count,
            "percent_parent": 100.0 if sample.event_count else None,
            "percent_total": 100.0 if sample.event_count else None,
        }
        for row in [root] + engine.gate_counts(doc, sample):
            result = {
                "sample": sample.name,
                "sample_id": sample.id,
                "population": gate_path(gate_map[row["id"]]) if row["id"] else "All events",
                "gate_id": row["id"],
                "count": row["count"],
                "percent_parent": row["percent_parent"],
                "percent_total": row["percent_total"],
                **{f"metadata:{key}": value for key, value in sample.tags.items()},
            }
            if channel:
                if channel in {c.name for c in sample.channels}:
                    result.update(
                        {
                            k: v
                            for k, v in engine.summary(doc, sample, row["id"], channel).items()
                            if k not in {"count", "channel"}
                        }
                    )
                else:
                    result["channel_missing"] = True
            rows.append(result)
    return rows
