from pathlib import Path

import numpy as np
import pytest
from cytoforge.app import create_app
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store


def pytest_addoption(parser):
    parser.addoption(
        "--in-process-asgi",
        action="store_true",
        help="Exercise HTTP contracts through HTTPX ASGI on the caller loop, with app lifespan",
    )


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "data")
    yield instance
    instance.close()


@pytest.fixture
def dataset(store):
    # Points on boundaries, zero/negative intensities, and one NaN event.
    values = np.array(
        [
            [-2, -2],
            [0, 0],
            [1, 1],
            [2, 2],
            [3, 0],
            [0, 3],
            [4, 4],
            [np.nan, 1],
        ],
        dtype=float,
    )
    sample = Sample(
        name="Boundary sample",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    doc = Workspace(name="Gate test", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    return doc, doc.samples[0], values, Engine(store)


@pytest.fixture
def client(tmp_path: Path, request):
    if request.config.getoption("--in-process-asgi"):
        from asgi_client import InProcessClient

        client_type = InProcessClient
    else:
        from fastapi.testclient import TestClient

        client_type = TestClient
    app = create_app(tmp_path / "api")
    with client_type(app, base_url="http://127.0.0.1") as instance:
        token = instance.get("/api/bootstrap").json()["token"]
        instance.headers["X-CytoForge-Token"] = token
        yield instance
