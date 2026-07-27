#!/usr/bin/env python

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest

from release import cli, manifest
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS
from release_test_helpers import FakeRun

VERSION = "1.2.3"
TAG = f"tv_{VERSION}"
REPO = "acme/widget"
SOLCBIN_REPO = "acme/widget-bin"
FINGERPRINT = "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5"
COMMIT = "cafef00d1234567890abcdef1234567890abcdef"
TESTED_COMMIT = "c7c21da02b4a8e82a2abbd9228d7c5969f700621"
BRANCH = f"binaries-for-{VERSION}"
NAMES = [name for name, _, _ in ARTIFACT_SPECS]


def write_config(root):
    directory = os.path.join(root, "tron-release")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "repo": REPO,
            "solcBinRepo": SOLCBIN_REPO,
            "signingKeyFingerprint": FINGERPRINT,
            "qaReviewers": [],
        }, handle)


def fixture(workdir):
    contents = {name: f"fake-{name}".encode() for name in NAMES}
    digests = {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "keccak256": "0" * 64}
        for name, data in contents.items()
    }
    man = manifest.Manifest(
        version=VERSION, tag=TAG, codename="Test", commit=COMMIT,
        tested_commit=TESTED_COMMIT,
        qa_approval={"pr": 42, "reviewer": "qa", "submittedAt": "2026-01-01T00:00:00Z"},
        prs={"upstreamMerge": [], "features": []},
        artifacts=manifest.build_artifacts(f"{VERSION}+commit.{COMMIT[:8]}"),
    ).with_digests(digests)
    for name, data in contents.items():
        with open(os.path.join(workdir, name), "wb") as handle:
            handle.write(data)
        open(os.path.join(workdir, name + ".sig"), "wb").close()
    with open(os.path.join(workdir, "manifest.json"), "w", encoding="utf-8") as handle:
        handle.write(man.to_json())
    open(os.path.join(workdir, "manifest.json.sig"), "wb").close()
    open(os.path.join(workdir, "shasum.txt"), "wb").close()
    open(os.path.join(workdir, "keccak256.txt"), "wb").close()
    open(os.path.join(workdir, "signing-key.asc"), "wb").close()
    return man


def seed_solcbin(workdir):
    checkout = os.path.join(workdir, "solc-bin")
    for _, directory, _ in ARTIFACT_SPECS:
        target = os.path.join(checkout, directory)
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "list.json"), "w", encoding="utf-8") as handle:
            json.dump({"builds": []}, handle)
    return checkout


def good_gpg(fingerprint=FINGERPRINT):
    return (
        "[GNUPG:] GOODSIG F859BCB44A28290B signer\n"
        "[GNUPG:] VALIDSIG ABCD 2026-01-01 0 0 4 0 1 8 00 " + fingerprint + "\n"
    )


def download_argv(workdir):
    patterns = []
    for name in NAMES:
        patterns += ["-p", name, "-p", name + ".sig"]
    patterns += [
        "-p", "shasum.txt", "-p", "keccak256.txt",
        "-p", "manifest.json", "-p", "manifest.json.sig", "-p", "signing-key.asc",
    ]
    return ["gh", "release", "download", TAG, "--repo", REPO,
            "-D", workdir, "--clobber"] + patterns


def release_view_argv():
    return ["gh", "release", "view", TAG, "--repo", REPO,
            "--json", "tagName,isDraft,assets,targetCommitish"]


