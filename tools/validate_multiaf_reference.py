"""Independent R oracle and latent truth for four sources including two AF spectra.

Uses the retained rectangular R equations and original MIT Huber fit. AF span
angles use base R QR projection, independently of the production SVD. This does
not validate biological AF classes or automatic extraction of their references.
"""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
from validate_spectral_autospill_reference import DRIVER, REFERENCE, RUNTIME, digest

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp/multiaf-reference"
FIXTURES = ROOT / "tests/fixtures/multiaf"
AF_REVIEW = r"""
directions <- sweep(U,2,weights,"*")
directions <- directions/sqrt(rowSums(directions^2))
af.indices <- scan(file.path(work,"af_indices.txt"),quiet=TRUE) + 1
af.review <- matrix(0,length(af.indices),4)
for (k in seq_along(af.indices)) {
    i <- af.indices[k]
    rest <- directions[-i,,drop=FALSE]
    projection <- qr.fitted(qr(t(rest),tol=1e-12),directions[i,])
    fraction <- min(1,max(0,sqrt(sum((directions[i,]-projection)^2))))
    cosines <- rest %*% directions[i,]
    closest <- which.max(abs(cosines))
    af.review[k,] <- c(fraction,asin(fraction)*180/pi,cosines[closest],
                       seq_len(n)[-i][closest]-1)
}
write.csv(af.review,file.path(work,"af_review.csv"),row.names=FALSE)
"""


