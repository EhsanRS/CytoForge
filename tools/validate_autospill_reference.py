"""Generate independent AutoSpill golden results using pinned author R code.

Run after sourcing tools/env.sh. R is a validation dependency only, extracted
under .cache/references/r-runtime; production ships neither R nor these packages.
Inputs are synthetic and do not establish biological or closed-source FlowJo parity.
"""

import hashlib
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PIN = "1e60e86337b297f1dd9ffec6701d8010dd06175b"
CYTOLIB_PIN = "1f886d99b51c520b7adf19d1cc570bbfdc61c315"
REFERENCE = ROOT / ".cache/references/autospill"
RUNTIME = ROOT / ".cache/references/r-runtime"
WORK = ROOT / ".tmp/autospill-reference"
FIXTURES = ROOT / "tests/fixtures/autospill"
SOURCE_FILES = [
    "fit_robust_linear_model.r",
    "get_marker_spillover.r",
    "get_compensation_error.r",
    "refine_spillover.r",
    "do_gate.r",
    "get_autospill_param_minimal.r",
]
BIEX_SPECS = {
    "biex256": [256, 4.418539922, 0, -100, 262144],
    "biex4096": [4096, 4.418539922, 0, -100, 262144],
    "biex_negative": [256, 5, 2, -1000, 1048576],
    "biex_width_clamp": [256, 4.418539922, 0, -10000, 262144],
}


def native_tables():
    """Compile the reference's own methods only in the ignored validation cache.

    cytolib is AGPL-3.0. Its downloaded source and executable are validation
    dependencies, never part of the app, installer or checked-in implementation.
    """
    hashes = {}
    for filename in ("src/transformation.cpp", "LICENSE"):
        data = urllib.request.urlopen(
            f"https://raw.githubusercontent.com/RGLab/cytolib/{CYTOLIB_PIN}/{filename}",
            timeout=30,
        ).read()
        (REFERENCE / ("native_" + filename.replace("/", "_"))).write_bytes(data)
        hashes[filename] = hashlib.sha256(data).hexdigest()
    source = (REFERENCE / "native_src_transformation.cpp").read_text()

    def method(signature):
        begin = source.index(signature)
        opened = source.index("{", begin)
        depth = 1
        end = opened + 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        return source[begin:end]

    # Stubs expose only the fields used by the original two lookup methods.
    harness = r"""
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
using namespace std;
using EVENT_DATA_TYPE = double;
struct Table {
 vector<double> x,y;
 void setCaltype(string) {} void setMethod(int) {}
 void setX(vector<double> v) {x=v;} void setY(vector<double> v) {y=v;}
};
struct biexpTrans {
 int channelRange; double pos,neg,widthBasis,maxValue; bool isComputed; Table calTbl;
 double logRoot(double b, double w); void computCalTbl();
};
"""
    harness += method("EVENT_DATA_TYPE biexpTrans::logRoot")
    harness += method("void biexpTrans::computCalTbl()")
    harness += r"""
int main(int argc, char **argv) {
 if (argc != 6) return 2;
 biexpTrans t; t.channelRange=atoi(argv[1]); t.pos=atof(argv[2]);
 t.neg=atof(argv[3]); t.widthBasis=atof(argv[4]); t.maxValue=atof(argv[5]);
 t.computCalTbl(); cout << setprecision(17) << "input,output\n";
 for (size_t i=0;i<t.calTbl.x.size();i++) cout<<t.calTbl.x[i]<<","<<t.calTbl.y[i]<<"\n";
}
"""
    driver = WORK / "native_biex.cpp"
    driver.write_text(harness)
    binary = WORK / "native_biex"
    subprocess.run(["g++", "-O2", str(driver), "-o", str(binary)], check=True)
    for name, spec in BIEX_SPECS.items():
        output = subprocess.check_output([str(binary), *map(str, spec)])
        (WORK / f"{name}.csv").write_bytes(output)
    return hashes


