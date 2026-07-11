"""The only module that touches AWS."""

import os
from typing import Callable, List

Run = Callable[[List[str]], str]


def list_keys(run: Run, bucket: str, commit: str) -> List[str]:
    """Object names directly under s3://<bucket>/<commit>/."""
    out = run(["aws", "s3", "ls", f"s3://{bucket}/{commit}/"])
    names = []
    for line in out.split("\n"):
        parts = line.split()
        if not parts or parts[0] == "PRE":
            continue
        names.append(parts[-1])
    return names


def download(run: Run, bucket: str, commit: str, names: List[str], dest: str) -> None:
    """Copy each of `names` from s3://<bucket>/<commit>/ into `dest`, one `aws s3 cp` per file."""
    for name in names:
        run(["aws", "s3", "cp", f"s3://{bucket}/{commit}/{name}",
             os.path.join(dest, name), "--only-show-errors"])