def inputs():
    spectra = np.array(
        [
            [1, 0.12, 0.28, 0.09, 0.03, 0.11, 0.18, 0.2],
            [1, 0.75, 0.15, 0.6, 0.04, 0.27, 0.14, 0.3],
            [0.04, 0.07, 0.18, 0.23, 1, 0.5, 0.11, 0.06],
            [0.2, 0.07, 0.1, 0.03, 0.12, 0.4, 1, 0.2],
        ]
    )
    background = np.array([33, -10, 55, 4, 19, 100, -50, 21])
    arrays, cases = {}, {}
    for case in ("exact", "independent_af", "correlated_af", "detector_noise"):
        rng = np.random.default_rng(819417)
        for index in range(4):
            latent = np.zeros((4096, 4))
            latent[:, index] = np.linspace(200, 32000, len(latent))
            rng.shuffle(latent[:, index])
            if case in {"independent_af", "correlated_af"}:
                for af in (2, 3):
                    if af != index:
                        latent[:, af] = rng.uniform(100, 800, len(latent))
                if case == "correlated_af" and index < 2:
                    latent[:, 3] += 0.15 * latent[:, index]
            raw = latent @ spectra + background
            if case == "detector_noise":
                raw += rng.normal(0, 2, raw.shape)
            arrays[f"{case}_{index}"] = raw
            arrays[f"{case}_{index}_latent"] = latent
        cases[case] = dict(
            detectors=[f"D{i + 1}" for i in range(8)],
            outputs=["Fluor1", "Fluor2", "AF1", "AF2"],
            peaks=[0, 0, 4, 6],
            af_indices=[2, 3],
            background=background.tolist(),
            weights=[1, 2, 0.3, 4, 0.8, 1.5, 2, 0.6],
            signature=spectra.tolist(),
            trim_fraction=0,
            biex="biex256",
        )
    detector_order = [7, 2, 0, 4, 1, 6, 3, 5]
    settings = cases["reordered_detectors"] = dict(cases["independent_af"])
    for field in ("detectors", "background", "weights"):
        settings[field] = [cases["independent_af"][field][j] for j in detector_order]
    settings["peaks"] = [detector_order.index(j) for j in cases["independent_af"]["peaks"]]
    settings["signature"] = spectra[:, detector_order].tolist()
    for i in range(4):
        arrays[f"reordered_detectors_{i}"] = arrays[f"independent_af_{i}"][:, detector_order]
        arrays[f"reordered_detectors_{i}_latent"] = arrays[f"independent_af_{i}_latent"]
    source_order = [3, 1, 2, 0]
    settings = cases["reordered_sources"] = dict(cases["independent_af"])
    settings["outputs"] = [cases["independent_af"]["outputs"][j] for j in source_order]
    settings["peaks"] = [cases["independent_af"]["peaks"][j] for j in source_order]
    settings["af_indices"] = [0, 2]
    settings["signature"] = spectra[source_order].tolist()
    for i, original in enumerate(source_order):
        arrays[f"reordered_sources_{i}"] = arrays[f"independent_af_{original}"]
        arrays[f"reordered_sources_{i}_latent"] = arrays[f"independent_af_{original}_latent"][
            :, source_order
        ]
    cases["biex4096"] = dict(cases["detector_noise"], biex="biex4096", trim_fraction=0.05)
    for i in range(4):
        arrays[f"biex4096_{i}"] = arrays[f"detector_noise_{i}"]
        arrays[f"biex4096_{i}_latent"] = arrays[f"detector_noise_{i}_latent"]
    return arrays, cases


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    driver = WORK / "reference.r"
    driver.write_text(DRIVER + AF_REVIEW)
    env = dict(os.environ, **json.loads((RUNTIME / "environment.json").read_text()))
    arrays, cases = inputs()
    reference, logs = {}, []
    with np.load(ROOT / "tests/fixtures/autospill/controls.npz", allow_pickle=False) as native:
        for name, settings in cases.items():
            for i in range(4):
                np.savetxt(
                    WORK / f"control_{i + 1}.csv",
                    arrays[f"{name}_{i}"],
                    delimiter=",",
                    header=",".join(settings["detectors"]),
                    comments="",
                )
            for field in ("peaks", "background", "weights", "af_indices"):
                np.savetxt(WORK / f"{field}.txt", settings[field])
            np.savetxt(
                WORK / "lookup.csv",
                native[settings["biex"] + "_lookup"],
                delimiter=",",
                header="raw,coordinate",
                comments="",
            )
            run = subprocess.run(
                [
                    str(RUNTIME / "root/usr/lib/R/bin/exec/R"),
                    "--vanilla",
                    "--slave",
                    "-f",
                    str(driver),
                    "--args",
                    str(WORK),
                    str(REFERENCE),
                    "4",
                    "8",
                    str(settings["trim_fraction"]),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            logs.append(f"{name}\n{run.stdout}\n{run.stderr}")
            (ROOT / "artifacts/multiaf-reference.log").write_text("\n".join(logs))
            if run.returncode:
                raise RuntimeError(
                    f"R reference failed for {name}; see artifacts/multiaf-reference.log"
                )
            reference[name] = {
                key: np.loadtxt(WORK / f"{key}.csv", delimiter=",", skiprows=1).tolist()
                for key in (
                    "initial",
                    "matrix",
                    "residual",
                    "convergence",
                    "reconstruction",
                    "reconstruction_rms",
                    "reconstruction_relative_rms",
                    "af_review",
                )
            }
            reference[name]["stop_reason"] = (WORK / "reason.txt").read_text().strip()
            print(name, reference[name]["stop_reason"], flush=True)
    np.savez_compressed(FIXTURES / "controls.npz", **arrays)
    (FIXTURES / "truth.json").write_text(json.dumps(cases, indent=2) + "\n")
    evidence = dict(
        status="reference_generated",
        scope=(
            "Synthetic four-source/eight-detector numerical validation; two AF references; "
            "no biological or FlowJo truth"
        ),
        reference_runtime=run.stdout.splitlines()[0].strip(),
        reference_commit="1e60e86337b297f1dd9ffec6701d8010dd06175b",
        original_huber_sha256=digest(REFERENCE / "R/fit_robust_linear_model.r"),
        retained_driver_sha256=digest(ROOT / "tools/validate_spectral_autospill_reference.py"),
        driver_sha256=digest(driver),
        generator_sha256=digest(Path(__file__)),
        controls_sha256=digest(FIXTURES / "controls.npz"),
        truth_sha256=digest(FIXTURES / "truth.json"),
        cases=reference,
    )
    (FIXTURES / "reference.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    (ROOT / "artifacts/multiaf-reference-validation.json").write_text(
        json.dumps(
            {k: v for k, v in evidence.items() if k != "cases"} | {"case_count": len(cases)},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
