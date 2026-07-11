"""Digest computation and the shasum.txt / keccak256.txt projections."""

import hashlib
from typing import Dict

from release.manifest import Manifest

_CHUNK = 1024 * 1024
_ALGORITHMS = ("sha256", "keccak256")


def sha256(path: str) -> str:
    """sha256 of the file at `path`, lowercase hex, no 0x prefix.

    Stdlib `hashlib` only. This is what `cli.verify_downloaded` calls, so that
    `tron_release sign` -- the one command handed to the signing-key holder,
    who has neither a dev environment nor pycryptodome installed -- never
    needs anything beyond the standard library.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def digests(path: str) -> Dict[str, str]:
    """sha256 and keccak256 of the file at `path`, lowercase hex, no 0x prefix."""
    # `sign` must run without pycryptodome installed (see `sha256` above and
    # `cli.cmd_sign`'s docstring), so this import stays inside the function --
    # a module-level `from Crypto.Hash import keccak` would make merely
    # importing `checksums` (and therefore `cli`) require pycryptodome. Only
    # `cmd_prepare`, which does need keccak256, ever reaches this line.
    from Crypto.Hash import keccak  # pylint: disable=import-outside-toplevel

    sha = hashlib.sha256()
    kec = keccak.new(digest_bits=256)
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            sha.update(chunk)
            kec.update(chunk)
    return {"sha256": sha.hexdigest(), "keccak256": kec.hexdigest()}


def render(man: Manifest, algorithm: str) -> str:
    """Project the manifest into a `<hex>  <name>` sums file."""
    if algorithm not in _ALGORITHMS:
        raise ValueError(f"unsupported algorithm: {algorithm}")
    lines = []
    for artifact in man.artifacts:
        value = getattr(artifact, algorithm)
        if value is None:
            raise ValueError(f"manifest has no {algorithm} for {artifact.name}")
        lines.append(f"{value}  {artifact.name}")
    return "\n".join(lines) + "\n"
