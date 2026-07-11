#!/usr/bin/env python

import argparse
import contextlib
import email
import email.policy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

# pragma pylint: disable=import-error
from release import bundle, cli, mailer, manifest
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

VERSION = "1.2.3"
CODENAME = "TestCodename"
TAG = f"tv_{VERSION}"
REPO = "acme/widget"
SOLCBIN_REPO = "acme/widget-bin"
FINGERPRINT = "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5"
COMMIT = "cafef00d1234567890abcdef1234567890abcdef"
TESTED_COMMIT = "c7c21da02b4a8e82a2abbd9228d7c5969f700621"
BRANCH = f"binaries-for-{VERSION}"

ARTIFACT_NAMES = [name for name, _, _ in ARTIFACT_SPECS]


# --------------------------------------------------------------------------
# Fixture builders. Each test's `run()` is entirely fake, so anything a real
# `gh`/`git` invocation would have produced on disk must be seeded by hand --
# same approach as TestCmdSign in test_release_sign.py.
# --------------------------------------------------------------------------

def write_config(root, repo=REPO, solcbin_repo=SOLCBIN_REPO, fingerprint=FINGERPRINT):
    os.makedirs(os.path.join(root, ".github"), exist_ok=True)
    config = {
        "repo": repo,
        "solcBinRepo": solcbin_repo,
        "signingKeyFingerprint": fingerprint,
        "qaReviewers": [],
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


def make_manifest(digests):
    return manifest.Manifest(
        version=VERSION, tag=TAG, codename=CODENAME, commit=COMMIT,
        tested_commit=TESTED_COMMIT,
        qa_approval={"pr": 42, "reviewer": "qa-bot", "submittedAt": "2026-01-01T00:00:00Z"},
        prs={"upstreamMerge": [], "features": []},
        artifacts=manifest.build_artifacts(f"{VERSION}+commit.{COMMIT[:8]}"),
    ).with_digests(digests)


def build_manifest_and_write_artifacts(workdir):
    """Write the four binaries + sigs + sums + manifest.json into `workdir`,
    standing in for what `gh release download` would have produced -- the
    manifest's sha256 fields are computed from the *actual* fake bytes
    written here, so G6 (which recomputes and compares) has something real
    to check, not a hand-typed digest that happens to be unreachable.
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
    with open(os.path.join(workdir, "shasum.txt"), "w", encoding="utf-8") as handle:
        handle.write("placeholder shasum contents, unparsed by apply\n")
    with open(os.path.join(workdir, "keccak256.txt"), "w", encoding="utf-8") as handle:
        handle.write("placeholder keccak256 contents, unparsed by apply\n")
    with open(os.path.join(workdir, "manifest.json"), "w", encoding="utf-8") as handle:
        handle.write(man.to_json())
    return man


def seed_solcbin_checkout(workdir):
    """The four solc-bin directories, each with a `list.json` -- standing in
    for what `gh repo clone` would have produced."""
    checkout = os.path.join(workdir, "solc-bin")
    for _, directory, _ in manifest.ARTIFACT_SPECS:
        target_dir = os.path.join(checkout, directory)
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "list.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"builds": []}))
    return checkout


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
    patterns += ["-p", "shasum.txt", "-p", "keccak256.txt", "-p", "manifest.json"]
    return ["gh", "release", "download", tag, "--repo", repo,
            "-D", workdir, "--clobber"] + patterns


def gpg_argv(workdir, name):
    return ["gpg", "--status-fd", "1", "--verify",
            os.path.join(workdir, name) + ".sig", os.path.join(workdir, name)]


def pr_list_argv(repo, branch):
    return ["gh", "pr", "list", "--repo", repo, "--head", branch,
            "--json", "url", "--jq", "[.[].url]"]


def clone_argv(repo, checkout):
    return ["gh", "repo", "clone", repo, checkout, "--", "--depth", "1"]


def checkout_branch_argv(checkout, branch):
    return ["git", "-C", checkout, "checkout", "-b", branch]


def add_argv(checkout):
    return ["git", "-C", checkout, "add", "-A"]


def commit_argv(checkout, version):
    return ["git", "-C", checkout, "commit", "-m", f"Binaries for {version}"]


def push_argv(checkout, branch):
    return ["git", "-C", checkout, "push", "origin", branch]


def pr_create_argv(repo, branch, version):
    return ["gh", "pr", "create", "--repo", repo, "--head", branch,
            "--title", f"Binaries for {version}", "--body", ""]


def release_view_argv(tag, repo):
    return ["gh", "release", "view", tag, "--repo", repo,
            "--json", "tagName,isDraft,assets,targetCommitish"]


def mail_env(password="test-password-not-a-real-secret"):
    return {
        "RELEASE_MAIL_RECIPIENTS": json.dumps({
            "from": "release-bot@example.invalid",
            "to": ["qa-lead@example.invalid"],
        }),
        "RELEASE_MAIL_HOST": "smtp.example.invalid",
        "RELEASE_MAIL_PORT": "465",
        "RELEASE_MAIL_USER": "release-bot@example.invalid",
        "RELEASE_MAIL_PASSWORD": password,
    }


class TestBundleStem(unittest.TestCase):
    """Task 11's dispatch overrides the brief's own first draft, which
    computed `stem = version.replace(".", "").lstrip("0") or ...` --
    `"0.8.27".replace(".", "")` is `"0827"`, and `.lstrip("0")` mangles that
    to `"827"`, losing the leading zero from the historical `0827.tgz`
    attachment name. `bundle_stem` is the one, correct implementation; these
    two cases are the acceptance criteria the dispatch names explicitly.
    """

    def test_0827(self):
        self.assertEqual(bundle.bundle_stem("0.8.27"), "0827")

    def test_0828(self):
        self.assertEqual(bundle.bundle_stem("0.8.28"), "0828")


class TestCmdApplyDryRun(unittest.TestCase):
    """End-to-end `apply --dry-run`: every git/gh/gpg call is faked; no test
    here spawns a subprocess, touches the network, or invokes real gpg.
    """

    def setUp(self):
        self.repo_root = tempfile.mkdtemp(prefix="test-release-apply-root-")
        self.workdir = tempfile.mkdtemp(prefix="test-release-apply-workdir-")
        self.addCleanup(shutil.rmtree, self.repo_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        write_config(self.repo_root)
        write_notes(self.repo_root, VERSION, CODENAME)
        self.man = build_manifest_and_write_artifacts(self.workdir)
        self.checkout = os.path.join(self.workdir, "solc-bin")

    def args(self, dry_run):
        return argparse.Namespace(tag=TAG, workdir=self.workdir, dry_run=dry_run)

    def build_successful_run(self, dry_run):
        table = {tuple(download_argv(self.workdir)): ""}
        for name in ARTIFACT_NAMES:
            table[tuple(gpg_argv(self.workdir, name))] = good_gpg()
        table[tuple(pr_list_argv(SOLCBIN_REPO, BRANCH))] = "[]\n"
        table[tuple(clone_argv(SOLCBIN_REPO, self.checkout))] = ""
        table[tuple(checkout_branch_argv(self.checkout, BRANCH))] = ""
        table[tuple(add_argv(self.checkout))] = ""
        table[tuple(commit_argv(self.checkout, VERSION))] = ""
        if not dry_run:
            table[tuple(push_argv(self.checkout, BRANCH))] = ""
            table[tuple(pr_create_argv(SOLCBIN_REPO, BRANCH, VERSION))] = (
                "https://github.com/tronprotocol/solc-bin/pull/99\n"
            )
        table[tuple(release_view_argv(TAG, REPO))] = json.dumps({
            "tagName": TAG, "isDraft": True, "assets": [], "targetCommitish": COMMIT,
        })
        seed_solcbin_checkout(self.workdir)
        return FakeRun(table)

    def test_dry_run_exercises_g5_and_g6_skips_push_and_pr_create_and_never_sends(self):
        run = self.build_successful_run(dry_run=True)
        captured = io.StringIO()
        with mock.patch.dict(os.environ, mail_env()):
            with mock.patch.object(mailer, "send") as mocked_send:
                with contextlib.redirect_stdout(captured):
                    result = cli.cmd_apply(self.args(dry_run=True), run=run,
                                           repo_root=self.repo_root)

        self.assertEqual(result, 0)

        # G5 and G6 were both exercised: the gpg argv appears once per
        # artifact (G6 is pure Python/hashlib, so it leaves no run() call of
        # its own -- its effect is checked separately, below).
        gpg_calls = [c for c in run.calls if c and c[0] == "gpg"]
        self.assertEqual(len(gpg_calls), len(ARTIFACT_NAMES))

        self.assertNotIn(push_argv(self.checkout, BRANCH), run.calls)
        self.assertNotIn(pr_create_argv(SOLCBIN_REPO, BRANCH, VERSION), run.calls)

        mocked_send.assert_not_called()

        eml_path = os.path.join(self.workdir, "release-request.eml")
        self.assertTrue(os.path.exists(eml_path))
        with open(eml_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        # Subject/body are MIME-encoded for non-ASCII text in the raw .eml;
        # re-parse the way any MTA or mail client would before comparing,
        # exactly as TestNonAsciiRoundTrip does in test_release_mailer.py.
        # The message is multipart (it carries the pack_tgz attachment), so
        # the body text must be fetched via get_body(), not get_content()
        # directly on the top-level (multipart/mixed) message.
        reparsed = email.message_from_string(raw, policy=email.policy.default)
        self.assertEqual(reparsed["Subject"], mailer.render_subject(self.man))
        body_text = reparsed.get_body(preferencelist=("plain",)).get_content()
        self.assertIn(self.man.commit, body_text)

    def test_password_never_appears_in_stdout_stderr_or_eml(self):
        run = self.build_successful_run(dry_run=True)
        password = "S3cr3t-mail-password-must-never-leak-9f8e7d6c"

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, mail_env(password=password)):
            with mock.patch.object(mailer, "send") as mocked_send:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = cli.cmd_apply(self.args(dry_run=True), run=run,
                                           repo_root=self.repo_root)

        self.assertEqual(result, 0)
        mocked_send.assert_not_called()
        self.assertNotIn(password, stdout.getvalue())
        self.assertNotIn(password, stderr.getvalue())

        eml_path = os.path.join(self.workdir, "release-request.eml")
        with open(eml_path, "r", encoding="utf-8") as handle:
            eml_content = handle.read()
        self.assertNotIn(password, eml_content)

    def test_dry_run_succeeds_without_any_smtp_credentials(self):
        """A --dry-run preview needs RELEASE_MAIL_RECIPIENTS (to build the
        message) but none of the four SMTP credentials, which are only used
        on the real-send path. Reading them unconditionally would force a
        local preview to configure secrets it never sends with.
        """
        run = self.build_successful_run(dry_run=True)
        recipients_only = {
            "RELEASE_MAIL_RECIPIENTS": json.dumps({
                "from": "release-bot@example.invalid",
                "to": ["qa-lead@example.invalid"],
            }),
        }
        with mock.patch.dict(os.environ, recipients_only, clear=False):
            for var in ("RELEASE_MAIL_HOST", "RELEASE_MAIL_PORT",
                        "RELEASE_MAIL_USER", "RELEASE_MAIL_PASSWORD"):
                os.environ.pop(var, None)
            with mock.patch.object(mailer, "send") as mocked_send:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = cli.cmd_apply(self.args(dry_run=True), run=run,
                                           repo_root=self.repo_root)

        self.assertEqual(result, 0)
        mocked_send.assert_not_called()
        self.assertTrue(
            os.path.exists(os.path.join(self.workdir, "release-request.eml")))

    def test_stops_before_solcbin_pr_when_g5_fails(self):
        """A bad signature on just the first artifact must stop the whole
        pipeline before any solc-bin call. Only one gpg table entry is
        supplied (for the first artifact, with the wrong fingerprint) --
        if g5_signatures ever continued past the first bad signature to
        check a second artifact, FakeRun would raise AssertionError
        (unexpected command) instead of the GateError this test expects.
        """
        table = {tuple(download_argv(self.workdir)): ""}
        table[tuple(gpg_argv(self.workdir, ARTIFACT_NAMES[0]))] = good_gpg("0" * 40)
        run = FakeRun(table)

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "fingerprint"):
                cli.cmd_apply(self.args(dry_run=True), run=run, repo_root=self.repo_root)

        self.assertEqual([c for c in run.calls if c[:2] == ["gh", "pr"]], [])
        self.assertEqual([c for c in run.calls if c[:2] == ["gh", "repo"]], [])

    def test_stops_before_solcbin_pr_when_g6_fails(self):
        """G5 (faked) passes for all four artifacts, but one artifact's
        on-disk bytes are tampered with after the manifest (which recorded
        the digest of the *original* bytes) was already written, so G6's
        freshly recomputed sha256 must not match.
        """
        table = {tuple(download_argv(self.workdir)): ""}
        for name in ARTIFACT_NAMES:
            table[tuple(gpg_argv(self.workdir, name))] = good_gpg()
        run = FakeRun(table)

        with open(os.path.join(self.workdir, ARTIFACT_NAMES[-1]), "wb") as handle:
            handle.write(b"tampered bytes that do not match the manifest sha256")

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(GateError, "G6"):
                cli.cmd_apply(self.args(dry_run=True), run=run, repo_root=self.repo_root)

        self.assertEqual([c for c in run.calls if c[:2] == ["gh", "pr"]], [])
        self.assertEqual([c for c in run.calls if c[:2] == ["gh", "repo"]], [])


class TestCmdApplyG7Ordering(unittest.TestCase):
    """G7 must run after the solc-bin PR exists and before any mail is sent.
    `dry_run=False` here so `open_solcbin_pr` really pushes and really opens
    the PR (against a fake `run`) -- proving G7's failure short-circuits
    *after* those calls happened, not merely because dry-run would have
    skipped mail anyway.
    """

    def setUp(self):
        self.repo_root = tempfile.mkdtemp(prefix="test-release-apply-g7-root-")
        self.workdir = tempfile.mkdtemp(prefix="test-release-apply-g7-workdir-")
        self.addCleanup(shutil.rmtree, self.repo_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        write_config(self.repo_root)
        # pack_tgz (which runs before G7) reads release-notes/tv_X.md, so it
        # must exist regardless of whether this test expects G7 to pass.
        write_notes(self.repo_root, VERSION, CODENAME)
        self.man = build_manifest_and_write_artifacts(self.workdir)
        self.checkout = os.path.join(self.workdir, "solc-bin")
        seed_solcbin_checkout(self.workdir)

    def args(self):
        return argparse.Namespace(tag=TAG, workdir=self.workdir, dry_run=False)

    def build_run(self, is_draft):
        table = {tuple(download_argv(self.workdir)): ""}
        for name in ARTIFACT_NAMES:
            table[tuple(gpg_argv(self.workdir, name))] = good_gpg()
        table[tuple(pr_list_argv(SOLCBIN_REPO, BRANCH))] = "[]\n"
        table[tuple(clone_argv(SOLCBIN_REPO, self.checkout))] = ""
        table[tuple(checkout_branch_argv(self.checkout, BRANCH))] = ""
        table[tuple(add_argv(self.checkout))] = ""
        table[tuple(commit_argv(self.checkout, VERSION))] = ""
        table[tuple(push_argv(self.checkout, BRANCH))] = ""
        table[tuple(pr_create_argv(SOLCBIN_REPO, BRANCH, VERSION))] = (
            "https://github.com/tronprotocol/solc-bin/pull/77\n"
        )
        table[tuple(release_view_argv(TAG, REPO))] = json.dumps({
            "tagName": TAG, "isDraft": is_draft, "assets": [], "targetCommitish": COMMIT,
        })
        return FakeRun(table)

    def test_g7_failure_prevents_mailer_send(self):
        run = self.build_run(is_draft=False)
        with mock.patch.object(mailer, "send") as mocked_send:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(GateError, "G7"):
                    cli.cmd_apply(self.args(), run=run, repo_root=self.repo_root)

        mocked_send.assert_not_called()
        # The PR really was opened before G7 raised -- proving G7 runs
        # *after* the PR exists, not merely "before mail" in isolation.
        self.assertIn(pr_create_argv(SOLCBIN_REPO, BRANCH, VERSION), run.calls)

    def test_g7_success_reaches_mailer_send(self):
        run = self.build_run(is_draft=True)
        with mock.patch.dict(os.environ, mail_env()):
            with mock.patch.object(mailer, "send") as mocked_send:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = cli.cmd_apply(self.args(), run=run, repo_root=self.repo_root)

        self.assertEqual(result, 0)
        mocked_send.assert_called_once()
        sent_message = mocked_send.call_args[0][0]
        self.assertEqual(sent_message["Subject"], mailer.render_subject(self.man))

    def test_missing_is_draft_field_raises_value_error_not_keyerror(self):
        """`cmd_apply` does `gates.g7_ready_to_mail(release["isDraft"], pr_url)`
        with no validation of its own -- `github.release_view` is what must
        catch a missing field (see `TestReleaseView` in
        test_release_github.py) before it becomes a bare `KeyError` deep
        inside the apply stage. This proves the `ValueError` actually
        propagates all the way out of `cmd_apply` end-to-end, not merely out
        of `release_view` in isolation, and that it happens instead of a
        `KeyError` -- the same fail-closed defect class already fixed
        repeatedly elsewhere in this project (`load_config`, G2, G4, G5).
        """
        run = self.build_run(is_draft=True)
        # Overwrite just the release-view response with one missing isDraft;
        # everything up to that call (download, G5, G6, the solc-bin PR,
        # pack_tgz) must still run for real against the faked commands above.
        run.table[tuple(release_view_argv(TAG, REPO))] = json.dumps({
            "tagName": TAG, "assets": [], "targetCommitish": COMMIT,
        })

        with mock.patch.object(mailer, "send") as mocked_send:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "isDraft"):
                    cli.cmd_apply(self.args(), run=run, repo_root=self.repo_root)

        mocked_send.assert_not_called()
        # The PR was still opened before release_view raised -- proving this
        # is caught at the G7-adjacent release_view call, not earlier.
        self.assertIn(pr_create_argv(SOLCBIN_REPO, BRANCH, VERSION), run.calls)


class TestMainRegistersApply(unittest.TestCase):
    def test_parses_tag_workdir_and_dry_run(self):
        with mock.patch.object(cli, "cmd_apply", return_value=0) as mocked:
            result = cli.main(["apply", TAG, "--workdir", "/tmp/example", "--dry-run"])
        self.assertEqual(result, 0)
        mocked.assert_called_once()
        args = mocked.call_args[0][0]
        self.assertEqual(args.tag, TAG)
        self.assertEqual(args.workdir, "/tmp/example")
        self.assertTrue(args.dry_run)

    def test_defaults_workdir_to_none_and_dry_run_to_false(self):
        with mock.patch.object(cli, "cmd_apply", return_value=0) as mocked:
            cli.main(["apply", TAG])
        args = mocked.call_args[0][0]
        self.assertIsNone(args.workdir)
        self.assertFalse(args.dry_run)


if __name__ == "__main__":
    unittest.main()
