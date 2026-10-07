"""One reviewed, undoable gate transaction over the original members of a virtual group."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .models import Gate, Workspace
from .partitions import replace_gate
from .report_sources import SourceAudit
from .science import Engine
from .store import ConflictError
from .virtual_groups import PooledEngine, Scope


class Request(Scope):
    revision: int = Field(ge=0)
    action: Literal["create", "edit", "delete"]
    gates: list[Gate] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self):
        if len({g.id for g in self.gates}) != len(self.gates):
            raise ValueError("Review each proposed gate only once")
        if any(g.sample_id != self.anchor_id for g in self.gates):
            raise ValueError("Proposed gates must belong to the representative sample")
        return self


class Apply(Request):
    review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def identifier(gate_id, sample_id, role="gate"):
    return hashlib.sha256(f"{role}:{gate_id}:{sample_id}".encode()).hexdigest()[:32]


def gate_parameters(gate):
    names = {n for n in (gate.x, gate.y) if n}
    for dimension in gate.dimensions:
        names.update(dimension.ratio_channels or [dimension.channel])
    return names


def plan(doc, base, request):
    if doc.revision != request.revision:
        raise ConflictError("Workspace changed. Review the group gates again before applying.")
    pooled = PooledEngine(doc, base, request.anchor_id, request.group_id, request.sample_filter)
    if request.action == "delete":
        return delete_plan(doc, base, pooled, request)
    pooled.validate(request.channels, [request.gate_id])
    candidate = doc.model_copy(deep=True)
    rows = []
    all_names, dependencies = set(), set()
    old = {g.id: g for g in doc.gates}
    for proposed in request.gates:
        if proposed.kind == "quality" or proposed.quality_id:
            raise ValueError("QC populations need their own per-sample fitted models")
        if request.action == "edit" and proposed.id not in old:
            raise ValueError("The population to edit no longer exists")
        if request.action == "create" and proposed.id in old:
            raise ValueError("The new population ID already exists")
        all_names.update(gate_parameters(proposed))
        dependencies.update([proposed.parent_id, *proposed.operands])
        if request.action == "edit" and proposed.partition != old[proposed.id].partition:
            raise ValueError("Linked partition membership cannot be changed in a pooled edit")
    pooled.validate(all_names, dependencies)

    for sample in pooled.members:
        mapped = {
            key: dict(pooled.mapping(key))[sample.id] for key in dependencies | {request.gate_id}
        }
        ids = []
        for proposed in request.gates:
            original = old.get(proposed.id)
            target_id = (
                dict(pooled.mapping(proposed.id))[sample.id]
                if request.action == "edit"
                else proposed.id
                if sample.id == request.anchor_id
                else identifier(proposed.id, sample.id)
            )
            gate = proposed.model_copy(
                deep=True,
                update={
                    "id": target_id,
                    "sample_id": sample.id,
                    "parent_id": mapped[proposed.parent_id],
                    "operands": [mapped[key] for key in proposed.operands],
                },
            )
            if gate.partition:
                if original:
                    target = old[target_id]
                    if (
                        not target.partition
                        or target.partition.kind != gate.partition.kind
                        or target.partition.member != gate.partition.member
                    ):
                        raise ValueError(
                            f"{sample.name}: linked partition memberships do not match"
                        )
                    gate.partition = target.partition.model_copy(deep=True)
                elif sample.id != request.anchor_id:
                    gate.partition.id = identifier(gate.partition.id, sample.id, "partition")
            elif original and old[target_id].partition:
                raise ValueError("Linked partition membership cannot be removed in a pooled edit")
            provenance = dict(gate.provenance)
            provenance["virtual_group"] = dict(
                group_id=request.group_id,
                anchor_id=request.anchor_id,
                member_ids=[s.id for s in pooled.members],
                review_revision=doc.revision,
                magnetic_position="per_sample" if gate.magnetic else None,
            )
            gate.provenance = provenance
            candidate.gates = replace_gate(candidate.gates, gate, create=request.action == "create")
            if request.action == "create" and gate.partition:
                for member in candidate.gates:
                    if (
                        member.partition
                        and member.partition.id == gate.partition.id
                        and member.id != target_id
                    ):
                        member.id = identifier(
                            gate.partition.id,
                            sample.id,
                            f"partition-member-{member.partition.member}",
                        )
            ids.extend(
                g.id
                for g in candidate.gates
                if g.id == target_id
                or gate.partition
                and g.partition
                and g.partition.id == gate.partition.id
            )
        rows.append(
            dict(
                sample_id=sample.id,
                sample_name=sample.name,
                compensation_id=sample.compensation_id,
                gate_ids=list(dict.fromkeys(ids)),
            )
        )

    candidate = Workspace.model_validate(candidate.model_dump())
    # Candidate gates share the revision with saved gates, so they get a separate cache.
    preview_engine = Engine(base.store, cache_bytes=64 * 1024 * 1024)
    new_gates = {g.id: g for g in candidate.gates}
    for row in rows:
        sample = preview_engine.sample(candidate, row["sample_id"])
        row["populations"] = [
            dict(
                id=key,
                name=new_gates[key].name,
                count=int(np.count_nonzero(preview_engine.mask(candidate, sample, key))),
                parent_count=int(
                    np.count_nonzero(
                        preview_engine.mask(candidate, sample, new_gates[key].parent_id)
                    )
                ),
            )
            for key in row.pop("gate_ids")
        ]
    before, after = SourceAudit(doc), SourceAudit(candidate)
    affected = [
        dict(id=key, name=item[0].request.name)
        for key, item in before.models.items()
        if not before.stale(key) and after.stale(key)
    ]
    preview = dict(
        revision=doc.revision,
        group_name=pooled.name,
        action=request.action,
        samples=rows,
        affected_models=affected,
        population_count=sum(len(row["populations"]) for row in rows),
        changed=candidate.gates != doc.gates,
    )
    content = dict(
        workspace_sha256=hashlib.sha256(doc.model_dump_json().encode()).hexdigest(),
        request=request.model_dump(exclude={"review_hash"}),
        preview=preview,
    )
    preview["review_hash"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return candidate, preview


def apply(doc, base, request):
    candidate, preview = plan(doc, base, request)
    if preview["review_hash"] != request.review_hash:
        raise ConflictError("The reviewed group gates changed. Review the proposal again.")
    if not preview["changed"]:
        raise ValueError("The proposed gates already match the group")
    doc.gates = candidate.gates


def delete_plan(doc, base, pooled, request):
    originals = {g.id: g for g in doc.gates}
    remove = set()
    for proposed in request.gates:
        if proposed.id not in originals or proposed != originals[proposed.id]:
            raise ValueError("Review the current saved population before removing it")
        remove.update(identifier for _, identifier in pooled.mapping(proposed.id))
    while True:
        families = {g.partition.id for g in doc.gates if g.id in remove and g.partition}
        dependent = {
            g.id
            for g in doc.gates
            if g.parent_id in remove
            or set(g.operands) & remove
            or g.partition
            and g.partition.id in families
        }
        if dependent <= remove:
            break
        remove |= dependent
    candidate = doc.model_copy(deep=True)
    candidate.gates = [g for g in candidate.gates if g.id not in remove]
    candidate = Workspace.model_validate(candidate.model_dump())
    before, after = SourceAudit(doc), SourceAudit(candidate)
    rows = []
    for sample in pooled.members:
        populations = []
        for gate in doc.gates:
            if gate.id not in remove or gate.sample_id != sample.id:
                continue
            row = dict(id=gate.id, name=gate.name, count=None, parent_count=None)
            if before.population_stale(gate.id):
                row["error"] = "Stale fitted population; its current count is unavailable"
            else:
                row.update(
                    count=int(np.count_nonzero(base.mask(doc, sample, gate.id))),
                    parent_count=int(np.count_nonzero(base.mask(doc, sample, gate.parent_id))),
                )
            populations.append(row)
        rows.append(
            dict(
                sample_id=sample.id,
                sample_name=sample.name,
                compensation_id=sample.compensation_id,
                populations=populations,
            )
        )
    preview = dict(
        revision=doc.revision,
        group_name=pooled.name,
        action="delete",
        samples=rows,
        affected_models=[
            dict(id=key, name=item[0].request.name)
            for key, item in before.models.items()
            if not before.stale(key) and after.stale(key)
        ],
        population_count=len(remove),
        changed=bool(remove),
    )
    content = dict(
        workspace_sha256=hashlib.sha256(doc.model_dump_json().encode()).hexdigest(),
        request=request.model_dump(exclude={"review_hash"}),
        preview=preview,
    )
    preview["review_hash"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return candidate, preview
