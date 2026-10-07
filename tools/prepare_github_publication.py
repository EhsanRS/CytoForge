"""Prepare a source-only Git commit/bundle without changing this checkout's Git index."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from package_source_trial import ROOT, digest_bytes, source_files


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, default=ROOT / ".tmp/github-publication")
    parser.add_argument("--bundle", type=Path, default=ROOT / "artifacts/CytoForge-github.bundle")
    args = parser.parse_args()
    snapshot, bundle = args.snapshot_dir.resolve(), args.bundle.resolve()
    if (
        not snapshot.is_relative_to(ROOT / ".tmp")
        or snapshot == ROOT / ".tmp"
        or snapshot.exists()
        or not bundle.is_relative_to(ROOT / "artifacts")
        or bundle.exists()
    ):
        parser.error("Choose new snapshot and bundle paths inside this checkout")
    files = source_files()
    original_index = ROOT / ".git/index"
    before = file_hash(original_index) if original_index.is_file() else None
    snapshot.mkdir(parents=True)
    for relative, data, mode in files:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode & 0o777)
        assert file_hash(target) == digest_bytes(data), relative

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(snapshot), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "CytoForge build")
    git("config", "user.email", "cytoforge-build@users.noreply.github.com")
    git("config", "core.autocrlf", "false")
    git("add", "--", ".")
    committed = set(git("ls-files").splitlines())
    assert committed == {name for name, _, _ in files}
    git("commit", "-m", "Add CytoForge desktop app and checked Windows preview release pipeline")
    git("remote", "add", "origin", "git@github.com:EhsanRS/CytoForge.git")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    git("bundle", "create", str(bundle), "main")
    git("bundle", "verify", str(bundle))
    after = file_hash(original_index) if original_index.is_file() else None
    assert before == after
    record = dict(
        status="prepared_not_pushed",
        repository="EhsanRS/CytoForge",
        branch="main",
        commit=git("rev-parse", "HEAD"),
        source_files=len(files),
        snapshot=str(snapshot.relative_to(ROOT)),
        bundle=str(bundle.relative_to(ROOT)),
        bundle_sha256=file_hash(bundle),
        original_git_index_unchanged=True,
        original_datasets_profiles_and_credentials_excluded=True,
        push_command="git -C .tmp/github-publication push -u origin main",
        author="CytoForge build (automation identity)",
        release_workflow=".github/workflows/windows-release.yml",
        source_sha256={name: digest_bytes(data) for name, data, _ in files},
    )
    (ROOT / "artifacts/github-publication-preparation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                k: record[k]
                for k in (
                    "status",
                    "commit",
                    "source_files",
                    "bundle",
                    "original_git_index_unchanged",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
