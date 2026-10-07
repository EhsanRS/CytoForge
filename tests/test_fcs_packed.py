"""Literal bitstreams and independent integer truth for bounded packed imports."""

from __future__ import annotations

import hashlib
import io
import threading

import flowio
import numpy as np
import pytest
from cytoforge import event_exports, imports
from cytoforge.imports import ImportCancelled, fcs_blocks, read_fcs_header
from cytoforge.models import Gate, Transform
from cytoforge.science import Engine
from test_imports import create, stream, upload

from tools.import_fixture import fcs_chain, fcs_dataset


@pytest.mark.parametrize("version", ["2.0", "3.0", "3.1"])
@pytest.mark.parametrize("chunk_values", [2, 4, 128])
def test_literal_stream_crosses_events_chunks_and_ignores_final_unused_bits(
    tmp_path, monkeypatch, version, chunk_values
):
    monkeypatch.setattr(imports, "CHUNK_VALUES", chunk_values)
    # Low-to-high bits: 101|10, 110|01, 111|00. The final high bit is unused.
    content = fcs_dataset(
        [[5, 2], [6, 1], [7, 0]],
        ["X", "Y"],
        data_type="I",
        widths=[3, 2],
        version=version,
        raw_data=b"\xd5\x9d",
    )
    result = stream(tmp_path, content)[0]
    np.testing.assert_array_equal(np.load(result.path), [[5, 2], [6, 1], [7, 0]])
    assert result.sample.event_count == 3
    assert result.sample.metadata["p1b"] == "3"
    assert result.warnings == []


def test_literal_byte_aligned_events_with_non_byte_aligned_parameters(tmp_path):
    result = stream(
        tmp_path,
        fcs_dataset(
            [[5, 17], [2, 3]], ["X", "Y"], data_type="I", widths=[3, 5], raw_data=b"\x8d\x1a"
        ),
    )[0]
    np.testing.assert_array_equal(np.load(result.path), [[5, 17], [2, 3]])


@pytest.mark.parametrize("width", range(1, 65))
def test_every_bit_width_with_mixed_fields_all_offsets_masks_and_exact_values(
    tmp_path, monkeypatch, width
):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 21)
    # Vary the first field width to reach every possible bit offset, including
    # unaligned uint64 fields that occupy nine input octets.
    for prefix in range(1, 9):
        usable = min(width, 53)
        mask = (1 << usable) - 1
        high_bits = ((1 << width) - 1) ^ mask
        values = [
            [row % (1 << prefix), high_bits | ((row * 17) & mask), row % 32] for row in range(19)
        ]
        result = stream(
            tmp_path,
            fcs_dataset(
                values,
                ["Prefix", "Value", "Suffix"],
                data_type="I",
                widths=[prefix, width, 5],
                metadata={"P2R": str(1 << usable)},
            ),
        )[0]
        expected = [[row % (1 << prefix), (row * 17) & mask, row % 32] for row in range(19)]
        np.testing.assert_array_equal(np.load(result.path), expected)
        result.path.unlink()


def test_full_uint64_mask_then_precision_guard_with_nine_octet_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 3)
    result = stream(
        tmp_path,
        fcs_dataset(
            [[127, (1 << 63) | (1 << 53), 1], [1, (1 << 63) | 17, 0]],
            ["Prefix", "Value", "Suffix"],
            data_type="I",
            widths=[7, 64, 1],
            metadata={"P2R": str((1 << 53) + 1)},
        ),
    )[0]
    # $PnR is not a power of two: the next higher power is 2^54. The top stored
    # bit is ignored and the exactly representable 2^53 endpoint is retained.
    np.testing.assert_array_equal(np.load(result.path), [[127, 1 << 53, 1], [1, 17, 0]])


@pytest.mark.parametrize("value", [(1 << 53) + 1, (1 << 64) - 1])
def test_precision_failure_removes_pending_and_completed_datasets(tmp_path, value):
    good = fcs_dataset([[5]], ["Flag"], data_type="I", widths=[3])
    bad = fcs_dataset([[1, value]], ["Flag", "Value"], data_type="I", widths=[1, 64])
    with pytest.raises(ValueError, match="exact float64"):
        stream(tmp_path, fcs_chain([good, bad]))
    assert not list((tmp_path / "prepared").iterdir())