# Thin data adapters let the original numerical functions operate on matrices.
# grDevices::chull supplies the convex hull in place of tripack's hull, without
# changing density estimation, tessellations, regressions or refinement.
R_DRIVER = r"""
suppressPackageStartupMessages({
    library(MASS); library(parallel); library(RColorBrewer)
    library(deldir); library(fields); library(sp)
})
args <- commandArgs(TRUE)
ref <- args[1]; work <- args[2]; case <- args[3]
for (file in c("fit_robust_linear_model.r", "get_marker_spillover.r",
    "get_compensation_error.r", "refine_spillover.r", "do_gate.r",
    "get_autospill_param_minimal.r")) source(file.path(ref, "R", file))
asp <- get.autospill.param.minimal()
asp$worker.process.n <- 1; asp$verbose <- FALSE
get.worker.process <- function(...) 1
check.critical <- function(ok, message) if (!all(ok)) stop(message)
skewness <- function(x) mean((x - mean(x))^3) / mean((x - mean(x))^2)^1.5
compensation <- function(matrix) matrix
compensate <- function(data, matrix) {
    result <- data %*% solve(matrix); dimnames(result) <- dimnames(data); result
}
transformList <- function(names, functions) functions
transform <- function(data, functions) {
    for (name in names(functions)) data[,name] <- functions[[name]](data[,name])
    data
}
get.flow.expression.data <- function(sets, control) do.call(rbind, sets)
tri.mesh <- function(x, y) list(x=x, y=y)
convex.hull <- function(mesh) {
    idx <- chull(mesh$x, mesh$y); list(x=mesh$x[idx], y=mesh$y[idx])
}
names <- c("D1", "D2", "AF")
for (name in c("biex256", "biex4096", "biex_negative", "biex_width_clamp")) {
    lut <- as.matrix(read.csv(file.path(work, paste0(name,".csv"))))
    forward <- splinefun(lut[,1], lut[,2], method="natural")
    backward <- splinefun(lut[,2], lut[,1], method="natural")
    span <- diff(range(lut[,1]))
    raw <- seq(min(lut[,1])-.3*span,max(lut[,1])+.3*span,length.out=120)
    coord <- seq(-50,max(lut[,2])+50,length.out=120)
    write.csv(data.frame(raw=raw,transformed=forward(raw),coord=coord,
        untransformed=backward(coord)),file.path(work,paste0(name,"_spline.csv")),row.names=FALSE)
}
lut <- as.matrix(read.csv(file.path(work, "biex256.csv")))
forward <- splinefun(lut[,1], lut[,2], method="natural")
backward <- splinefun(lut[,2], lut[,1], method="natural")
fc <- list(marker=names, marker.original=names, marker.n=3, sample=names,
    expr.data.min=0, expr.data.max=262144, figure.scatter.dir=NULL)
fc$transform <- setNames(rep(list(forward),3),names)
fc$transform.inv <- setNames(rep(list(backward),3),names)
flow.gate <- list(); sets <- list()
for (i in 1:3) {
    data <- as.matrix(read.csv(file.path(work, paste0(case,"_",i,".csv")),check.names=FALSE))
    rownames(data) <- paste0(names[i],"_",1:nrow(data))
    idx <- seq_len(nrow(data))
    if (case == "cleanup") idx <- do.gate(data[,c("FSC-A","SSC-A")],
        asp$default.gate.param, names[i], fc, asp)
    write.csv(data.frame(event_id=idx-1),file.path(work,paste0(case,"_gate_",i,".csv")),row.names=FALSE)
    sets[[names[i]]] <- data[,names]; flow.gate[[names[i]]] <- idx
}
fc$flow.set <- sets
fc$event.sample <- rep(names, vapply(sets,nrow,integer(1)))
fc$expr.data.untr <- do.call(rbind,sets)
fc$expr.data.tran <- do.call(rbind,lapply(sets,transform,fc$transform))
initial <- get.marker.spillover(TRUE,flow.gate,fc,asp)
result <- refine.spillover(initial,NULL,flow.gate,fc,asp)
write.csv(initial$coef,file.path(work,paste0(case,"_initial.csv")))
write.csv(result$spillover,file.path(work,paste0(case,"_matrix.csv")))
write.csv(result$error$slop-diag(3),file.path(work,paste0(case,"_residual.csv")))
write.csv(result$convergence,file.path(work,paste0(case,"_convergence.csv")),row.names=FALSE)
cat("R",getRversion() |> as.character(),"MASS",as.character(packageVersion("MASS")),"\n")
"""


