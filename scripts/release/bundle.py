"""Pack the release-request email attachment: `<stem>.tgz`.

`0827.tgz` -- the historical name of this attachment -- is nothing more than
`0.8.27` with the dots stripped; see `bundle_stem`. A naive `.replace(".",
"").lstrip("0")` mangles that to `827`: `bundle_stem` exists precisely so
this transformation is written, and tested, in exactly one place.
"""

import os
import shutil
import tarfile

from release.errors import GateError
from release.manifest import Manifest

_SUMS_FILES = ("shasum.txt", "keccak256.txt")


def bundle_stem(version: str) -> str:
    """0.8.27 -> 0827, matching the historical `0827.tgz` attachment name."""
    return version.replace(".", "")


def pack_tgz(man: Manifest, workdir: str, repo_root: str) -> str:
    """Pack the release-request email attachment.

    `<stem>.tgz` contains, under a top-level `<stem>/` directory: the four
    release binaries, their four `.sig` files, `shasum.txt`, `keccak256.txt`,
    a copy of the release notes as `ReleaseNotes.md`, and the source tarball
    if one exists at `<repo_root>/upload/solidity_<version>.tar.gz` (`prepare`
    always builds one; its absence here just means this `workdir` was not, in
    fact, populated from a real `prepare` run).

    Fails closed -- naming every missing binary/signature/sums file at once,
    not just the first -- rather than silently packing an incomplete
    attachment: the request email tells its reader everything is "见附件"
    (see attachment), so a tgz quietly missing one signature would be
    discovered only by that reader, never by this pipeline.
    """
    names = [a.name for a in man.artifacts]
    notes_path = os.path.join(repo_root, "release-notes", f"{man.tag}.md")
    missing = []
    for name in names:
        if not os.path.exists(os.path.join(workdir, name)):
            missing.append(name)
        if not os.path.exists(os.path.join(workdir, name + ".sig")):
            missing.append(name + ".sig")
    for name in _SUMS_FILES:
        if not os.path.exists(os.path.join(workdir, name)):
            missing.append(name)
    if not os.path.exists(notes_path):
        missing.append(notes_path)
    if missing:
        raise GateError(f"pack_tgz: missing from {workdir}: {missing}")

    stem = bundle_stem(man.version)
    bundle_dir = os.path.join(workdir, stem)
    os.makedirs(bundle_dir, exist_ok=True)
    for name in names:
        shutil.copyfile(os.path.join(workdir, name), os.path.join(bundle_dir, name))
        shutil.copyfile(os.path.join(workdir, name + ".sig"),
                         os.path.join(bundle_dir, name + ".sig"))
    for name in _SUMS_FILES:
        shutil.copyfile(os.path.join(workdir, name), os.path.join(bundle_dir, name))

    shutil.copyfile(notes_path, os.path.join(bundle_dir, "ReleaseNotes.md"))

    tarball = os.path.join(repo_root, "upload", f"solidity_{man.version}.tar.gz")
    if os.path.exists(tarball):
        shutil.copyfile(tarball, os.path.join(bundle_dir, os.path.basename(tarball)))

    out = os.path.join(workdir, f"{stem}.tgz")
    with tarfile.open(out, "w:gz") as tar:
        tar.add(bundle_dir, arcname=stem)
    return out
