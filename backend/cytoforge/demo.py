"""Deterministic synthetic PBMC acquisition, clearly labeled throughout the UI."""

import numpy as np

from .models import Channel, Compensation, Gate, Group, Sample, Transform, Workspace
from .science import save_events
from .store import Store


def create_demo(store: Store) -> Workspace:
    rng = np.random.default_rng(20261002)
    linear, logicle = Transform(), Transform(kind="logicle")
    channels = [
        Channel(name="FSC-A", label="Forward scatter", transform=linear),
        Channel(name="SSC-A", label="Side scatter", transform=linear),
        Channel(name="FSC-H", label="Singlets", transform=linear),
        Channel(name="BV510-A", label="Live / Dead", transform=logicle),
        Channel(name="FITC-A", label="CD3", transform=logicle),
        Channel(name="PE-A", label="CD4", transform=logicle),
        Channel(name="APC-A", label="CD8", transform=logicle),
        Channel(name="BV421-A", label="CD19", transform=logicle),
        Channel(name="PE-Cy7-A", label="CD69", transform=logicle),
        Channel(name="Time", label="Acquisition time (s)", transform=linear, range=360),
    ]
    detectors = [c.name for c in channels[3:9]]
    spillover = np.eye(6)
    spillover[1, 2] = 0.10
    spillover[2, 1] = 0.04
    spillover[2, 5] = 0.12
    spillover[4, 0] = 0.06
    comp = Compensation(
        name="PBMC reference panel",
        detectors=detectors,
        matrix=spillover.tolist(),
        source="Synthetic acquisition",
    )
    workspace = Workspace(
        name="PBMC · T-cell activation",
        description="Synthetic demonstration · 6 donors, paired conditions",
        compensations=[comp],
    )
    resting, activated = [], []
    for donor in range(1, 7):
        for condition in ["Unstimulated", "Stimulated"]:
            n = int(rng.integers(42000, 62000))
            classes = rng.choice(5, size=n, p=[0.42, 0.24, 0.16, 0.10, 0.08])
            # 0 CD4 T, 1 CD8 T, 2 B, 3 monocyte, 4 debris.
            fsc_means = np.array([105000, 98000, 88000, 165000, 26000])
            ssc_means = np.array([42000, 45000, 38000, 115000, 12000])
            values = np.zeros((n, 10))
            values[:, 0] = rng.normal(fsc_means[classes], 13000)
            values[:, 1] = rng.normal(ssc_means[classes], 8500)
            doublets = rng.random(n) < 0.055
            values[:, 2] = values[:, 0] * rng.normal(0.93, 0.035, n)
            values[doublets, 2] *= 0.52
            fluorescence = rng.normal(90, 170, (n, 6))
            dead = rng.random(n) < 0.075
            fluorescence[dead, 0] += rng.lognormal(9.3, 0.38, dead.sum())
            is_t = classes < 2
            fluorescence[is_t, 1] += rng.lognormal(9.8, 0.36, is_t.sum())
            fluorescence[classes == 0, 2] += rng.lognormal(9.4, 0.40, (classes == 0).sum())
            fluorescence[classes == 1, 3] += rng.lognormal(9.7, 0.38, (classes == 1).sum())
            fluorescence[classes == 2, 4] += rng.lognormal(10.1, 0.35, (classes == 2).sum())
            activation_probability = 0.04 if condition == "Unstimulated" else 0.48 + donor * 0.035
            cd69 = is_t & (rng.random(n) < activation_probability)
            fluorescence[cd69, 5] += rng.lognormal(9.0, 0.48, cd69.sum())
            values[:, 3:9] = fluorescence @ spillover
            values[:, 9] = np.sort(rng.uniform(0, 300, n))
            sample = Sample(
                name=f"D{donor:02d}_{'CTRL' if condition == 'Unstimulated' else 'STIM'}.fcs",
                event_count=n,
                channels=channels,
                compensation_id=comp.id,
                source="Synthetic demo",
                tags={
                    "Donor": f"D{donor:02d}",
                    "Condition": condition,
                    "Well": f"{'A' if condition == 'Unstimulated' else 'B'}{donor:02d}",
                },
                metadata={
                    "cyt": "Synthetic spectral cytometer",
                    "date": "02-OCT-2026",
                    "project": "PBMC T-cell activation",
                    "synthetic": "true",
                },
            )
            sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
            workspace.samples.append(sample)
            (resting if condition == "Unstimulated" else activated).append(sample.id)
            lymphocytes = Gate(
                sample_id=sample.id,
                name="Lymphocytes",
                kind="polygon",
                x="FSC-A",
                y="SSC-A",
                vertices=[(42000, 18000), (148000, 18000), (150000, 76000), (58000, 82000)],
                color="#7ba8f8",
            )
            singlets = Gate(
                sample_id=sample.id,
                parent_id=lymphocytes.id,
                name="Singlets",
                kind="polygon",
                x="FSC-A",
                y="FSC-H",
                vertices=[(40000, 30000), (155000, 131000), (155000, 156000), (40000, 48000)],
                color="#b89df8",
            )
            live = Gate(
                sample_id=sample.id,
                parent_id=singlets.id,
                name="Live cells",
                kind="range",
                x="BV510-A",
                bounds=[-1e10, 1800],
                color="#38d9ba",
            )
            tcells = Gate(
                sample_id=sample.id,
                parent_id=live.id,
                name="CD3+ T cells",
                kind="range",
                x="FITC-A",
                bounds=[2200, 1e10],
                color="#38d9ba",
            )
            cd4 = Gate(
                sample_id=sample.id,
                parent_id=tcells.id,
                name="CD4+",
                kind="rectangle",
                x="PE-A",
                y="APC-A",
                bounds=[2200, 1e10, -1e10, 2200],
                color="#f0b96a",
            )
            cd8 = Gate(
                sample_id=sample.id,
                parent_id=tcells.id,
                name="CD8+",
                kind="rectangle",
                x="PE-A",
                y="APC-A",
                bounds=[-1e10, 2200, 2200, 1e10],
                color="#ec8ab0",
            )
            activated_gate = Gate(
                sample_id=sample.id,
                parent_id=tcells.id,
                name="CD69+ activated",
                kind="range",
                x="PE-Cy7-A",
                bounds=[1800, 1e10],
                color="#c6ee86",
            )
            workspace.gates.extend([lymphocytes, singlets, live, tcells, cd4, cd8, activated_gate])
    workspace.groups = [
        Group(name="Unstimulated", sample_ids=resting, color="#7ba8f8"),
        Group(name="Stimulated", sample_ids=activated, color="#38d9ba"),
    ]
    return store.create(Workspace.model_validate(workspace.model_dump()))
