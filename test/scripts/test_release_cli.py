#!/usr/bin/env python

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

# pragma pylint: disable=import-error
from release import cli, checksums
from release.errors import GateError
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

VERSION = "1.2.3"
CODENAME = "TestCodename"
TAG = f"tv_{VERSION}"
REPO = "acme/widget"
QA_LOGIN = "qa-bot"
BUCKET = "fake-release-bucket"
PR = 42
COMMIT = "cafef00d1234567890abcdef1234567890abcdef"
PARENT1 = "1111111111111111111111111111111111111111"
PARENT2 = "2222222222222222222222222222222222222222"
SHORT_COMMIT = COMMIT[:8]
LONG_VERSION = f"{VERSION}+commit.{SHORT_COMMIT}"


def write_config(root, repo, qa_reviewers):
    os.makedirs(os.path.join(root, ".github"), exist_ok=True)
    config = {
        "repo": repo,
        "solcBinRepo": f"{repo}-bin",
        "signingKeyFingerprint": "DEADBEEF",
        "qaReviewers": qa_reviewers,
    }
    path = os.path.join(root, ".github", "release-config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)


def write_notes(root, version, codename):
    os.makedirs(os.path.join(root, "release-notes"), exist_ok=True)
    text = (
        "---\n"
        f"version: {version}\n"
        f"codename: {codename}\n"
        "testReport: https://example.test/report\n"
        "---\n"
        f"TRON Solidity compiler {version} is fully compatible with "
        f"Ethereum Solidity {version}.\n"
        "\n"
        "Fixture release notes body.\n"
    )
    path = os.path.join(root, "release-notes", f"tv_{version}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class TestRun(unittest.TestCase):
    def test_returns_stdout_on_success(self):
        completed = subprocess.CompletedProcess(args=["true"], returncode=0, stdout="ok\n")
        with mock.patch("release.cli.subprocess.run", return_value=completed):
            self.assertEqual(cli.run(["true"]), "ok\n")

    def test_still_raises_on_failure(self):
        """gitinfo.is_ancestor's except-and-return-False depends on this still raising.

        Tightened to the specific `RuntimeError` `cli.run` wraps failures in --
        a bare `assertRaises(Exception)` would also pass if this regressed to
        let the raw `CalledProcessError` escape unwrapped (whose `__str__()`
        omits stderr; see the message test below), silently losing the
        regression-detection this test exists for.
        """
        error = subprocess.CalledProcessError(
            returncode=1, cmd=["git", "merge-base"], stderr="fatal: bad revision\n")
        with mock.patch("release.cli.subprocess.run", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                cli.run(["git", "merge-base"])
        self.assertNotIsInstance(ctx.exception, subprocess.CalledProcessError)

    def test_error_message_includes_command_and_stderr(self):
        """Plain CalledProcessError.__str__() omits stderr -- cli.run must not."""
        error = subprocess.CalledProcessError(
            returncode=1, cmd=["gh", "release", "create"], stderr="HTTP 404: not found\n")
        with mock.patch("release.cli.subprocess.run", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                cli.run(["gh", "release", "create", "tv_1.2.3"])
        message = str(ctx.exception)
        self.assertIn("HTTP 404: not found", message)
        self.assertIn("gh release create tv_1.2.3", message)


class TestMainErrorHandling(unittest.TestCase):
    """`main()` must never let a known pipeline exception escape as a raw traceback.

    Every one must become a clean stderr line plus a non-zero exit code.
    `cmd_prepare` is mocked out entirely (via `mock.patch.object`), so these
    tests never touch the filesystem or real subprocesses; `main()`'s own
    subparser wiring binds `func=cmd_prepare` by looking up the module-level
    name at call time, so the patched mock is what actually gets invoked.
    """

    def run_main_with(self, error):
        with mock.patch.object(cli, "cmd_prepare", side_effect=error):
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                result = cli.main(["prepare", "deadbeef"])
        return result, captured.getvalue()

    def test_gate_error_gets_gate_failed_prefix(self):
        result, stderr = self.run_main_with(GateError("G1: not a merge commit"))
        self.assertEqual(result, 2)
        self.assertIn("GATE FAILED: G1: not a merge commit", stderr)

    def test_value_error_gets_a_clean_message_and_nonzero_exit(self):
        """A bad config (load_config) must not surface as a raw traceback."""
        result, stderr = self.run_main_with(ValueError("missing required key(s): ['repo']"))
        self.assertNotEqual(result, 0)
        self.assertIn("missing required key(s): ['repo']", stderr)
        self.assertNotIn("GATE FAILED", stderr)

    def test_runtime_error_gets_a_clean_message_and_nonzero_exit(self):
        """A failed git/gh/aws command (cli.run) must not surface as a raw traceback."""
        result, stderr = self.run_main_with(RuntimeError("command failed (1): gh release create"))
        self.assertNotEqual(result, 0)
        self.assertIn("command failed (1): gh release create", stderr)
        self.assertNotIn("GATE FAILED", stderr)


class TestRequireEnv(unittest.TestCase):
    def test_returns_the_value_when_set(self):
        with mock.patch.dict(os.environ, {"RELEASE_CLI_TEST_VAR": "value"}):
            self.assertEqual(cli.require_env("RELEASE_CLI_TEST_VAR"), "value")

    def test_raises_descriptive_error_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RELEASE_CLI_TEST_VAR_UNSET", None)
            with self.assertRaisesRegex(ValueError, "RELEASE_CLI_TEST_VAR_UNSET"):
                cli.require_env("RELEASE_CLI_TEST_VAR_UNSET")


class TestLoadConfig(unittest.TestCase):
    """`load_config` must fail closed on a missing/misconfigured key.

    This is the same fail-closed defect class already fixed elsewhere in this
    project (bare KeyError/IndexError in `gates.g2_qa_approval`, bare KeyError
    in `gates.g4_version_evidence` and `Manifest.with_digests`): a missing key
    must raise a descriptive `ValueError`, not a bare `KeyError` from a raw
    subscript deep inside `cmd_prepare`. Every test below writes its own
    temp-file config -- never the real `.github/release-config.json`.
    """

    def write_raw_config(self, root, config):
        os.makedirs(os.path.join(root, ".github"), exist_ok=True)
        path = os.path.join(root, ".github", "release-config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        return path

    def test_loads_a_valid_config(self):
        with tempfile.TemporaryDirectory() as root:
            write_config(root, REPO, [QA_LOGIN])
            config = cli.load_config(root)
        self.assertEqual(config["repo"], REPO)
        self.assertEqual(config["qaReviewers"], [QA_LOGIN])

    def test_present_but_empty_qa_reviewers_still_loads(self):
        """An empty whitelist is the deliberate ship-state, not a broken config.

        `load_config` must not reject it -- `gates.g2_qa_approval` is the one
        that fails closed on an empty whitelist, and that behavior must stay
        untouched.
        """
        with tempfile.TemporaryDirectory() as root:
            write_config(root, REPO, [])
            config = cli.load_config(root)
        self.assertEqual(config["qaReviewers"], [])

    def test_raises_value_error_naming_missing_repo(self):
        """A missing `repo` key must raise ValueError naming it, not a bare KeyError.

        assertRaisesRegex with ValueError (not just assertRaises) matters here:
        a regression back to the bare `config["repo"]` subscript raises
        KeyError instead, which is not a ValueError, so this test would error
        out (not silently pass) if that guard were ever removed.
        """
        with tempfile.TemporaryDirectory() as root:
            self.write_raw_config(root, {
                "solcBinRepo": f"{REPO}-bin",
                "signingKeyFingerprint": "DEADBEEF",
                "qaReviewers": [QA_LOGIN],
            })
            with self.assertRaisesRegex(ValueError, "repo"):
                cli.load_config(root)

    def test_raises_value_error_naming_missing_qa_reviewers(self):
        """A missing `qaReviewers` key must raise ValueError naming it, not KeyError.

        `qaReviewers` is expected to be hand-edited by a maintainer before the
        first real release, so a typo dropping the key entirely is a live
        scenario, not just a hypothetical.
        """
        with tempfile.TemporaryDirectory() as root:
            self.write_raw_config(root, {
                "repo": REPO,
                "solcBinRepo": f"{REPO}-bin",
                "signingKeyFingerprint": "DEADBEEF",
            })
            with self.assertRaisesRegex(ValueError, "qaReviewers"):
                cli.load_config(root)

    def test_names_every_missing_key_at_once(self):
        """Several missing keys -> one error naming all of them, not just the first."""
        with tempfile.TemporaryDirectory() as root:
            self.write_raw_config(root, {"solcBinRepo": f"{REPO}-bin"})
            with self.assertRaises(ValueError) as ctx:
                cli.load_config(root)
        message = str(ctx.exception)
        self.assertIn("repo", message)
        self.assertIn("signingKeyFingerprint", message)
        self.assertIn("qaReviewers", message)

    def test_raises_value_error_when_qa_reviewers_is_not_a_list(self):
        """A hand-edit that turns the whitelist into a bare string must be rejected.

        Otherwise `gates.g2_qa_approval`'s `login in qa_logins` would silently
        do substring matching against a string instead of list membership.
        """
        with tempfile.TemporaryDirectory() as root:
            self.write_raw_config(root, {
                "repo": REPO,
                "solcBinRepo": f"{REPO}-bin",
                "signingKeyFingerprint": "DEADBEEF",
                "qaReviewers": QA_LOGIN,
            })
            with self.assertRaisesRegex(ValueError, "qaReviewers"):
                cli.load_config(root)


class TestBuildSourceTarball(unittest.TestCase):
    def test_creates_marker_when_absent_and_removes_it_after(self):
        with tempfile.TemporaryDirectory() as root:
            script = os.path.join(root, "scripts", "create_source_tarball.sh")
            version_script = os.path.join(root, "scripts", "get_version.sh")
            marker = os.path.join(root, "prerelease.txt")
            seen = {}

            def fake_run(argv):
                if tuple(argv) == (script,):
                    seen["marker_existed"] = os.path.exists(marker)
                    seen["marker_size"] = (
                        os.path.getsize(marker) if seen["marker_existed"] else None)
                    return ""
                if tuple(argv) == (version_script,):
                    return VERSION + "\n"
                raise AssertionError(f"unexpected command: {argv}")

            self.assertFalse(os.path.exists(marker))
            tarball = cli.build_source_tarball(fake_run, root)

            self.assertTrue(seen["marker_existed"])
            self.assertEqual(seen["marker_size"], 0)
            self.assertFalse(os.path.exists(marker))
            self.assertEqual(tarball, os.path.join(root, "upload", f"solidity_{VERSION}.tar.gz"))

    def test_leaves_pre_existing_empty_marker_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            script = os.path.join(root, "scripts", "create_source_tarball.sh")
            version_script = os.path.join(root, "scripts", "get_version.sh")
            marker = os.path.join(root, "prerelease.txt")
            with open(marker, "w", encoding="utf-8"):
                pass

            run = FakeRun({(script,): "", (version_script,): VERSION + "\n"})
            cli.build_source_tarball(run, root)

            self.assertTrue(os.path.exists(marker))
            self.assertEqual(os.path.getsize(marker), 0)

    def test_rejects_non_empty_marker_without_running_anything(self):
        with tempfile.TemporaryDirectory() as root:
            marker = os.path.join(root, "prerelease.txt")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("this looks like a nightly build in progress")

            run = FakeRun({})
            with self.assertRaisesRegex(GateError, "prerelease.txt"):
                cli.build_source_tarball(run, root)

            self.assertEqual(run.calls, [])
            with open(marker, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "this looks like a nightly build in progress")


class TestGateOrdering(unittest.TestCase):
    def test_g1_runs_before_g2(self):
        """If G1 fails, cmd_prepare must never reach G2's github.reviews call.

        The fake run table below has no entry for `gh api .../pulls` or
        `.../reviews`. If cli.py ever called those before G1 raised, FakeRun
        would raise AssertionError (unexpected command) instead of the
        expected GateError -- proving the order is G1 then G2, not the
        reverse (G2 also self-guards on len(parents), but the *contract* is
        that G1 runs first).
        """
        with tempfile.TemporaryDirectory() as root:
            write_config(root, REPO, [QA_LOGIN])
            write_notes(root, VERSION, CODENAME)
            run = FakeRun({
                ("git", "rev-parse", COMMIT): COMMIT + "\n",
                (os.path.join(root, "scripts", "get_version.sh"),): VERSION + "\n",
                ("git", "rev-list", "--parents", "-n", "1", COMMIT): f"{COMMIT} {PARENT1}\n",
                ("git", "merge-base", "--is-ancestor", COMMIT, "origin/develop"): "",
            })
            args = argparse.Namespace(commit=COMMIT, workdir=root, dry_run=True)
            with self.assertRaisesRegex(GateError, "G1"):
                cli.cmd_prepare(args, run=run, repo_root=root)


class TestCmdPrepareDryRun(unittest.TestCase):
    """End-to-end `prepare --dry-run`: every git/gh/aws call is faked."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-release-cli-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write_config(self.root, REPO, [QA_LOGIN])
        write_notes(self.root, VERSION, CODENAME)

        self.artifacts_dir = os.path.join(self.root, "artifacts")
        os.makedirs(self.artifacts_dir)
        self.fixtures = {
            "solc-macos": b"#!/bin/sh\necho fake solc-macos\n",
            "solc-static-linux": f"ELF junk {VERSION} junk {SHORT_COMMIT} junk".encode(),
            "solc-windows.exe": f"MZ junk {VERSION} junk {SHORT_COMMIT} junk".encode(),
            "soljson.js": b"// fake soljson placeholder\n",
        }
        for name, data in self.fixtures.items():
            with open(os.path.join(self.artifacts_dir, name), "wb") as handle:
                handle.write(data)

    def build_run(self):
        table = {
            ("git", "rev-parse", COMMIT): COMMIT + "\n",
            (os.path.join(self.root, "scripts", "get_version.sh"),): VERSION + "\n",
            ("git", "rev-list", "--parents", "-n", "1", COMMIT):
                f"{COMMIT} {PARENT1} {PARENT2}\n",
            ("git", "merge-base", "--is-ancestor", COMMIT, "origin/develop"): "",
            ("gh", "api", f"repos/{REPO}/commits/{COMMIT}/pulls", "--jq", "[.[].number]"):
                f"[{PR}]\n",
            ("gh", "api", f"repos/{REPO}/pulls/{PR}/reviews", "--paginate"):
                json.dumps([{
                    "state": "APPROVED",
                    "commit_id": PARENT2,
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "user": {"login": QA_LOGIN},
                }]),
            ("aws", "s3", "ls", f"s3://{BUCKET}/{COMMIT}/"):
                "2026-01-01 00:00:00        10 solc-macos\n"
                "2026-01-01 00:00:00        10 solc-static-linux\n"
                "2026-01-01 00:00:00        10 solc-windows.exe\n"
                "2026-01-01 00:00:00        10 soljson.js\n",
            ("git", "tag", "--list", "tv_*", "--sort=-creatordate"): f"{TAG}\ntv_1.2.2\n",
            ("git", "tag", "-l", "v*"): "v1.2.2\n",
            ("git", "log", "--merges", "--format=%s", f"tv_1.2.2..{COMMIT}",
             "--not", "refs/tags/v1.2.2"):
                "Merge pull request #99 from someuser/some-feature\n",
            (os.path.join(self.root, "scripts", "create_source_tarball.sh"),): "",
            (os.path.join(self.artifacts_dir, "solc-macos"), "--version"):
                f"Version: {LONG_VERSION}.TEST\n",
        }
        for name in self.fixtures:
            table[("aws", "s3", "cp", f"s3://{BUCKET}/{COMMIT}/{name}",
                    os.path.join(self.artifacts_dir, name), "--only-show-errors")] = ""

        soljson_path = os.path.join(self.artifacts_dir, "soljson.js")
        table[("node", "-e",
               f"process.stdout.write(require('{soljson_path}')"
               ".cwrap('solidity_version','string',[])())")] = f"{LONG_VERSION}.TEST"
        return FakeRun(table)

    def test_writes_manifest_and_skips_gh_release_create(self):
        run = self.build_run()
        args = argparse.Namespace(commit=COMMIT, workdir=self.root, dry_run=True)

        captured = io.StringIO()
        with mock.patch.dict(os.environ, {"S3_BUCKET_PROD": BUCKET}):
            with contextlib.redirect_stdout(captured):
                result = cli.cmd_prepare(args, run=run, repo_root=self.root)
        self.assertEqual(result, 0)
        self.assertIn(f"[dry-run] would create draft release {TAG}", captured.getvalue())

        manifest_path = os.path.join(self.root, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["version"], VERSION)
        self.assertEqual(payload["tag"], TAG)
        self.assertEqual(payload["commit"], COMMIT)
        self.assertEqual(payload["testedCommit"], PARENT2)
        self.assertEqual(payload["prs"], {"upstreamMerge": [], "features": [99]})
        self.assertEqual(payload["releaseName"], f"{VERSION}_{CODENAME}")
        self.assertEqual(payload["qaApproval"]["reviewer"], QA_LOGIN)
        self.assertEqual(payload["qaApproval"]["pr"], PR)

        digests_by_name = {a["name"]: a for a in payload["artifacts"]}
        self.assertEqual(set(digests_by_name), set(self.fixtures))
        for name in self.fixtures:
            expected = checksums.digests(os.path.join(self.artifacts_dir, name))
            self.assertEqual(digests_by_name[name]["sha256"], expected["sha256"])
            self.assertEqual(digests_by_name[name]["keccak256"], expected["keccak256"])

        self.assertTrue(os.path.exists(os.path.join(self.root, "shasum.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "keccak256.txt")))

        release_create_calls = [c for c in run.calls if c[:3] == ["gh", "release", "create"]]
        self.assertEqual(release_create_calls, [])

    def test_does_not_leave_a_prerelease_marker_behind(self):
        run = self.build_run()
        args = argparse.Namespace(commit=COMMIT, workdir=self.root, dry_run=True)
        with mock.patch.dict(os.environ, {"S3_BUCKET_PROD": BUCKET}):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_prepare(args, run=run, repo_root=self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "prerelease.txt")))


if __name__ == "__main__":
    unittest.main()
