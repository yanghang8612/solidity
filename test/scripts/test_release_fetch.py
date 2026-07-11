#!/usr/bin/env python

import unittest

# pragma pylint: disable=import-error
from release import fetch, gates
from release.errors import GateError
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

BUCKET = "solc-prod"
COMMIT = "19164bedaa1a6ad09e7a9bf461d5ce73423d7611"
ALL_FOUR = ["solc-macos", "solc-static-linux", "solc-windows.exe", "soljson.js"]


class TestListKeys(unittest.TestCase):
    def test_parses_aws_ls_output(self):
        run = FakeRun({
            ("aws", "s3", "ls", f"s3://{BUCKET}/{COMMIT}/"):
                "2026-04-24 01:00:00   20124408 solc-static-linux\n"
                "2026-04-24 01:00:01    9187505 soljson.js\n"
                "2026-04-24 01:00:02          0 github-binaries.tar\n",
        })
        self.assertEqual(
            fetch.list_keys(run, BUCKET, COMMIT),
            ["solc-static-linux", "soljson.js", "github-binaries.tar"],
        )

    def test_ignores_directory_entries(self):
        run = FakeRun({
            ("aws", "s3", "ls", f"s3://{BUCKET}/{COMMIT}/"):
                "                           PRE nested/\n"
                "2026-04-24 01:00:00   20124408 solc-static-linux\n",
        })
        self.assertEqual(fetch.list_keys(run, BUCKET, COMMIT), ["solc-static-linux"])


class TestG3(unittest.TestCase):
    def test_accepts_all_four(self):
        gates.g3_artifacts_present(ALL_FOUR + ["github-binaries.tar"])

    def test_rejects_missing_artifact(self):
        with self.assertRaisesRegex(GateError, "soljson.js"):
            gates.g3_artifacts_present(["solc-macos", "solc-static-linux",
                                        "solc-windows.exe"])

    def test_rejects_empty_prefix(self):
        with self.assertRaisesRegex(GateError, "solc-macos"):
            gates.g3_artifacts_present([])


class TestDownload(unittest.TestCase):
    def test_issues_one_cp_per_artifact(self):
        run = FakeRun({
            ("aws", "s3", "cp", f"s3://{BUCKET}/{COMMIT}/{name}",
             f"/tmp/art/{name}", "--only-show-errors"): ""
            for name in ALL_FOUR
        })
        fetch.download(run, BUCKET, COMMIT, ALL_FOUR, "/tmp/art")
        self.assertEqual(len(run.calls), 4)


if __name__ == "__main__":
    unittest.main()