class TestCmdApply(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="apply-root-")
        self.workdir = tempfile.mkdtemp(prefix="apply-work-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        write_config(self.root)
        fixture(self.workdir)
        self.checkout = seed_solcbin(self.workdir)

    def args(self, dry_run=True):
        return argparse.Namespace(tag=TAG, workdir=self.workdir, dry_run=dry_run)

    def table(self, dry_run=True, is_draft=True, target=COMMIT):
        table = {
            tuple(download_argv(self.workdir)): "",
            tuple(["gpg", "--batch", "--import",
                   os.path.join(self.workdir, "signing-key.asc")]): "",
            tuple(["gh", "pr", "list", "--repo", SOLCBIN_REPO, "--head", BRANCH,
                   "--json", "url", "--jq", "[.[].url]"]): "[]\n",
            tuple(["gh", "repo", "clone", SOLCBIN_REPO, self.checkout,
                   "--", "--depth", "1"]): "",
            tuple(["git", "-C", self.checkout, "checkout", "-b", BRANCH]): "",
            tuple(["git", "-C", self.checkout, "config", "user.name",
                   "tron-release-bot"]): "",
            tuple(["git", "-C", self.checkout, "config", "user.email",
                   "41898282+github-actions[bot]@users.noreply.github.com"]): "",
            tuple(["git", "-C", self.checkout, "add", "-A"]): "",
            tuple(["git", "-C", self.checkout, "commit", "-m", f"Binaries for {VERSION}"]): "",
            tuple(release_view_argv()): json.dumps({
                "tagName": TAG, "isDraft": is_draft, "assets": [],
                "targetCommitish": target,
            }),
        }
        for name in NAMES + ["manifest.json"]:
            table[tuple(["gpg", "--status-fd", "1", "--verify",
                         os.path.join(self.workdir, name) + ".sig",
                         os.path.join(self.workdir, name)])] = good_gpg()
        if not dry_run:
            table[tuple(["git", "-C", self.checkout, "push", "origin", BRANCH])] = ""
            table[tuple(["gh", "pr", "create", "--repo", SOLCBIN_REPO,
                         "--head", BRANCH, "--title", f"Binaries for {VERSION}",
                         "--body", ""])] = "https://example.test/pr/9\n"
        return table

    def test_dry_run_verifies_manifest_and_five_binaries_without_push(self):
        run = FakeRun(self.table())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_apply(self.args(), run=run, repo_root=self.root), 0)
        verify_calls = [call for call in run.calls if call[:4] == ["gpg", "--status-fd", "1", "--verify"]]
        self.assertEqual(len(verify_calls), 6)
        self.assertFalse(any(call[:3] == ["git", "-C", self.checkout] and "push" in call
                             for call in run.calls))

    def test_real_apply_opens_pr_but_sends_no_email(self):
        run = FakeRun(self.table(dry_run=False))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.assertEqual(cli.cmd_apply(self.args(False), run=run, repo_root=self.root), 0)
        self.assertIn("ready for review", captured.getvalue())
        self.assertNotIn("mail", captured.getvalue().lower())

    def test_bad_signature_stops_before_solcbin(self):
        table = self.table()
        first = NAMES[0]
        table[tuple(["gpg", "--status-fd", "1", "--verify",
                     os.path.join(self.workdir, first) + ".sig",
                     os.path.join(self.workdir, first)])] = good_gpg("0" * 40)
        run = FakeRun(table)
        with self.assertRaisesRegex(GateError, "fingerprint"):
            cli.cmd_apply(self.args(), run=run, repo_root=self.root)
        self.assertFalse(any(call[:2] == ["gh", "repo"] for call in run.calls))

    def test_tampered_binary_stops_before_solcbin(self):
        with open(os.path.join(self.workdir, NAMES[-1]), "wb") as handle:
            handle.write(b"tampered")
        run = FakeRun(self.table())
        with self.assertRaisesRegex(GateError, "G6"):
            cli.cmd_apply(self.args(), run=run, repo_root=self.root)
        self.assertFalse(any(call[:2] == ["gh", "repo"] for call in run.calls))

    def test_release_target_must_match_signed_manifest(self):
        run = FakeRun(self.table(target="0" * 40))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "draft release targets"):
                cli.cmd_apply(self.args(), run=run, repo_root=self.root)

    def test_g7_rejects_already_published_release(self):
        run = FakeRun(self.table(is_draft=False))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "G7"):
                cli.cmd_apply(self.args(), run=run, repo_root=self.root)


if __name__ == "__main__":
    unittest.main()
