"""Generate an independent base-R oracle for the published AutoSpread equations.

R is a validation dependency in .cache only. No production numerical functions,
author implementation or closed-source FlowJo binary are used by this oracle.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
from scipy.special import ndtri

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp/autospread-reference"
FIXTURES = ROOT / "tests/fixtures/autospread"
RUNTIME = ROOT / ".cache/references/r-runtime"
R_DRIVER = r"""
args <- commandArgs(trailingOnly=TRUE)
index <- read.csv(file.path(args[1], "index.csv"), stringsAsFactors=FALSE)
fit.rows <- list(); bin.rows <- list()
signed.root <- function(x) sign(x) * (sqrt(abs(x) + 1) - 1)
for (k in seq_len(nrow(index))) {
  entry <- index[k,]
  raw <- as.matrix(read.csv(file.path(args[1], entry$file), header=FALSE))
  S <- as.matrix(read.csv(file.path(args[1], entry$matrix), header=FALSE))
  bg <- scan(text=entry$background, quiet=TRUE)
  weights <- scan(text=entry$weights, quiet=TRUE)
  if (entry$kind == "spillover") {
    values <- raw %*% solve(S)
  } else {
    # Independent normal equations; production uses a weighted pseudoinverse.
    values <- sweep(raw, 2, bg) %*% diag(weights) %*% t(S) %*%
      solve(S %*% diag(weights) %*% t(S))
  }
  N <- nrow(values); Q <- min(entry$quantiles, N %/% entry$events_per_bin)
  sizes <- c(rep(N %/% Q + 1, N %% Q), rep(N %/% Q, Q - N %% Q))
  ends <- cumsum(sizes); starts <- c(1, head(ends, -1) + 1)
  order.ids <- order(values[,entry$primary], method="radix")
  bins <- lapply(seq_len(Q), function(q) order.ids[starts[q]:ends[q]])
  F <- sapply(bins, function(ids) median(values[ids,entry$primary]))
  x <- signed.root(F)
  for (j in seq_len(ncol(values))) {
    if (j == entry$primary) next
    med <- sapply(bins, function(ids) median(values[ids,j]))
    sigma <- sapply(bins, function(ids)
      unname(quantile(values[ids,j], .84, type=7)) - median(values[ids,j]))
    first <- lm(sigma ~ x); a <- coef(first)[1]; b <- coef(first)[2]
    adjusted <- signed.root(sigma^2 - a^2)
    second <- lm(adjusted ~ x - 1); second.s <- summary(second)
    slope <- unname(coef(second)[1])
    stat <- unname(second.s$fstatistic[1])
    p <- if (!is.finite(stat)) if (slope == 0) 1 else 0 else pf(stat, 1, Q-1, lower.tail=FALSE)
    status <- if (slope <= 0) "negative" else if (p >= .05) "not_significant" else "positive"
    fit.rows[[length(fit.rows)+1]] <- data.frame(case=entry$case, control=entry$control,
      secondary=j-1, baseline=unname(a), initial_slope=unname(b), final_slope=slope,
      p_value=p, r_squared=if (is.na(second.s$r.squared)) 0 else unname(second.s$r.squared),
      coefficient=if (status == "positive") slope else 0, status=status)
    bin.rows[[length(bin.rows)+1]] <- data.frame(case=entry$case, control=entry$control,
      secondary=j-1, bin=seq_len(Q)-1, count=sizes, primary=F, median=med,
      robust_sd=sigma, adjusted_sd=adjusted)
  }
}
options(digits=17)
write.table(do.call(rbind,fit.rows), file.path(args[1],"fits.csv"), sep=",",
  row.names=FALSE, col.names=TRUE)
write.table(do.call(rbind,bin.rows), file.path(args[1],"bins.csv"), sep=",",
  row.names=FALSE, col.names=TRUE)
