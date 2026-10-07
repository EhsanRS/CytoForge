"""Independent R oracle for rectangular AutoSpill; never imports production code.

Uses the cached MIT-licensed author's Huber regression and native lookup fixtures.
Rectangular normalization, weighted least squares, and E @ U are implemented here
from Roca et al., Nature Communications 12, 2890 (2021), equations 2–10.
This is synthetic numerical validation, not FlowJo or biological validation.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp/spectral-autospill-reference"
FIXTURES = ROOT / "tests/fixtures/spectral_autospill"
REFERENCE = ROOT / ".cache/references/autospill"
RUNTIME = ROOT / ".cache/references/r-runtime"

DRIVER = r"""
suppressPackageStartupMessages(library(MASS))
args <- commandArgs(TRUE)
work <- args[1]; ref <- args[2]; n <- as.integer(args[3]); d <- as.integer(args[4])
source(file.path(ref, "R", "fit_robust_linear_model.r"))
asp <- list(rlm.iter.max=100)
sets <- lapply(seq_len(n), function(i) as.matrix(read.csv(
    file.path(work,paste0("control_",i,".csv")),check.names=FALSE)))
peaks <- scan(file.path(work,"peaks.txt"),quiet=TRUE) + 1
background <- scan(file.path(work,"background.txt"),quiet=TRUE)
weights <- sqrt(scan(file.path(work,"weights.txt"),quiet=TRUE))
trim <- as.numeric(args[5])
lut <- as.matrix(read.csv(file.path(work,"lookup.csv")))
forward <- splinefun(lut[,1],lut[,2],method="natural")
backward <- splinefun(lut[,2],lut[,1],method="natural")
paired <- function(x,y) {
    m <- round(length(x)*trim)
    keep <- rep(TRUE,length(x))
    if (m > 0) for (axis in 1:2) {
        v <- if (axis == 1) x else y
        if (diff(range(v)) == 0) next
        limits <- sort(v)[c(m,length(v)-m+1)]
        k <- v > limits[1] & v < limits[2]
        if (axis == 2 && sum(keep & k) < 50) {
            inclusive <- v >= limits[1] & v <= limits[2]
            if (sum(keep & inclusive) >= 50) k <- inclusive
        }
        keep <- keep & k
    }
    if (sum(keep) < 50) stop("Too few trimmed regression events")
    list(x=x[keep],y=y[keep])
}
slope <- function(x,y,transformed=FALSE) {
    if (transformed) { x <- forward(x); y <- forward(y) }
    pair <- paired(x,y)
    fit <- fit.robust.linear.model(pair$x,pair$y,"primary","secondary",asp)
    coefficient <- fit[2,1]
    if (transformed) {
        ends <- range(pair$x)
        pred <- fit[1,1]+coefficient*ends
        coefficient <- if (pred[1] == pred[2]) 0 else
            diff(backward(pred))/diff(backward(ends))
    }
    as.numeric(coefficient)
}
S <- matrix(0,n,d)
for (i in seq_len(n)) for (j in seq_len(d))
    S[i,j] <- if (j == peaks[i]) 1 else slope(sets[[i]][,peaks[i]],sets[[i]][,j])
