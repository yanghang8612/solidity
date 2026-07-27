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
from unittest import mock

# pragma pylint: disable=import-error
from release import cli, manifest
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

VERSION = "1.2.3"
CODENAME = "TestCodename"
TAG = f"tv_{VERSION}"
REPO = "acme/widget"
SOLCBIN_REPO = "acme/widget-bin"
SOLCBIN_PR = "99"
FINGERPRINT = "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5"
COMMIT = "cafef00d1234567890abcdef1234567890abcdef"
TESTED_COMMIT = "c7c21da02b4a8e82a2abbd9228d7c5969f700621"

ARTIFACT_NAMES = [name for name, _, _ in ARTIFACT_SPECS]


def write_config(root, repo=REPO, solcbin_repo=SOLCBIN_REPO, fingerprint=FINGERPRINT):
    os.makedirs(os.path.join(root, "tron-release"), exist_ok=True)
    config = {
        "repo": repo,
        "solcBinRepo": solcbin_repo,
        "signingKeyFingerprint": fingerprint,
        "qaReviewers": [],
    }
    path = os.path.join(root, "tron-release", "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)


def make_manifest(digests):
    return manifest.Manifest(
        version=VERSION, tag=TAG, codename=CODENAME, commit=COMMIT,
        tested_commit=TESTED_COMMIT,
        qa_approval={"pr": 42, "reviewer": "qa-bot", "submittedAt": "2026-01-01T00:00:00Z"},
        prs={"upstreamMerge": [], "features": []},
        artifacts=manifest.build_artifacts(f"{VERSION}+commit.{COMMIT[:8]}"),
    ).with_digests(digests)


def write_fixture_artifacts(workdir):
    """Write the four binaries + `.sig` files + manifest.json into `workdir`,
    standing in for what `gh release download` would have produced -- the
    manifest's sha256 fields are computed from the *actual* fake bytes
    written here, so G6 (which recomputes and compares) has something real
    to check, not a hand-typed digest that happens to be unreachable. Same
    technique as `build_manifest_and_write_artifacts` in
    test_release_apply.py, minus shasum.txt/keccak256.txt: `cmd_publish`'s
    re-verification download does not fetch those (G6 recomputes sha256
    directly from the binaries against the manifest, never from shasum.txt).
    """
    contents = {name: f"fake-{name}-bytes".encode() for name in ARTIFACT_NAMES}
    digests = {name: {"sha256": hashlib.sha256(data).hexdigest(), "keccak256": "0" * 64}
               for name, data in contents.items()}
    man = make_manifest(digests)

    for name, data in contents.items():
        with open(os.path.join(workdir, name), "wb") as handle:
            handle.write(data)
        with open(os.path.join(workdir, name + ".sig"), "wb") as handle:
            handle.write(f"fake-{name}-sig-bytes".encode())
    with open(os.path.join(workdir, "manifest.json"), "w", encoding="utf-8") as handle:
        handle.write(man.to_json())
    open(os.path.join(workdir, "manifest.json.sig"), "wb").close()
    open(os.path.join(workdir, "signing-key.asc"), "wb").close()
    return man


def good_gpg(fingerprint=FINGERPRINT):
    return (
        "[GNUPG:] GOODSIG F859BCB44A28290B build_tron <build@example.invalid>\n"
        "[GNUPG:] VALIDSIG 1254F859D2B1BD9F66E7107DF859BCB44A28290B 2026-05-27 "
        f"1779873591 0 4 0 1 8 00 {fingerprint}\n"
    )


def download_argv(workdir, tag=TAG, repo=REPO):
    patterns = []
    for name in ARTIFACT_NAMES:
        patterns += ["-p", name, "-p", name + ".sig"]
    patterns += [
        "-p", "manifest.json", "-p", "manifest.json.sig", "-p", "signing-key.asc",
    ]
    return ["gh", "release", "download", tag, "--repo", repo,
            "-D", workdir, "--clobber"] + patterns


def gpg_argv(workdir, name):
    return ["gpg", "--status-fd", "1", "--verify",
            os.path.join(workdir, name) + ".sig", os.path.join(workdir, name)]


def release_view_argv(tag=TAG, repo=REPO):
    return ["gh", "release", "view", tag, "--repo", repo,
            "--json", "tagName,isDraft,assets,targetCommitish"]


def release_edit_argv(tag=TAG, repo=REPO):
    return ["gh", "release", "edit", tag, "--repo", repo, "--draft=false"]


def pr_merge_argv(pr=SOLCBIN_PR, repo=SOLCBIN_REPO):
    return ["gh", "pr", "merge", pr, "--repo", repo, "--merge"]


def release_view_response(is_draft):
    return json.dumps({
        "tagName": TAG, "isDraft": is_draft, "assets": [], "targetCommitish": COMMIT,
    })


class TestCmdPublish(unittest.TestCase):
    """`cmd_publish` re-verifies G5 (signatures) and G6 (asset digests vs.
    the manifest) by re-downloading the draft release's *current* assets
    immediately before the irreversible `gh release edit --draft=false` --
    the same "never trust previously-observed state, re-check what's
    reachable right now" principle G7 already applies in `apply`. Each test
    below relies on FakeRun raising AssertionError for any command not in
    its table, so a regression that makes cmd_publish call an unexpected
    command -- or skip a required one, such as the re-verification itself --
    fails the test instead of silently passing.
    """

    def setUp(self):
        self.repo_root = tempfile.mkdtemp(prefix="test-release-publish-root-")
        self.workdir = tempfile.mkdtemp(prefix="test-release-publish-workdir-")
        self.addCleanup(shutil.rmtree, self.repo_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        write_config(self.repo_root)

    def args(self, tag=TAG, solcbin_pr=None, dry_run=False, workdir=None):
        return argparse.Namespace(
            tag=tag, solcbin_pr=solcbin_pr, dry_run=dry_run,
            workdir=self.workdir if workdir is None else workdir)

    def verified_table(self, is_draft=True):
        """A FakeRun table covering `release_view` plus, when the release is
        still a draft, the download + per-artifact gpg calls the G5/G6
        re-verification performs. `is_draft=False` deliberately returns just
        the one entry: the already-published early return must happen before
        any of that, so a regression that downloads/verifies anyway hits an
        unlisted command and fails via AssertionError.
        """
        table = {tuple(release_view_argv()): release_view_response(is_draft=is_draft)}
        if is_draft:
            write_fixture_artifacts(self.workdir)
            table[tuple(download_argv(self.workdir))] = ""
            table[("gpg", "--batch", "--import",
                   os.path.join(self.workdir, "signing-key.asc"))] = ""
            for name in ARTIFACT_NAMES + ["manifest.json"]:
                table[tuple(gpg_argv(self.workdir, name))] = good_gpg()
        return table

    def test_already_published_is_idempotent_and_does_not_edit(self):
        """FakeRun's table has only the release_view entry. If cmd_publish
        tried to download/re-verify or `gh release edit` an already-published
        release anyway, FakeRun would raise AssertionError on that unlisted
        command instead of letting this test pass -- proving the ordering is
        release_view first, then the already-published check, and only THEN
        (skipped here) download + G5/G6.
        """
        run = FakeRun(self.verified_table(is_draft=False))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_publish(self.args(), run=run, repo_root=self.repo_root)

        self.assertEqual(result, 0)
        self.assertIn("already published", captured.getvalue())
        self.assertEqual(run.calls, [release_view_argv()])

    def test_dry_run_runs_g5_and_g6_but_does_not_edit_or_merge(self):
        """--dry-run must still exercise the re-verification -- that IS the
        value of a publish dry run -- so the gpg argv must appear once per
        artifact. FakeRun's table lacks both `gh release edit` and `gh pr
        merge`; either call happening would raise AssertionError.
        """
        run = FakeRun(self.verified_table(is_draft=True))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_publish(
                self.args(solcbin_pr=SOLCBIN_PR, dry_run=True), run=run,
                repo_root=self.repo_root)

        self.assertEqual(result, 0)
        self.assertIn("dry-run", captured.getvalue())
        self.assertIn(SOLCBIN_PR, captured.getvalue())

        gpg_calls = [c for c in run.calls if c and c[0] == "gpg"]
        self.assertEqual(len(gpg_calls), len(ARTIFACT_NAMES) + 2)
        self.assertIn(download_argv(self.workdir), run.calls)
        self.assertNotIn(release_edit_argv(), run.calls)
        self.assertNotIn(pr_merge_argv(), run.calls)

    def test_dry_run_without_solcbin_pr_says_no_pr_to_merge(self):
        """With no --solcbin-pr, the dry-run preview must not claim it would
        merge a PR -- a real run would attempt no merge either.
        """
        run = FakeRun(self.verified_table(is_draft=True))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_publish(self.args(solcbin_pr=None, dry_run=True),
                                      run=run, repo_root=self.repo_root)

        self.assertEqual(result, 0)
        self.assertIn("dry-run", captured.getvalue())
        self.assertIn("no solc-bin PR", captured.getvalue())
        self.assertNotIn(release_edit_argv(), run.calls)
        self.assertNotIn(pr_merge_argv(), run.calls)

    def test_omitted_solcbin_pr_does_not_attempt_merge(self):
        """--solcbin-pr is optional. FakeRun's table has release_view, the
        re-verification download/gpg calls, and release_edit, but no `gh pr
        merge` entry -- if cmd_publish attempted the merge anyway despite
        solcbin_pr being None, FakeRun would raise AssertionError on that
        unlisted command.
        """
        table = self.verified_table(is_draft=True)
        table[tuple(release_edit_argv())] = ""
        run = FakeRun(table)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_publish(self.args(solcbin_pr=None), run=run,
                                      repo_root=self.repo_root)

        self.assertEqual(result, 0)
        self.assertIn(release_edit_argv(), run.calls)
        self.assertNotIn(pr_merge_argv(), run.calls)
        self.assertIn("published", captured.getvalue())

    def test_given_solcbin_pr_is_merged_against_the_solcbin_repo(self):
        """The mirror image of the previous test: when --solcbin-pr *is*
        given, the merge must actually happen, against solcBinRepo (not
        repo) and with the given PR number -- proving the two repos/values
        are not accidentally swapped.
        """
        table = self.verified_table(is_draft=True)
        table[tuple(release_edit_argv())] = ""
        table[tuple(pr_merge_argv())] = ""
        run = FakeRun(table)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_publish(self.args(solcbin_pr=SOLCBIN_PR), run=run,
                                      repo_root=self.repo_root)

        self.assertEqual(result, 0)
        self.assertIn(release_edit_argv(), run.calls)
        self.assertIn(pr_merge_argv(), run.calls)
        self.assertIn("merged", captured.getvalue())

    def test_stops_before_release_edit_when_g6_fails(self):
        """One downloaded binary's on-disk bytes are tampered with after the
        manifest (which recorded the digest of the *original* bytes) was
        already written, so G6's freshly recomputed sha256 must not match --
        and `gh release edit` (the irreversible flip) must never run.
        """
        run = FakeRun(self.verified_table(is_draft=True))

        with open(os.path.join(self.workdir, ARTIFACT_NAMES[-1]), "wb") as handle:
            handle.write(b"tampered bytes that do not match the manifest sha256")

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "G6"):
                cli.cmd_publish(self.args(), run=run, repo_root=self.repo_root)

        self.assertNotIn(release_edit_argv(), run.calls)
        self.assertNotIn(pr_merge_argv(), run.calls)

    def test_stops_before_release_edit_when_g5_fails(self):
        """A bad signature on just the first artifact must stop the whole
        pipeline before `gh release edit` runs. Only one gpg table entry is
        supplied (for the first artifact, with the wrong fingerprint) -- if
        g5_signatures ever continued past the first bad signature to check a
        second artifact, FakeRun would raise AssertionError (unexpected
        command) instead of the GateError this test expects.
        """
        write_fixture_artifacts(self.workdir)
        table = {
            tuple(release_view_argv()): release_view_response(is_draft=True),
            tuple(download_argv(self.workdir)): "",
            tuple(["gpg", "--batch", "--import",
                   os.path.join(self.workdir, "signing-key.asc")]): "",
            tuple(gpg_argv(self.workdir, ARTIFACT_NAMES[0])): good_gpg("0" * 40),
        }
        run = FakeRun(table)

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "fingerprint"):
                cli.cmd_publish(self.args(), run=run, repo_root=self.repo_root)

        self.assertNotIn(release_edit_argv(), run.calls)
        self.assertNotIn(pr_merge_argv(), run.calls)


class TestMainRegistersPublish(unittest.TestCase):
    def test_parses_tag_solcbin_pr_workdir_and_dry_run(self):
        with mock.patch.object(cli, "cmd_publish", return_value=0) as mocked:
            result = cli.main(["publish", TAG, "--solcbin-pr", SOLCBIN_PR,
                                "--workdir", "/tmp/example", "--dry-run"])

        self.assertEqual(result, 0)
        mocked.assert_called_once()
        args = mocked.call_args[0][0]
        self.assertEqual(args.tag, TAG)
        self.assertEqual(args.solcbin_pr, SOLCBIN_PR)
        self.assertEqual(args.workdir, "/tmp/example")
        self.assertTrue(args.dry_run)

    def test_defaults_solcbin_pr_workdir_to_none_and_dry_run_to_false(self):
        with mock.patch.object(cli, "cmd_publish", return_value=0) as mocked:
            cli.main(["publish", TAG])

        args = mocked.call_args[0][0]
        self.assertIsNone(args.solcbin_pr)
        self.assertIsNone(args.workdir)
        self.assertFalse(args.dry_run)


if __name__ == "__main__":
    unittest.main()