def test_preprocess_gain_log_time_and_non_power_of_two_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 3)
    result = stream(
        tmp_path,
        fcs_dataset(
            [[255, 1023, 9], [129, 0, 7]],
            ["Linear", "Log", "Time"],
            data_type="I",
            widths=[9, 10, 4],
            metadata={
                "P1R": "100",
                "P1G": "2",
                "P2R": "1024",
                "P2E": "4,0",
                "P2G": "2",
                "P3G": "999",
                "TIMESTEP": "0.5",
            },
        ),
    )[0]
    np.testing.assert_allclose(
        np.load(result.path),
        [[63.5, 10 ** (4 * 1023 / 1024) / 2, 4.5], [0.5, 0.5, 3.5]],
        rtol=1e-15,
    )
    assert result.sample.channels[0].range == 50
    assert result.sample.channels[1].range == 5000
    assert result.sample.channels[2].range == 8
    assert len(result.warnings) == 1 and "logarithmic zero corrected" in result.warnings[0]


@pytest.mark.parametrize("width", [0, -1, 65])
def test_invalid_widths_reject_before_event_storage(tmp_path, width):
    with pytest.raises(ValueError, match="widths of 1–64"):
        stream(
            tmp_path,
            fcs_dataset([[1]], ["X"], data_type="I", widths=[3], metadata={"P1B": str(width)}),
        )
    assert not list((tmp_path / "prepared").iterdir())


@pytest.mark.parametrize("payload", [b"\xd5", b"\xd5\x1d\x00"])
def test_packed_lengths_are_continuous_not_padded_per_event(tmp_path, payload):
    with pytest.raises(ValueError, match="DATA length"):
        stream(
            tmp_path,
            fcs_dataset(
                [[5, 2], [6, 1], [7, 0]], ["X", "Y"], data_type="I", widths=[3, 2], raw_data=payload
            ),
        )
    assert not list((tmp_path / "prepared").iterdir())


def test_unverified_big_endian_packing_has_explicit_error(tmp_path):
    with pytest.raises(ValueError, match="Big-endian bit-packed.*verified instrument layout"):
        stream(
            tmp_path,
            fcs_dataset([[1]], ["X"], data_type="I", widths=[3], order=">", raw_data=b"\x01"),
        )
    assert not list((tmp_path / "prepared").iterdir())


def test_zero_event_packed_acquisition(tmp_path):
    result = stream(tmp_path, fcs_dataset([], ["X", "Y"], data_type="I", widths=[3, 7]))[0]
    assert np.load(result.path).shape == (0, 2)
    assert [channel.name for channel in result.sample.channels] == ["X", "Y"]


def test_chunk_carry_reads_every_byte_once_for_scientific_data_hashes(monkeypatch):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 4)
    values = [[row % 8, row % 4] for row in range(31)]
    content = fcs_dataset(values, ["X", "Y"], data_type="I", widths=[3, 2])
    handle = io.BytesIO(content)
    header = read_fcs_header(handle, 0, len(content))
    reader = event_exports.HashReader(handle)
    blocks = list(fcs_blocks(reader, header))
    np.testing.assert_array_equal(np.concatenate(blocks), values)
    assert all(block.size <= 4 for block in blocks)
    raw = content[header.data_start : header.data_stop + 1]
    assert reader.digest.hexdigest() == hashlib.sha256(raw).hexdigest()
    assert handle.tell() == header.data_stop + 1


