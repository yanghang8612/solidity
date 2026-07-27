"""Release gates. Every gate fails closed."""

import os
from typing import Callable, Dict, List, Optional

from release import checksums
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS, Manifest

Run = Callable[[List[str]], str]


def g1_merge_commit(parents: List[str], is_ancestor: bool) -> None:
    """G1: the release commit must be a two-parent merge commit on develop."""
    if not is_ancestor:
        raise GateError("G1: release commit is not an ancestor of develop")
    if len(parents) != 2:
        raise GateError(
            f"G1: release commit must have exactly 2 parents, found {len(parents)}"
        )


def g2_qa_approval(parents: List[str], reviews: List[dict], qa_logins: List[str]) -> Dict:
    """G2: QA must have approved exactly the code being released.

    `parents` must be the two parents of the release merge commit -- the same
    list G1 validates -- with `parents[1]` the head of the merged branch, i.e.
    the code QA actually tested. Callers must run G1 before G2.

    `reviews` must come from the GitHub API (`release.github.reviews`) and must
    never be re-derived from git topology: this gate exists to check GitHub's
    independently recorded approval against the code being released, so
    computing the "approved" commit from the commit graph itself would
    collapse the check to `X == X`.

    The approval's `commit_id` is recorded by GitHub at approval time and cannot be
    forged or mistyped. If someone pushed to the branch after QA approved, the two
    diverge and this gate stops the release.
    """
    if not qa_logins:
        raise GateError("G2: QA reviewer whitelist is empty; refusing to proceed")

    if len(parents) != 2:
        raise GateError(
            f"G2: parents must be the release merge commit's 2 parents, found {len(parents)}"
        )

    approvals = [
        r for r in reviews
        if r.get("state") == "APPROVED" and r.get("user", {}).get("login") in qa_logins
    ]
    if not approvals:
        raise GateError(
            f"G2: no APPROVED review from any QA reviewer {sorted(qa_logins)}"
        )

    for approval in approvals:
        login = approval.get("user", {}).get("login")
        for field in ("commit_id", "submitted_at"):
            if approval.get(field) is None:
                raise GateError(
                    f"G2: APPROVED review from {login} is missing '{field}'; "
                    "cannot verify this review against the released code"
                )

    latest = max(approvals, key=lambda r: r.get("submitted_at"))
    tested = parents[1]
    if latest.get("commit_id") != tested:
        raise GateError(
            f"G2: {latest['user']['login']} approved {latest['commit_id']} "
            f"but the released code is {tested}; the branch moved after QA signed off"
        )

    return {"reviewer": latest["user"]["login"], "submittedAt": latest["submitted_at"]}


def g3_artifacts_present(present: List[str]) -> None:
    """G3: all configured binaries must exist in the selected Actions run."""
    missing = [name for name, _, _ in ARTIFACT_SPECS if name not in present]
    if missing:
        raise GateError(f"G3: missing artifacts in S3: {missing}")


# `prepare` runs on a GitHub-hosted x86-64 Linux runner. Execute the native
# Linux compiler and soljson for strong evidence; inspect embedded strings in
# the cross-platform macOS, Linux ARM64, and Windows binaries.
_EXECUTABLE_ARTIFACTS = ("solc-static-linux", "soljson.js")
_STRING_ARTIFACTS = ("solc-macos", "solc-static-linux-arm", "solc-windows.exe")


def g4_version_evidence(
    evidence_by_name: Dict[str, str], long_version: str, short_commit: str
) -> None:
    """G4: every artifact must carry evidence of the release commit."""
    version = long_version.split("+")[0]

    for name in _EXECUTABLE_ARTIFACTS:
        if name not in evidence_by_name:
            raise GateError(f"G4: missing evidence for {name}")
        text = evidence_by_name[name]
        if not text.startswith(long_version + "."):
            raise GateError(
                f"G4: {name} reports {text!r}, expected it to start with "
                f"{long_version + '.'!r}"
            )

    for name in _STRING_ARTIFACTS:
        if name not in evidence_by_name:
            raise GateError(f"G4: missing evidence for {name}")
        if evidence_by_name[name] != f"{version}|{short_commit}":
            raise GateError(
                f"G4: {name} does not contain both {version!r} and {short_commit!r} "
                f"(evidence: {evidence_by_name[name]!r})"
            )


def _validsig_primary_fingerprint(line: str) -> str:
    """The *last* whitespace-separated field of a `[GNUPG:] VALIDSIG` line.

    VALIDSIG's fields are:
        VALIDSIG <sig-key-fpr> <date> <sig-ts> <expire> <version> <reserved>
                 <pubkey-algo> <hash-algo> <sig-class> <primary-key-fpr>
    The first field (right after the tag) is the *signing subkey*; the last
    is the *primary key* -- the one release-config.json pins against. Using
    the first field would pin the wrong key entirely.
    """
    return line.split()[-1]