def inputs():
    rng = np.random.default_rng(912204)
    matrices = {
        "tails": np.array([[1, 0.2, 0.03], [0.1, 1, 0.12], [0.02, 0.08, 1]]),
        "af": np.array([[1, 0.2, 0.04], [0.12, 1, 0.1], [0.4, 0.3, 1]]),
        "cleanup": np.array([[1, 0.18, 0.05], [0.09, 1, 0.1], [0.03, 0.07, 1]]),
        "af_independent": np.array([[1, 0.18, 0.04], [0.09, 1, 0.1], [0.35, 0.2, 1]]),
    }
    arrays = {}
    for case, matrix in matrices.items():
        for primary in range(3):
            n = 1800 if case == "cleanup" else 1600
            true = np.zeros((n, 3))
            true[:, primary] = rng.lognormal(np.log(4500), 0.8, n)
            if case == "af":
                true[:, 2] = rng.lognormal(np.log(350), 0.7, n)
                if primary != 2:
                    true[:, primary] += 1.4 * true[:, 2]
            noise = rng.normal(0, 12, (n, 3))
            values = true @ matrix + [70, 35, 25] + noise
            if case == "af_independent":
                stain, af = np.meshgrid(np.linspace(1000, 15000, 101), np.linspace(100, 2000, 31))
                true = np.zeros((stain.size, 3))
                true[:, 2] = af.ravel()
                if primary < 2:
                    true[:, primary] = stain.ravel()
                values = true @ matrix + [70, 35, 25]
            if case == "tails":
                outliers = rng.choice(n, 28, replace=False)
                values[outliers, (primary + 1) % 3] += rng.uniform(1200, 6000, len(outliers))
            channels = ["D1", "D2", "AF"]
            if case == "cleanup":
                # Dense corner debris and a separate doublet cluster.
                scatter = np.column_stack([rng.normal(60000, 5000, n), rng.normal(28000, 2500, n)])
                debris = np.column_stack([rng.normal(1200, 160, 500), rng.normal(700, 100, 500)])
                doublets = np.column_stack(
                    [rng.normal(120000, 4000, 100), rng.normal(65000, 3000, 100)]
                )
                contaminant = rng.uniform(300, 1200, (600, 3))
                values = np.column_stack(
                    [np.vstack([values, contaminant]), np.vstack([scatter, debris, doublets])]
                )
                channels += ["FSC-A", "SSC-A"]
            arrays[f"{case}_{primary}"] = values
            np.savetxt(
                WORK / f"{case}_{primary + 1}.csv",
                values,
                delimiter=",",
                header=",".join(channels),
                comments="",
            )
    return arrays, {key: matrix.tolist() for key, matrix in matrices.items()}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (REFERENCE / "R").mkdir(parents=True, exist_ok=True)
    source_hashes = {}
    for name in SOURCE_FILES:
        url = f"https://raw.githubusercontent.com/carlosproca/autospill/{PIN}/R/{name}"
        content = urllib.request.urlopen(url, timeout=30).read()
        (REFERENCE / "R" / name).write_bytes(content)
        source_hashes[name] = hashlib.sha256(content).hexdigest()
    env = dict(os.environ, **json.loads((RUNTIME / "environment.json").read_text()))
    driver = WORK / "reference.r"
    driver.write_text(R_DRIVER)
    native_hashes = native_tables()
    arrays, physical = inputs()
    truth = {}
    logs = []
    for case in physical:
        completed = subprocess.run(
            [
                str(RUNTIME / "root/usr/lib/R/bin/exec/R"),
                "--vanilla",
                "--slave",
                "-f",
                str(driver),
                "--args",
                str(REFERENCE),
                str(WORK),
                case,
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        logs.append(f"{case}\n{completed.stdout}\n{completed.stderr}")
        (ROOT / "artifacts/autospill-reference.log").write_text("\n".join(logs))
        if completed.returncode:
            raise RuntimeError(logs[-1])
        result = {
            key: np.loadtxt(
                WORK / f"{case}_{key}.csv", delimiter=",", skiprows=1, usecols=[1, 2, 3]
            ).tolist()
            for key in ("matrix", "initial", "residual")
        }
        for primary in range(3):
            ids = np.loadtxt(
                WORK / f"{case}_gate_{primary + 1}.csv",
                delimiter=",",
                skiprows=1,
                dtype=np.int64,
                ndmin=1,
            )
            arrays[f"{case}_gate_{primary}"] = ids
        result["convergence"] = np.genfromtxt(
            WORK / f"{case}_convergence.csv",
            delimiter=",",
            names=True,
            dtype=None,
            encoding="utf-8",
        ).tolist()
        result["physical_matrix"] = physical[case]
        truth[case] = result
        print(
            case,
            "R iterations",
            len(result["convergence"]),
            "max residual",
            float(np.max(np.abs(result["residual"]))),
        )
    for name in BIEX_SPECS:
        arrays[name + "_lookup"] = np.loadtxt(WORK / f"{name}.csv", delimiter=",", skiprows=1)
        arrays[name + "_spline"] = np.loadtxt(
            WORK / f"{name}_spline.csv", delimiter=",", skiprows=1
        )
    np.savez_compressed(FIXTURES / "controls.npz", **arrays)
    (FIXTURES / "truth.json").write_text(
        json.dumps(
            {
                "repository": "https://github.com/carlosproca/autospill",
                "commit": PIN,
                "source_sha256": source_hashes,
                "native_transform": {
                    "repository": "https://github.com/RGLab/cytolib",
                    "commit": CYTOLIB_PIN,
                    "source_sha256": native_hashes,
                    "interpolation": "R stats::splinefun natural; linear extrapolation",
                    "specs": BIEX_SPECS,
                },
                "runtime": logs[-1].splitlines()[1],
                "seed": 912204,
                "cases": truth,
            },
            indent=2,
        )
    )
    print("Wrote independent R fixtures", FIXTURES)


if __name__ == "__main__":
    main()