def test_reads_and_output_stay_bounded_when_many_events_cross_chunk_boundaries(monkeypatch):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 131)
    values = [[row % 8, row % 1024, row % 8192] for row in range(20003)]
    content = fcs_dataset(values, ["X", "Y", "Z"], data_type="I", widths=[3, 10, 13])

    class RecordingReader(io.BytesIO):
        sizes = []

        def read(self, count=-1):
            self.sizes.append(count)
            return super().read(count)

    handle = RecordingReader(content)
    header = read_fcs_header(handle, 0, len(content))
    handle.sizes.clear()
    decoded = []
    for block in fcs_blocks(handle, header):
        assert block.size <= 131
        decoded.extend(block.tolist())
    assert decoded == values
    assert min(handle.sizes) >= 0
    assert max(handle.sizes) <= ((131 // 3) * 26 + 14) // 8
    assert sum(handle.sizes) == header.data_stop - header.data_start + 1


def test_short_read_after_header_validation_is_not_silent():
    content = fcs_dataset([[5, 2], [6, 1], [7, 0]], ["X", "Y"], data_type="I", widths=[3, 2])
    handle = io.BytesIO(content)
    header = read_fcs_header(handle, 0, len(content))
    truncated = io.BytesIO(content[:-1])
    with pytest.raises(ValueError, match="DATA is truncated"):
        list(fcs_blocks(truncated, header))


def test_cancellation_between_packed_chunks_removes_all_staged_files(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "CHUNK_VALUES", 9)
    stop = threading.Event()
    observed = []

    def progress(**state):
        if state.get("stage") == "Reading events":
            observed.append(state["events_read"])
            if state["events_read"] >= 6:
                stop.set()

    def check():
        if stop.is_set():
            raise ImportCancelled()

    good = fcs_dataset([[1]], ["First"], data_type="I", widths=[3])
    many = fcs_dataset(
        [[i % 8, i, 1] for i in range(20)], ["X", "Y", "Flag"], data_type="I", widths=[3, 7, 1]
    )
    with pytest.raises(ImportCancelled):
        stream(tmp_path, fcs_chain([good, many]), progress=progress, check=check)
    assert observed == [1, 3, 6]
    assert not list((tmp_path / "prepared").iterdir())


def test_packed_api_gating_double_export_project_roundtrip_and_duplicates(client, tmp_path):
    content = fcs_dataset(
        [[row % 8, row * 17, row] for row in range(31)],
        ["X", "Y", "Time"],
        data_type="I",
        widths=[3, 10, 5],
        metadata={"TIMESTEP": "0.25", "P2G": "2"},
    )
    result = upload(client, create(client), [("packed.fcs", content)])
    assert result["imported"] == 1 and result["errors"] == []
    doc = result["workspace"]
    sample_id = doc["samples"][0]["id"]
    gate = Gate(
        sample_id=sample_id,
        name="Known packed population",
        kind="range",
        x="X",
        bounds=[2, 5],
        x_transform=Transform(kind="linear"),
    )
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates",
        json={"revision": doc["revision"], "gate": gate.model_dump()},
    )
    assert response.status_code == 200, response.text
    doc = response.json()
    store = client.app.state.store
    engine = Engine(store)
    workspace = store.get(doc["id"])
    sample = workspace.samples[0]
    expected = np.array([[row % 8, row * 8.5, row * 0.25] for row in range(31)])
    np.testing.assert_array_equal(engine.raw(workspace, sample), expected)
    np.testing.assert_array_equal(
        np.flatnonzero(engine.mask(workspace, sample, gate.id)),
        [2, 3, 4, 10, 11, 12, 18, 19, 20, 26, 27, 28],
    )
    export, _ = event_exports.write_export(
        store,
        engine,
        workspace,
        event_exports.Request(revision=workspace.revision, sample_id=sample_id, values="raw"),
        tmp_path / "export",
    )
    # FlowIO can read the lossless double export even though it cannot decode packed input.
    np.testing.assert_array_equal(
        np.asarray(flowio.FlowData(export).events).reshape(-1, 3), expected
    )
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    restored = client.post(
        "/api/import/project", files={"file": ("packed.cytoforge", archive.content)}
    )
    assert restored.status_code == 200, restored.text
    reopened = store.get(restored.json()["id"])
    np.testing.assert_array_equal(Engine(store).raw(reopened, reopened.samples[0]), expected)
    assert reopened.samples[0].metadata["p1b"] == "3"
    assert reopened.gates[0].id == gate.id
    duplicate = upload(client, restored.json(), [("renamed.fcs", content)])
    assert duplicate["imported"] == 0
    assert duplicate["datasets"][0]["skipped"] is True