writeLines(R.version.string, file.path(args[1], "r-version.txt"))
"""


def cases():
    z = ndtri((np.arange(128) + 0.5) / 128)
    z = (z - np.median(z)) / (np.percentile(z, 84) - np.median(z))
    conventional = np.array([[1, 0.22], [0.13, 1]])
    spectral = np.array(
        [[1, 0.2, 0.1, 0.4, 0.05], [0.1, 1, 0.3, 0.2, 0.12], [0.3, 0.2, 0.5, 1, 0.6]]
    )
    out = {}
    for name in (
        "baseline",
        "signed",
        "constant_noise",
        "negative",
        "nonsignificant",
        "adaptive",
        "spectral",
    ):
        S = (
            spectral
            if name == "spectral"
            else np.eye(2)
            if name in ("constant_noise", "adaptive")
            else conventional
        )
        levels = np.linspace(-25 if name == "signed" else 0, 40000, 32 if name == "signed" else 64)
        if name == "adaptive":
            levels = np.linspace(0, 40000, 17)
        samples = []
        for primary in range(len(S)):
            parts = []
            for b, level in enumerate(levels):
                zz = z[:83] if name == "adaptive" else z
                latent = np.zeros((len(zz), len(S)))
                latent[:, primary] = level
                for secondary in range(len(S)):
                    if secondary == primary:
                        continue
                    if name == "constant_noise":
                        sigma = 0.0
                    elif name == "negative":
                        sigma = 150 - 0.4 * np.sqrt(level + 1)
                    elif name == "nonsignificant":
                        sigma = 50 + (b % 2) * 30
                    else:
                        sigma = 30 + (0.3 + 0.15 * secondary) * np.sign(level) * (
                            np.sqrt(abs(level) + 1) - 1
                        )
                    latent[:, secondary] = sigma * np.roll(zz, 13 * secondary)
                raw = latent @ S
                if name == "spectral":
                    raw += np.array([2, 3, -4, 5, 7])
                    raw[:, 4] += 0.1 * zz
                parts.append(raw)
            samples.append(np.vstack(parts))
        out[name] = dict(
            matrix=S.tolist(),
            kind="spectral" if name == "spectral" else "spillover",
            background=[2, 3, -4, 5, 7] if name == "spectral" else [0, 0],
            weights=[1, 2, 0.5, 3, 0.75] if name == "spectral" else [1, 1],
            quantiles=32 if name == "signed" else 256 if name == "adaptive" else 64,
            events_per_bin=100,
            samples=samples,
        )
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    inputs, arrays, entries, truth = cases(), {}, [], {}
    for name, case in inputs.items():
        matrix_file = f"{name}-matrix.csv"
        np.savetxt(WORK / matrix_file, case["matrix"], delimiter=",", fmt="%.17g")
        truth[name] = {k: v for k, v in case.items() if k != "samples"}
        truth[name]["controls"] = []
        for i, values in enumerate(case["samples"]):
            key, filename = f"{name}_{i}", f"{name}-{i}.csv"
            arrays[key] = values
            np.savetxt(WORK / filename, values, delimiter=",", fmt="%.17g")
            entries.append(
                dict(
                    case=name,
                    control=i,
                    file=filename,
                    matrix=matrix_file,
                    kind=case["kind"],
                    background=" ".join(map(str, case["background"])),
                    weights=" ".join(map(str, case["weights"])),
                    primary=i + 1,
                    quantiles=case["quantiles"],
                    events_per_bin=case["events_per_bin"],
                )
            )
            truth[name]["controls"].append(dict(primary=i, pairs=[]))
    with (WORK / "index.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(entries[0]))
        writer.writeheader()
        writer.writerows(entries)
    driver = WORK / "oracle.r"
    driver.write_text(R_DRIVER)
    env = dict(os.environ, **json.loads((RUNTIME / "environment.json").read_text()))
    run = subprocess.run(
        [
            str(RUNTIME / "root/usr/lib/R/bin/exec/R"),
            "--vanilla",
            "--slave",
            "-f",
            str(driver),
            "--args",
            str(WORK),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    (ROOT / "artifacts/autospread-reference.log").write_text(run.stdout + run.stderr)
    with (WORK / "fits.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            pair = {
                k: float(row[k])
                for k in (
                    "baseline",
                    "initial_slope",
                    "final_slope",
                    "p_value",
                    "r_squared",
                    "coefficient",
                )
            }
            # Exact zero secondary noise is a degenerate lm summary, with NaN R2.
            if not np.isfinite(pair["r_squared"]):
                pair["r_squared"] = 0.0
            pair.update(secondary=int(row["secondary"]), status=row["status"], bins=[])
            truth[row["case"]]["controls"][int(row["control"])]["pairs"].append(pair)
    with (WORK / "bins.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            control = truth[row["case"]]["controls"][int(row["control"])]
            pair = next(p for p in control["pairs"] if p["secondary"] == int(row["secondary"]))
            pair["bins"].append(
                {
                    k: float(row[k])
                    for k in ("count", "primary", "median", "robust_sd", "adjusted_sd")
                }
            )
    np.savez_compressed(FIXTURES / "controls.npz", **arrays)
    (FIXTURES / "truth.json").write_text(json.dumps(truth, indent=2, allow_nan=False) + "\n")
    metadata = dict(
        reference="https://doi.org/10.1038/s41467-021-23126-8",
        section="Sec26",
        equations=[11, 12],
        oracle="Independent implementation in base R, not FlowJo",
        r_version=(WORK / "r-version.txt").read_text().strip(),
        paper_sha256=hashlib.sha256(
            (ROOT / ".cache/references/autospill/paper.xml").read_bytes()
        ).hexdigest(),
        driver_sha256=hashlib.sha256(R_DRIVER.encode()).hexdigest(),
        biological_validation=False,
        closed_source_parity_verified=False,
        fixture_sha256={
            name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            for name in ("controls.npz", "truth.json")
        },
    )
    (FIXTURES / "reference.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                status="independent_R_oracle_generated",
                cases=len(truth),
                controls=sum(len(c["controls"]) for c in truth.values()),
                **metadata,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
