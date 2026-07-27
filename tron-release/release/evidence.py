"""Extract per-artifact evidence of which commit a binary was built from.

Strength varies by platform. Executables that the runner can run yield the full
version string; cross-platform binaries yield only grep-able fragments. The weak
case is backstopped by the S3 key, which is the commit sha itself.
"""

import errno
import re
from typing import Callable, List, Optional

Run = Callable[[List[str]], str]

_VERSION_LINE = re.compile(r"^Version:\s*(\S+)", re.MULTILINE)

_SOLJSON_SNIPPET = (
    "process.stdout.write(require('{path}').cwrap('solidity_version','string',[])())"
)


def _from_solc(run: Run, path: str) -> str:
    match = _VERSION_LINE.search(run([path, "--version"]))
    if match is None:
        raise ValueError(f"no 'Version:' line in `{path} --version` output")
    return match.group(1)


def _from_soljson(run: Run, path: str) -> str:
    return run(["node", "-e", _SOLJSON_SNIPPET.format(path=path)]).strip()


def _from_strings(path: str, version: str, short_commit: str) -> str:
    with open(path, "rb") as handle:
        blob = handle.read()
    found_version = version if version.encode() in blob else ""
    found_commit = short_commit if short_commit.encode() in blob else ""
    return f"{found_version}|{found_commit}"


def extract(
    run: Optional[Run],
    name: str,
    path: str,
    version: Optional[str] = None,
    short_commit: Optional[str] = None,
) -> str:
    """Evidence text for artifact `name` stored at `path`."""
    if name == "solc-static-linux":
        try:
            return _from_solc(run, path)
        except OSError as exc:
            # A developer may rehearse `prepare` from macOS or another host
            # that cannot execute the Linux x86-64 artifact.  Preserve the
            # stronger `--version` check on the Ubuntu release runner, but
            # fall back to the same embedded version/commit evidence used for
            # the other cross-platform binaries when the kernel reports an
            # incompatible executable format.
            if exc.errno != errno.ENOEXEC:
                raise
            return _from_strings(path, version, short_commit)
    if name == "soljson.js":
        return _from_soljson(run, path)
    if name in ("solc-macos", "solc-static-linux-arm", "solc-windows.exe"):
        return _from_strings(path, version, short_commit)
    raise ValueError(f"unknown artifact: {name}")