write.csv(S,file.path(work,"initial.csv"),row.names=FALSE)
centered <- lapply(sets,function(y) sweep(y,2,background,"-"))
U <- S; transformed <- FALSE; damping <- 1
history <- rep(-1,10); previous <- -1; count <- 0; stable <- FALSE
trace <- list(); reason <- "iteration_limit"
for (iteration in 0:100) {
    U <- U / U[cbind(seq_len(n),peaks)]
    A <- sweep(U,2,weights,"*")
    # Base R weighted normal equations: independent of NumPy's SVD operator.
    operator <- sweep(t(A) %*% solve(A %*% t(A)),1,weights,"*")
    unmixed <- lapply(centered,function(y) y %*% operator)
    E <- matrix(0,n,n)
    for (i in seq_len(n)) for (j in seq_len(n))
        if (i != j) E[i,j] <- slope(unmixed[[i]][,i],unmixed[[i]][,j],transformed)
    maximum <- max(abs(E)); delta <- sd(c(E))
    history[iteration %% 10+1] <- if (previous >= 0) delta-previous else -1
    count <- count + as.integer(previous >= 0)
    change <- mean(history)
    trace[[length(trace)+1]] <- c(iteration=iteration,scale=as.numeric(transformed),
        damping=damping,error_sd=delta,max_error=maximum,error_change=change,
        condition_number=kappa(A,exact=TRUE))
    if (transformed && maximum < 1e-4) {
        if (stable) { reason <- "target_reached"; break }
        stable <- TRUE
    } else stable <- FALSE
    if (!transformed && maximum < 1e-2) {
        transformed <- TRUE; damping <- 1; stable <- maximum < 1e-4
        history <- rep(-1,10); previous <- -1; count <- 0
    } else {
        plateau <- count >= 10 && change > -1e-6
        if (plateau && damping == 1) {
            damping <- 0.1; history <- rep(-1,10); previous <- -1; count <- 0
        } else if (plateau) { reason <- "plateau"; break } else previous <- delta
    }
    if (iteration < 100) U <- U + damping*(E %*% U)
}
write.csv(U,file.path(work,"matrix.csv"),row.names=FALSE)
write.csv(E,file.path(work,"residual.csv"),row.names=FALSE)
write.csv(do.call(rbind,trace),file.path(work,"convergence.csv"),row.names=FALSE)
reconstruction <- matrix(0,n,d)
reconstruction.rms <- matrix(0,n,d)
relative.rms <- rep(0,n)
for (i in seq_len(n)) {
    residual <- centered[[i]]-unmixed[[i]] %*% U
    for (j in seq_len(d)) reconstruction[i,j] <- slope(centered[[i]][,peaks[i]],residual[,j])
    reconstruction.rms[i,] <- sqrt(colMeans(residual^2))
    relative.rms[i] <- sqrt(sum(residual^2)/sum(centered[[i]]^2))
}
write.csv(reconstruction,file.path(work,"reconstruction.csv"),row.names=FALSE)
write.csv(reconstruction.rms,file.path(work,"reconstruction_rms.csv"),row.names=FALSE)
write.csv(matrix(relative.rms,n,1),file.path(work,"reconstruction_relative_rms.csv"),row.names=FALSE)
writeLines(reason,file.path(work,"reason.txt"))
cat("R",as.character(getRversion()),"MASS",as.character(packageVersion("MASS")),"\n")
"""


def inputs():
    rng = np.random.default_rng(20261007)
    signature = np.array(
        [
            [1, 0.12, 0.28, 0.09, 0.03, 0.11],
            [1, 0.75, 0.15, 0.6, 0.04, 0.27],
            [0.04, 0.07, 0.18, 0.23, 1, 0.5],
        ]
    )
    background = np.array([33, -10, 55, 4, 19, 100.0])
    weights = np.array([1, 2, 0.3, 4, 0.8, 1.5])
    arrays, cases = {}, {}
    for case in (
        "independent_af",
        "correlated_af",
        "outliers",
        "detector_noise",
        "missing_component",
    ):
        sources, controls = [], []
        for i in range(3):
            latent = rng.normal(0, 5, (1600, 3))
            latent[:, i] = rng.uniform(200, 100000 if i < 2 else 8000, len(latent))
            if i < 2:
                latent[:, 2] = 100 + (
                    0.02 * latent[:, i]
                    if case == "correlated_af"
                    else rng.uniform(0, 1500, len(latent))
                )
            if case == "outliers" and i == 0:
                latent[::40, 1] += rng.uniform(2000, 10000, len(latent[::40]))
            raw = latent @ signature + background
            if case == "detector_noise":
                raw += rng.normal(0, [3, 10, 30, 5, 20, 6], raw.shape)
            if case == "missing_component" and i < 2:
                raw[:, 2] += 0.4 * latent[:, i] ** 2 / 100000
            sources.append(latent)
            controls.append(raw)
        cases[case] = dict(
            detectors=[f"D{i + 1}" for i in range(6)],
            outputs=["Fluor1", "Fluor2", "AF"],
            peaks=[0, 0, 4],
            background=background.tolist(),
            weights=weights.tolist(),
            signature=signature.tolist(),
            trim_fraction=0.01,
            biex="biex256",
        )
        for i in range(3):
            arrays[f"{case}_{i}"] = controls[i]
            arrays[f"{case}_latent_{i}"] = sources[i]
    permutation = [4, 2, 0, 5, 3, 1]
    cases["reordered_detectors"] = dict(cases["independent_af"])
    for field in ("detectors", "background", "weights"):
        cases["reordered_detectors"][field] = [
            cases["independent_af"][field][j] for j in permutation
        ]
    cases["reordered_detectors"]["peaks"] = [
        permutation.index(j) for j in cases["independent_af"]["peaks"]
    ]
    cases["reordered_detectors"]["signature"] = signature[:, permutation].tolist()
    for i in range(3):
        arrays[f"reordered_detectors_{i}"] = arrays[f"independent_af_{i}"][:, permutation]
        arrays[f"reordered_detectors_latent_{i}"] = arrays[f"independent_af_latent_{i}"]
    cases["biex4096"] = dict(cases["detector_noise"], biex="biex4096")
    for i in range(3):
        arrays[f"biex4096_{i}"] = arrays[f"detector_noise_{i}"]
        arrays[f"biex4096_latent_{i}"] = arrays[f"detector_noise_latent_{i}"]
    return arrays, cases


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    driver = WORK / "reference.r"
    driver.write_text(DRIVER)
    env = dict(os.environ, **json.loads((RUNTIME / "environment.json").read_text()))
    arrays, cases = inputs()
    reference, logs = {}, []
    with np.load(ROOT / "tests/fixtures/autospill/controls.npz", allow_pickle=False) as native:
        for name, settings in cases.items():
            for i in range(3):
                np.savetxt(
                    WORK / f"control_{i + 1}.csv",
                    arrays[f"{name}_{i}"],
                    delimiter=",",
                    header=",".join(settings["detectors"]),
                    comments="",
                )
            for field in ("peaks", "background", "weights"):
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
                    "3",
                    "6",
                    str(settings["trim_fraction"]),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            logs.append(f"{name}\n{run.stdout}\n{run.stderr}")
            (ROOT / "artifacts/spectral-autospill-reference.log").write_text("\n".join(logs))
            if run.returncode:
                raise RuntimeError(f"R reference failed for {name}; see the reference log")
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
                )
            }
            reference[name]["stop_reason"] = (WORK / "reason.txt").read_text().strip()
            print(name, reference[name]["stop_reason"], flush=True)
    np.savez_compressed(FIXTURES / "controls.npz", **arrays)
    (FIXTURES / "truth.json").write_text(json.dumps(cases, indent=2) + "\n")
    evidence = dict(
        status="reference_generated",
        scope=(
            "Independent base R rectangular adaptation of published equations; "
            "author Huber and native lookup validation only"
        ),
        reference_runtime=run.stdout.strip(),
        reference_commit="1e60e86337b297f1dd9ffec6701d8010dd06175b",
        author_regression_sha256=digest(REFERENCE / "R/fit_robust_linear_model.r"),
        paper_sha256=digest(REFERENCE / "paper.xml"),
        driver_sha256=digest(Path(__file__)),
        controls_sha256=digest(FIXTURES / "controls.npz"),
        truth_sha256=digest(FIXTURES / "truth.json"),
        cases=reference,
        biological_validation=False,
        flowjo_validation=False,
    )
    (FIXTURES / "reference.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    (ROOT / "artifacts/spectral-autospill-reference-validation.json").write_text(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "cases"}
            | {"case_count": len(cases)},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
