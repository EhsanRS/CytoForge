import io

import numpy as np
import pytest
from cytoforge.formulas import evaluate, parse
from cytoforge.models import Channel, DerivedParameter, Workspace


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os")',
        'ch("X").__class__',
        '[v for v in ch("X")]',
        'open("x")',
        'ch("X")[0]',
        "lambda: 1",
        "min(1)",
        "True",
        "ch(1)",
    ],
)
def test_formula_rejects_code_and_unrecognized_syntax(expression):
    with pytest.raises(ValueError):
        parse(expression)


def test_vectorized_expression_missing_values_and_constant():
    values = {"X": np.array([2, 6, 10]), "Y": np.array([1, 0, -2])}
    result = evaluate('ch("X") / ch("Y")', values.__getitem__, 3)
    np.testing.assert_allclose(result, [2, np.nan, -5])
    np.testing.assert_allclose(
        evaluate('clip(abs(ch("X")) * 2, 0, 15)', values.__getitem__, 3), [4, 12, 15]
    )
    assert evaluate("42", values.__getitem__, 3).tolist() == [42] * 3


def test_derived_channel_gating_and_compensated_export(client):
    doc = client.post("/api/workspaces", json={"name": "Derived"}).json()
    doc = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[("files", ("ratios.csv", b"X,Y\n2,1\n6,0\n10,-2\n", "text/csv"))],
    ).json()["workspace"]
    sample = doc["samples"][0]
    response = client.post(
        f"/api/workspaces/{doc['id']}/derived",
        json={
            "revision": doc["revision"],
            "sample_ids": [sample["id"]],
            "parameter": {"name": "Ratio", "expression": 'ch("X") / ch("Y")'},
        },
    )
    assert response.status_code == 200, response.text
    doc = response.json()
    summary = client.get(
        f"/api/workspaces/{doc['id']}/samples/{sample['id']}/statistics?channel=Ratio"
    ).json()
    assert summary["count"] == 3 and summary["finite_count"] == 2 and summary["median"] == -1.5
    plot = client.get(f"/api/workspaces/{doc['id']}/samples/{sample['id']}/plot?x=Ratio").json()
    assert plot["finite_count"] == 2
    exported = client.get(
        f"/api/workspaces/{doc['id']}/samples/{sample['id']}/export?format=csv&compensated=false"
    )
    assert exported.text.splitlines()[0] == "X,Y,Ratio"
    raw = np.loadtxt(io.StringIO(exported.text), delimiter=",", skiprows=1)
    np.testing.assert_allclose(raw[:, -1], [2, np.nan, -5])
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("derived.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    assert (
        client.get(
            f"/api/workspaces/{copy['id']}/samples/{sample['id']}/statistics?channel=Ratio"
        ).json()["median"]
        == -1.5
    )


def test_derived_dependencies_and_cycles(dataset):
    doc, sample, _, _ = dataset
    sample.channels += [Channel(name="Ratio"), Channel(name="Second")]
    sample.derived_parameters = [
        DerivedParameter(name="Ratio", expression='ch("X") / max(ch("Y"), 1)'),
        DerivedParameter(name="Second", expression='ch("Ratio") * 2'),
    ]
    Workspace.model_validate(doc.model_dump())
    sample.derived_parameters[0].expression = 'ch("Second")'
    with pytest.raises(ValueError, match="cycle"):
        Workspace.model_validate(doc.model_dump())
