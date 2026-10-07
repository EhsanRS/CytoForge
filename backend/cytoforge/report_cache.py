"""Bounded publication-panel cache; page arrangement does not recompute plots."""

import copy
import hashlib
import json
import threading
from collections import OrderedDict

from . import report_svg


class PanelCache:
    def __init__(self, maximum_bytes=64 * 1024 * 1024):
        self.maximum_bytes = maximum_bytes
        self.bytes = 0
        self.items = OrderedDict()
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            value = self.items.get(key)
            if value is not None:
                self.items.move_to_end(key)
                return value[0], copy.deepcopy(value[1]), copy.deepcopy(value[2])

    def put(self, key, figure, provenance, issues):
        svg = report_svg.serialize(figure)
        size = len(svg.encode()) + len(json.dumps([provenance, issues], ensure_ascii=True).encode())
        if size > self.maximum_bytes:
            return svg, provenance, issues
        with self.lock:
            old = self.items.pop(key, None)
            if old:
                self.bytes -= old[3]
            while self.items and self.bytes + size > self.maximum_bytes:
                self.bytes -= self.items.popitem(last=False)[1][3]
            self.items[key] = (svg, copy.deepcopy(provenance), copy.deepcopy(issues), size)
            self.bytes += size
        return svg, provenance, issues


PANELS = PanelCache()


def workspace_key(workspace, engine):
    def stamp(path):
        try:
            stat = path.stat()
            return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except OSError:
            return str(path), None

    value = workspace.model_dump(exclude={"layouts", "revision", "created_at", "updated_at"})
    files = [stamp(engine.store.data_path(workspace.id, sample.id)) for sample in workspace.samples]
    for results in (
        workspace.analyses,
        workspace.cell_cycle_results,
        workspace.proliferation_results,
        workspace.kinetics_results,
    ):
        files.extend(
            stamp(engine.store.analysis_path(workspace.id, result.id, data.sample_id))
            for result in results
            for data in result.data
        )
    files.extend(
        stamp(engine.store.quality_path(workspace.id, result.id))
        for result in workspace.quality_results
    )
    files.extend(
        stamp(engine.store.comparison_path(workspace.id, result.id))
        for result in workspace.comparison_results
    )
    return hashlib.sha256(
        json.dumps([value, files], sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


def panel_key(scientific_key, element, iteration, layout):
    value = element.model_dump(
        exclude={"x_mm", "y_mm", "rotation", "group_id", "position_locked", "page", "opacity"}
    )
    return hashlib.sha256(
        json.dumps(
            [scientific_key, value, iteration, layout.batch.mode],
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
