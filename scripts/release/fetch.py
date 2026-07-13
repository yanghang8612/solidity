"""The only module that touches AWS.

Production pulls the four build artifacts from S3, where `build.yml` uploaded
them keyed by commit sha. A fork-test override (`TRON_RELEASE_ARTIFACT_RELEASE`,
set to ``<owner>/<repo>@<tag>``) instead pulls them from a *published* GitHub
release, so the pipeline can run end-to-end on a fork with no S3 bucket. When
the override is set the borrowed artifacts belong to some *other* commit, so
their version evidence will not match the fork's release commit -- `cmd_prepare`
knows to downgrade G4 to a warning in that mode. Production leaves it unset and
this whole branch is dead code there.
"""

import os
from typing import Callable, List, Optional, Tuple

Run = Callable[[List[str]], str]

_RELEASE_ENV = "TRON_RELEASE_ARTIFACT_RELEASE"


def artifact_release() -> Optional[str]:
    """The fork-test artifact-source override, or None in production."""
    return os.environ.get(_RELEASE_ENV) or None


def _parse_release_spec(spec: str) -> Tuple[str, str]:
    """Split ``<owner>/<repo>@<tag>`` into (repo, tag), failing closed."""
    if "@" not in spec:
        raise ValueError(
            f"{_RELEASE_ENV} must be '<owner>/<repo>@<tag>', got {spec!r}"
        )
    repo, _, tag = spec.partition("@")
    if not repo or "/" not in repo or not tag:
        raise ValueError(
            f"{_RELEASE_ENV} must be '<owner>/<repo>@<tag>', got {spec!r}"
        )
    return repo, tag


def list_keys(run: Run, bucket: str, commit: str) -> List[str]:
    """Artifact names available for `commit` -- from a release in fork-test mode,
    else object names directly under s3://<bucket>/<commit>/."""
    spec = artifact_release()
    if spec:
        repo, tag = _parse_release_spec(spec)
        out = run(["gh", "release", "view", tag, "--repo", repo,
                   "--json", "assets", "--jq", ".assets[].name"])
        return [line.strip() for line in out.split("\n") if line.strip()]

    out = run(["aws", "s3", "ls", f"s3://{bucket}/{commit}/"])
    names = []
    for line in out.split("\n"):
        parts = line.split()
        if not parts or parts[0] == "PRE":
            continue
        names.append(parts[-1])
    return names


def download(run: Run, bucket: str, commit: str, names: List[str], dest: str) -> None:
    """Copy each of `names` into `dest` -- from a release in fork-test mode, else
    one `aws s3 cp` per file from s3://<bucket>/<commit>/."""
    spec = artifact_release()
    if spec:
        repo, tag = _parse_release_spec(spec)
        patterns = []
        for name in names:
            patterns += ["-p", name]
        run(["gh", "release", "download", tag, "--repo", repo,
             "-D", dest, "--clobber"] + patterns)
        return

    for name in names:
        run(["aws", "s3", "cp", f"s3://{bucket}/{commit}/{name}",
             os.path.join(dest, name), "--only-show-errors"])