def g5_signatures(run: Run, config: dict, artifacts_dir: str, names: List[str]) -> None:
    """G5: every artifact carries a valid signature from the pinned release key.

    `gpg --verify` exits 0 for a good signature made by *any* key gpg has
    ever seen -- trusted or not (a freshly-imported release key is reported
    as `TRUST_UNDEFINED`, yet verification still succeeds). So a zero exit
    code alone proves nothing about *which* key signed; this gate must
    inspect gpg's own status output to find out.

    `--status-fd 1` routes gpg's machine-readable `[GNUPG:] ...` status lines
    to stdout, which is all `run()` returns -- plain `gpg --verify` instead
    prints a human-readable "Primary key fingerprint: ..." line to stderr,
    which `run()` never sees. From that stdout, this gate requires:
      1. `run(...)` not raising (gpg exited 0),
      2. a `[GNUPG:] GOODSIG` line, and
      3. exactly one `[GNUPG:] VALIDSIG` line whose *last* field -- the
         primary key fingerprint, not the signing subkey named in the first
         field -- equals the pinned `signingKeyFingerprint`.
    Any of the three failing raises GateError. An absent VALIDSIG is never a
    silent pass, and more than one is never resolved by taking the first.
    """
    if "signingKeyFingerprint" not in config:
        raise GateError("G5: config has no 'signingKeyFingerprint' key")
    expected = config["signingKeyFingerprint"].replace(" ", "").upper()
    for name in names:
        path = os.path.join(artifacts_dir, name)
        try:
            output = run(["gpg", "--status-fd", "1", "--verify", path + ".sig", path])
        except Exception as error:  # pylint: disable=broad-except
            raise GateError(f"G5: gpg failed to verify {name}: {error}") from error

        lines = output.splitlines()
        if not any(line.startswith("[GNUPG:] GOODSIG") for line in lines):
            raise GateError(f"G5: {name}: no GOODSIG line in gpg status output")

        validsigs = [line for line in lines if line.startswith("[GNUPG:] VALIDSIG")]
        if not validsigs:
            raise GateError(f"G5: {name}: no VALIDSIG line in gpg status output")
        if len(validsigs) > 1:
            raise GateError(
                f"G5: {name}: {len(validsigs)} VALIDSIG lines in gpg status output, expected 1"
            )

        actual = _validsig_primary_fingerprint(validsigs[0]).upper()
        if actual != expected:
            raise GateError(
                f"G5: {name}: signed by primary key fingerprint {actual}, "
                f"expected the pinned {expected}"
            )


def g6_asset_digests(artifacts_dir: str, man: Manifest) -> None:
    """G6: the assets sitting on the draft release are still the exact bytes
    that `prepare` hashed into the manifest.

    Uses `checksums.sha256` (stdlib `hashlib` only), never `checksums.digests`
    (which lazily imports pycryptodome the moment it runs): G6 only ever
    needs sha256, so calling `digests` would waste a keccak256 computation
    and needlessly require pycryptodome on the apply-stage host -- the same
    reasoning that keeps `cli.verify_downloaded` on `checksums.sha256`.

    A manifest artifact whose `sha256` is `None` (a manifest that never went
    through `with_digests`, or a hand-edited/corrupt manifest.json) must fail
    this gate, not vacuously pass it or raise a bare TypeError comparing
    `None` to a hex string.
    """
    for artifact in man.artifacts:
        if artifact.sha256 is None:
            raise GateError(f"G6: manifest has no sha256 for {artifact.name}")

        path = os.path.join(artifacts_dir, artifact.name)
        if not os.path.exists(path):
            raise GateError(f"G6: {artifact.name} missing from draft release")

        actual = checksums.sha256(path)
        if actual != artifact.sha256:
            raise GateError(
                f"G6: {artifact.name} sha256 {actual} != manifest {artifact.sha256}"
            )


def g7_ready_for_review(is_draft: bool, solcbin_pr_url: Optional[str]) -> None:
    """G7: apply may finish only while the release is still private.

    At this point no tag may exist yet (GitHub creates it on publish), the
    release must still be a draft, and the solc-bin PR must be open. Human
    review happens on the draft release and that PR before `publish`.

    `is_draft` must be a value the caller just obtained from a fresh
    `github.release_view` call, never one it computed or assumed itself --
    a human could have published the release out of band between
    `prepare`/`sign` and `apply`, and this gate is the only thing that would
    notice.
    """
    if not is_draft:
        raise GateError(
            "G7: release is already published; apply must precede publication"
        )
    if not solcbin_pr_url:
        raise GateError("G7: no solc-bin pull request URL; nothing to reference")
