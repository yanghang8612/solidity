#!/usr/bin/env python

import json
import os
import tempfile
import unittest

from release import fetch, gates
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS
from release_test_helpers import FakeRun

REPO = "acme/solidity"
RUN_ID = "12345"
COMMIT = "19164bedaa1a6ad09e7a9bf461d5ce73423d7611"
NAMES = [name for name, _, _ in ARTIFACT_SPECS]


def view_argv():
    return [
        "gh", "run", "view", RUN_ID, "--repo", REPO,
        "--json", "databaseId,headSha,conclusion,workflowName",
    ]


def metadata(**changes):
    data = {
        "databaseId": int(RUN_ID),
        "headSha": COMMIT,
        "conclusion": "success",
        "workflowName": "Build binaries on multi platform",
    }
    data.update(changes)
    return json.dumps(data)


class TestVerifyRun(unittest.TestCase):
    def test_accepts_successful_build_for_exact_commit(self):
        run = FakeRun({tuple(view_argv()): metadata()})
        self.assertEqual(fetch.verify_run(run, REPO, RUN_ID, COMMIT)["headSha"], COMMIT)

    def test_rejects_wrong_commit(self):
        run = FakeRun({tuple(view_argv()): metadata(headSha="0" * 40)})
        with self.assertRaisesRegex(GateError, "expected"):
            fetch.verify_run(run, REPO, RUN_ID, COMMIT)

    def test_rejects_failed_run(self):
        run = FakeRun({tuple(view_argv()): metadata(conclusion="failure")})
        with self.assertRaisesRegex(GateError, "failure"):
            fetch.verify_run(run, REPO, RUN_ID, COMMIT)

    def test_rejects_another_workflow(self):
        run = FakeRun({tuple(view_argv()): metadata(workflowName="Tests")})
        with self.assertRaisesRegex(GateError, "build.yml"):
            fetch.verify_run(run, REPO, RUN_ID, COMMIT)


class TestDownload(unittest.TestCase):
    def test_downloads_all_five_named_actions_artifacts(self):
        with tempfile.TemporaryDirectory() as dest:
            for name in NAMES:
                open(os.path.join(dest, name), "wb").close()

            artifact_names = {
                "solc-windows.exe": "solc-windows",
                "solc-macos": "solc-macos",
                "solc-static-linux": "solc-linux",
                "solc-static-linux-arm": "solc-linux-arm",
                "soljson.js": "solc-ems",
            }
            table = {
                tuple([
                    "gh", "run", "download", RUN_ID, "--repo", REPO,
                    "--name", artifact_names[name], "--dir", dest,
                ]): ""
                for name in NAMES
            }
            run = FakeRun(table)
            fetch.download(run, REPO, RUN_ID, NAMES, dest)
            self.assertEqual(len(run.calls), 5)

    def test_g3_requires_linux_arm64_too(self):
        with self.assertRaisesRegex(GateError, "solc-static-linux-arm"):
            gates.g3_artifacts_present([name for name in NAMES if name != "solc-static-linux-arm"])


if __name__ == "__main__":
    unittest.main()
