from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .models import Workspace


def now() -> str:
    return datetime.now(UTC).isoformat()


class ConflictError(Exception):
    pass


class Store:
    """Atomic SQLite snapshots, persistent undo/redo, and optimistic concurrency."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_dir = self.root / "events"
        self.events_dir.mkdir(exist_ok=True)
        self.analyses_dir = self.root / "analyses"
        self.analyses_dir.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "workspaces.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                cursor INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL, document TEXT NOT NULL, label TEXT NOT NULL,
                at TEXT NOT NULL, PRIMARY KEY(workspace_id, seq)
            );
            PRAGMA user_version=1;
        """)

    def close(self):
        with self.lock:
            self.db.close()

    def create(self, workspace: Workspace) -> Workspace:
        workspace = workspace.model_copy(deep=True)
        workspace.revision = 0
        workspace.created_at = workspace.updated_at = now()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO workspaces VALUES (?,?,?,?,?,?)",
                (workspace.id, workspace.name, 0, 0, workspace.created_at, workspace.updated_at),
            )
            self.db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?)",
                (workspace.id, 0, workspace.model_dump_json(), "Create workspace", now()),
            )
        return workspace

    def list(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT w.*, s.document FROM workspaces w JOIN snapshots s "
                "ON s.workspace_id=w.id AND s.seq=w.cursor ORDER BY w.updated_at DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "updated_at": row["updated_at"],
                "sample_count": len(json.loads(row["document"])["samples"]),
            }
            for row in rows
        ]

    def get(self, workspace_id: str) -> Workspace:
        with self.lock:
            row = self.db.execute(
                "SELECT s.document, w.revision, w.updated_at FROM workspaces w "
                "JOIN snapshots s ON s.workspace_id=w.id AND s.seq=w.cursor WHERE w.id=?",
                (workspace_id,),
            ).fetchone()
        if not row:
            raise KeyError("Workspace not found")
        doc = Workspace.model_validate_json(row["document"])
        doc.revision = row["revision"]
        doc.updated_at = row["updated_at"]
        return doc

    def revision(self, workspace_id: str) -> int:
        """Observe committed edits without deserializing a scientific snapshot."""
        with self.lock:
            row = self.db.execute(
                "SELECT revision FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Workspace not found")
        return row["revision"]

    def _check_revision(self, row, expected: int | None):
        if not row:
            raise KeyError("Workspace not found")
        if expected is not None and row["revision"] != expected:
            raise ConflictError("Workspace changed in another window. Reload and retry your edit.")

    def mutate(
        self,
        workspace_id: str,
        label: str,
        fn: Callable[[Workspace], None],
        expected: int | None = None,
    ) -> Workspace:
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            self._check_revision(row, expected)
            doc = self.get(workspace_id)
            fn(doc)
            doc.revision += 1
            doc.updated_at = now()
            doc = Workspace.model_validate(doc.model_dump())
            cursor = row["cursor"] + 1
            self.db.execute(
                "DELETE FROM snapshots WHERE workspace_id=? AND seq>?",
                (workspace_id, row["cursor"]),
            )
            self.db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?)",
                (workspace_id, cursor, doc.model_dump_json(), label, doc.updated_at),
            )
            self.db.execute(
                "UPDATE workspaces SET name=?, revision=?, cursor=?, updated_at=? WHERE id=?",
                (doc.name, doc.revision, cursor, doc.updated_at, workspace_id),
            )
        return doc

    def history(self, workspace_id: str) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT cursor FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if not row:
                raise KeyError("Workspace not found")
            history = self.db.execute(
                "SELECT seq, label, at FROM snapshots WHERE workspace_id=? ORDER BY seq",
                (workspace_id,),
            ).fetchall()
        return {
            "cursor": row["cursor"],
            "entries": [dict(v) for v in history],
            "can_undo": row["cursor"] > 0,
            "can_redo": row["cursor"] < history[-1]["seq"],
        }

    def move_history(self, workspace_id: str, direction: int, expected: int) -> Workspace:
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            self._check_revision(row, expected)
            cursor = row["cursor"] + direction
            target = self.db.execute(
                "SELECT document FROM snapshots WHERE workspace_id=? AND seq=?",
                (workspace_id, cursor),
            ).fetchone()
            if not target:
                raise ValueError("No further history in this direction")
            doc = Workspace.model_validate_json(target["document"])
            self.db.execute(
                "UPDATE workspaces SET name=?, revision=revision+1, cursor=?, updated_at=? "
                "WHERE id=?",
                (doc.name, cursor, now(), workspace_id),
            )
        return self.get(workspace_id)

    def data_path(self, workspace_id: str, sample_id: str) -> Path:
        # IDs are validated at the model/API boundary; also enforce at the filesystem boundary.
        for value in [workspace_id, sample_id]:
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid object ID")
        directory = self.events_dir / workspace_id
        directory.mkdir(exist_ok=True)
        return directory / f"{sample_id}.npy"

    def referenced_samples(self, workspace_id: str) -> set[str]:
        """Include undo/redo snapshots when recovering an interrupted file transaction."""
        with self.lock:
            referenced = set()
            for row in self.db.execute(
                "SELECT document FROM snapshots WHERE workspace_id=?", (workspace_id,)
            ):
                referenced.update(s["id"] for s in json.loads(row["document"])["samples"])
            return referenced

    def origins_path(self, workspace_id: str, sample_id: str) -> Path:
        return self.data_path(workspace_id, sample_id).with_suffix(".origins.npy")

    def referenced_memberships(self, workspace_id: str) -> set[str]:
        with self.lock:
            return {
                gate["membership"]["id"]
                for row in self.db.execute(
                    "SELECT document FROM snapshots WHERE workspace_id=?", (workspace_id,)
                )
                for gate in json.loads(row["document"])["gates"]
                if gate.get("membership")
            }

    def analysis_path(self, workspace_id: str, analysis_id: str, sample_id: str) -> Path:
        for value in [workspace_id, analysis_id, sample_id]:
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid analysis object ID")
        directory = self.analyses_dir / workspace_id / analysis_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{sample_id}.npy"

    def fitted_ids_path(self, workspace_id: str, analysis_id: str, sample_id: str) -> Path:
        return self.analysis_path(workspace_id, analysis_id, sample_id).with_name(
            f"{sample_id}.fit.npy"
        )

    def quality_path(self, workspace_id: str, quality_id: str) -> Path:
        for value in [workspace_id, quality_id]:
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid QC object ID")
        directory = self.root / "quality" / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{quality_id}.npy"

    def membership_path(self, workspace_id: str, membership_id: str) -> Path:
        for value in (workspace_id, membership_id):
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid population membership ID")
        directory = self.root / "memberships" / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{membership_id}.npy"

    def comparison_path(self, workspace_id: str, comparison_id: str) -> Path:
        for value in [workspace_id, comparison_id]:
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid comparison object ID")
        directory = self.root / "comparisons" / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{comparison_id}.npz"

    def interchange_path(self, workspace_id: str, record_id: str) -> Path:
        for value in [workspace_id, record_id]:
            if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Invalid interchange object ID")
        directory = self.root / "interchanges" / workspace_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{record_id}.xml"
