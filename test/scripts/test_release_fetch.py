#!/usr/bin/env python

import os
import unittest
from unittest import mock

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


class TestForkTestReleaseSource(unittest.TestCase):
    """`TRON_RELEASE_ARTIFACT_RELEASE=<owner>/<repo>@<tag>` makes fetch pull
    from a published GitHub release instead of S3, so `prepare` can run on a
    fork with no S3 bucket. Unset (production), fetch uses S3 -- the tests
    above cover that path; these cover only the override.
    """

    SPEC = "tronprotocol/solidity@tv_0.8.27"

    def test_unset_env_is_production_s3_path(self):
        self.assertIsNone(fetch.artifact_release())

    @mock.patch.dict(os.environ, {"TRON_RELEASE_ARTIFACT_RELEASE": SPEC})
    def test_list_keys_reads_release_assets(self):
        run = FakeRun({
            ("gh", "release", "view", "tv_0.8.27", "--repo", "tronprotocol/solidity",
             "--json", "assets", "--jq", ".assets[].name"):
                "solc-macos\nsolc-static-linux\nsolc-windows.exe\nsoljson.js\n"
                "shasum.txt\nkeccak256.txt\n",
        })
        # bucket/commit are ignored in override mode; pass placeholders.
        self.assertEqual(
            fetch.list_keys(run, "", "deadbeef"),
            ["solc-macos", "solc-static-linux", "solc-windows.exe", "soljson.js",
             "shasum.txt", "keccak256.txt"],
        )

    @mock.patch.dict(os.environ, {"TRON_RELEASE_ARTIFACT_RELEASE": SPEC})
    def test_download_uses_gh_release_download_with_one_pattern_per_name(self):
        patterns = []
        for name in ALL_FOUR:
            patterns += ["-p", name]
        run = FakeRun({
            tuple(["gh", "release", "download", "tv_0.8.27", "--repo",
                   "tronprotocol/solidity", "-D", "/tmp/art", "--clobber"] + patterns): "",
        })
        fetch.download(run, "", "deadbeef", ALL_FOUR, "/tmp/art")
        self.assertEqual(len(run.calls), 1)

    @mock.patch.dict(os.environ, {"TRON_RELEASE_ARTIFACT_RELEASE": "no-at-sign"})
    def test_malformed_spec_raises(self):
        with self.assertRaisesRegex(ValueError, "owner"):
            fetch.list_keys(FakeRun({}), "", "deadbeef")

    @mock.patch.dict(os.environ, {"TRON_RELEASE_ARTIFACT_RELEASE": "norepo@tag"})
    def test_spec_without_slash_in_repo_raises(self):
        with self.assertRaisesRegex(ValueError, "owner"):
            fetch.list_keys(FakeRun({}), "", "deadbeef")


if __name__ == "__main__":
    unittest.main()
