"""Import progress, cooperative cancellation and recovery around one workspace commit."""

from __future__ import annotations

import copy
import json
import re
import threading
import time

from .analysis import atomic_json
from .imports import ImportCancelled
from .models import new_id
from .store import ConflictError, Store, now

ACTIVE = {"ready", "receiving", "reading", "committing"}
ID = re.compile(r"^[0-9a-f]{32}$")
MAX_FILES = 128
MAX_UPLOAD_BYTES = 1024**3
MAX_BATCH_BYTES = 4 * 1024**3


class ImportSessions:
    def __init__(self, store: Store):
        self.store = store
        self.root = store.root / "imports"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        self.written: dict[str, float] = {}
        self.closed = False
        for directory in self.root.iterdir():
            if not directory.is_dir() or not ID.fullmatch(directory.name):
                continue
            try:
                record = json.loads((directory / "state.json").read_text())
                if record["id"] != directory.name or not ID.fullmatch(record["workspace_id"]):
                    continue
                if record["status"] in ACTIVE:
                    referenced = self.store.referenced_samples(record["workspace_id"])
                    outputs = set(record.get("outputs", []))
                    committed = bool(outputs) and outputs <= referenced
                    record.update(
                        status="succeeded" if committed else "interrupted",
                        imported=len(outputs) if committed else 0,
                        stage=(
                            "Import completed before the engine stopped"
                            if committed
                            else "Import interrupted; select the files again to retry"
                        ),
                        finished_at=now(),
                    )
                    self._persist(record)
                self._clean(record)
                self.records[record["id"]] = record
                self._trim()
            except (OSError, ValueError, KeyError, TypeError):
                # Damaged records do not authorize deleting immutable event files.
                continue

    def _trim(self):
        while len(self.records) > 256:
            oldest = next(
                (key for key, record in self.records.items() if record["status"] not in ACTIVE),
                None,
            )
            if oldest is None:
                break
            self.records.pop(oldest)
            self.written.pop(oldest, None)

    def _persist(self, record):
        atomic_json(self.root / record["id"] / "state.json", record)
        self.written[record["id"]] = time.monotonic()

    def _record(self, identifier, workspace_id):
        if not ID.fullmatch(identifier):
            raise KeyError("Import session not found")
        record = self.records.get(identifier)
        if record is None:
            try:
                record = json.loads((self.root / identifier / "state.json").read_text())
            except (OSError, ValueError) as exc:
                raise KeyError("Import session not found") from exc
            self.records[identifier] = record
        if record["workspace_id"] != workspace_id:
            raise KeyError("Import session not found in this workspace")
        return record

    @staticmethod
    def public(record):
        return copy.deepcopy({key: value for key, value in record.items() if key != "outputs"})

    def create(self, workspace_id: str, revision: int) -> dict:
        with self.lock:
            if self.closed:
                raise ConflictError("The analysis engine is stopping")
            workspace = self.store.get(workspace_id)
            if workspace.revision != revision:
                raise ConflictError("Workspace changed. Reload before starting the import.")
            if sum(record["status"] in ACTIVE for record in self.records.values()) >= 4:
                raise ConflictError("Four imports are already active. Finish or cancel one first.")
            identifier = new_id()
            (self.root / identifier).mkdir()
            record = {
                "id": identifier,
                "workspace_id": workspace_id,
                "revision": revision,
                "status": "ready",
                "stage": "Preparing files",
                "created_at": now(),
                "finished_at": None,
                "cancel_requested": False,
                "file": "",
                "file_index": 0,
                "file_count": 0,
                "bytes_received": 0,
                "bytes_total": 0,
                "dataset_index": 0,
                "events_read": 0,
                "event_total": 0,
                "imported": 0,
                "errors": [],
                "warnings": [],
                "datasets": [],
                "outputs": [],
            }
            self.records[identifier] = record
            self._persist(record)
            self._trim()
            return self.public(record)

    def get(self, workspace_id: str, identifier: str) -> dict:
        with self.lock:
            return self.public(self._record(identifier, workspace_id))

    def begin(self, workspace_id: str, identifier: str, revision: int, file_count: int, total: int):
        with self.lock:
            record = self._record(identifier, workspace_id)
            if record["revision"] != revision:
                raise ConflictError("Import session revision does not match this request")
            if record["cancel_requested"] or self.closed:
                raise ImportCancelled()
            if record["status"] != "ready":
                raise ConflictError("This import session has already been used")
            if not 1 <= file_count <= MAX_FILES:
                record.update(
                    status="failed", stage="Too many files in this selection", finished_at=now()
                )
                self._persist(record)
                raise ValueError(f"Choose between 1 and {MAX_FILES} files in one import")
            if total > MAX_BATCH_BYTES:
                record.update(
                    status="failed", stage="Selection exceeds the transfer limit", finished_at=now()
                )
                self._persist(record)
                raise ValueError("Import batch exceeds the current 4 GiB transfer limit")
            record.update(
                status="receiving",
                stage="Receiving files",
                file_count=file_count,
                bytes_total=total,
            )
            self._persist(record)

    def update(self, workspace_id: str, identifier: str, **changes):
        with self.lock:
            record = self._record(identifier, workspace_id)
            previous = record["stage"], record["file_index"], record["dataset_index"]
            record.update(changes)
            # Event progress is cheap in memory; persist at most four times a second.
            if (
                previous != (record["stage"], record["file_index"], record["dataset_index"])
                or time.monotonic() - self.written.get(identifier, 0) >= 0.25
            ):
                self._persist(record)

    def check(self, workspace_id: str, identifier: str):
        with self.lock:
            record = self._record(identifier, workspace_id)
            if self.closed or record["cancel_requested"]:
                raise ImportCancelled()

    def cancel(self, workspace_id: str, identifier: str) -> dict:
        with self.lock:
            record = self._record(identifier, workspace_id)
            if record["status"] == "committing":
                raise ConflictError("Import is saving. Wait for it to finish, then use Undo.")
            if record["status"] not in ACTIVE:
                return self.public(record)
            record.update(cancel_requested=True, stage="Stopping import")
            if record["status"] == "ready":
                record.update(status="cancelled", imported=0, finished_at=now())
            self._persist(record)
            return self.public(record)

    def committing(self, workspace_id: str, identifier: str, outputs: list[str]):
        with self.lock:
            self.check(workspace_id, identifier)
            record = self._record(identifier, workspace_id)
            record.update(status="committing", stage="Saving samples", outputs=outputs)
            # The complete move intent is durable before any array enters the event store.
            self._persist(record)

    def finish(self, workspace_id: str, identifier: str, status: str, **changes):
        with self.lock:
            record = self._record(identifier, workspace_id)
            record.update(status=status, finished_at=now(), **changes)
            self._persist(record)

    def _clean(self, record):
        directory = self.root / record["id"]
        for path in directory.iterdir():
            if path.name != "state.json" and path.is_file():
                path.unlink(missing_ok=True)
        if record["status"] != "succeeded" and record.get("outputs"):
            referenced = self.store.referenced_samples(record["workspace_id"])
            for identifier in record["outputs"]:
                if ID.fullmatch(identifier) and identifier not in referenced:
                    self.store.data_path(record["workspace_id"], identifier).unlink(missing_ok=True)
                    self.store.origins_path(record["workspace_id"], identifier).unlink(
                        missing_ok=True
                    )

    def clean(self, workspace_id: str, identifier: str):
        with self.lock:
            self._clean(self._record(identifier, workspace_id))

    def close(self):
        with self.lock:
            self.closed = True
            for record in self.records.values():
                if record["status"] == "ready":
                    record.update(
                        status="interrupted",
                        stage="Engine stopped before receiving the files",
                        finished_at=now(),
                    )
                    self._persist(record)
