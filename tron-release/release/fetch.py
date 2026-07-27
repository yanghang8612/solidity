"""Fetch release binaries from a successful GitHub Actions build run."""

import json
import os
from typing import Callable, List

from release.errors import GateError

Run = Callable[[List[str]], str]


_ARTIFACT_RUN_NAMES = {
    "solc-windows.exe": "solc-windows",
    "solc-macos": "solc-macos",
    "solc-static-linux": "solc-linux",
    "solc-static-linux-arm": "solc-linux-arm",
    "soljson.js": "solc-ems",
}


def verify_run(run: Run, repo: str, run_id: str, commit: str) -> dict:
    """Require a successful `build.yml` run for exactly `commit`."""
    raw = run([
        "gh", "run", "view", str(run_id), "--repo", repo,
        "--json", "databaseId,headSha,conclusion,workflowName",
    ])
    data = json.loads(raw)
    required = ("databaseId", "headSha", "conclusion", "workflowName")
    missing = [field for field in required if field not in data]
    if missing:
        raise GateError(f"G3: build run metadata is missing {missing}")
    if data["headSha"] != commit:
        raise GateError(
            f"G3: build run {run_id} is for {data['headSha']}, expected {commit}"
        )
    if data["conclusion"] != "success":
        raise GateError(
            f"G3: build run {run_id} concluded {data['conclusion']!r}, expected 'success'"
        )
    if data["workflowName"] != "Build binaries on multi platform":
        raise GateError(
            f"G3: run {run_id} belongs to {data['workflowName']!r}, not build.yml"
        )
    return data


def download(run: Run, repo: str, run_id: str, names: List[str], dest: str) -> None:
    """Download each named binary from its build-run artifact."""
    os.makedirs(dest, exist_ok=True)
    unknown = [name for name in names if name not in _ARTIFACT_RUN_NAMES]
    if unknown:
        raise ValueError(f"no GitHub Actions artifact mapping for {unknown}")

    for name in names:
        run([
            "gh", "run", "download", str(run_id), "--repo", repo,
            "--name", _ARTIFACT_RUN_NAMES[name], "--dir", dest,
        ])
        path = os.path.join(dest, name)
        if not os.path.isfile(path):
            raise GateError(
                f"G3: Actions artifact {_ARTIFACT_RUN_NAMES[name]!r} did not contain {name}"
            )
