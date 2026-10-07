# Windows desktop trial

For Windows 10/11 x64 on Intel or AMD PCs, extract
`CytoForge-Windows-x64-trial.zip` completely into a writable folder and
double-click **Start-CytoForge.cmd** inside the extracted **CytoForge** folder.
First launch needs internet access and downloads the local desktop and analysis
dependencies. Later launches reuse them. Keep the folder after closing the app;
it contains your saved experiments and imported data.

The launcher opens the native Electron desktop app. Double-click a sample or
population, or click **New plot window**, to open an independent plot window.
Each plot has its own axes and view while sharing the experiment's saved gates.

Node.js and uv downloads are pinned and checked against the publishers' SHA256
checksums. Python 3.13, locked scientific packages, Electron, profiles, logs,
datasets and caches stay in the extracted folder. Setup does not require an
administrator, change machine PATH, change PowerShell execution policy, disable
the Electron sandbox, or start the remote preview viewer. Setup failures leave
the error visible; diagnostic output is in `.cache/logs/windows-start.log`.

The source trial and launcher checks have been verified on Linux. A native
Windows build/run has **not** been executed in this workspace, so Windows
compatibility is still awaiting that run. The Linux AppImage cannot run on
Windows. The trial ZIP is a source launcher, not a prebuilt Windows installer.

## Windows installers and GitHub releases

The **Windows desktop preview** workflow runs on Windows after a push to `main`
or a manual Actions dispatch. It installs tools inside the checkout, runs the
complete Python regression, builds the native Windows PyInstaller engine and
verifies actual PCA/PhenoGraph worker processes. It packages an unpacked Windows
desktop and checks all bundled engine/interface/desktop files against their
sources. It then runs the packaged desktop and independent plot-window checks
with the normal Electron sandbox.

Only a successful Windows job produces the setup and portable EXEs and publishes
an immutable `v0.1.0-preview.<run number>` prerelease with checksums. Download the
**portable EXE** to try the app or the **setup EXE** to install it. Those builds
bundle the engine and interface and do not need separate Node.js or Python.
Preview builds are unsigned. Code signing and full FlowJo/biological/instrument
validation remain unfinished.

To build locally from a source checkout on Windows, run:

```bat
Start-CytoForge.cmd --dev-tools
.local\tools\node\node.exe tools\windows\build.mjs
```

Installer outputs are under `artifacts/installers/`, and the native Windows
validation record is `artifacts/windows-build-validation.json`.

## Publishing the prepared source

This workspace's original Git index is preserved. A separate source-only commit
is prepared in `.tmp/github-publication`, with a transferable Git bundle at
`artifacts/CytoForge-github.bundle`. It includes application code, tests and
published/generated reference fixtures, documentation, dependency locks and the
Windows release workflow. Original acquisitions, profiles, caches, credentials
and locally built installers are excluded. The commit uses an automation author.

After SSH access works, push from this checkout with:

```bash
git -C .tmp/github-publication push -u origin main
```

This uses the existing SSH key and performs a normal push. If the remote already
has incompatible history, Git rejects the push rather than replacing it. On
another machine with Git installed, clone the bundle instead:

```bash
git clone CytoForge-github.bundle CytoForge
cd CytoForge
git remote set-url origin git@github.com:EhsanRS/CytoForge.git
git push -u origin main
```

The Windows workflow then publishes a prerelease only after its native checks
pass. Repository publication and releases have not yet occurred: this session's
SSH client rejects a system configuration file's ownership/permissions, and its
GitHub connector reports read access without push permission for the repository.
