#!/usr/bin/env python

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

# pragma pylint: disable=import-error
from release import checksums, cli, manifest
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

REPO = "acme/widget"
TAG = "tv_1.2.3"
FINGERPRINT = "DEADBEEF"
COMMIT = "cafef00d1234567890abcdef1234567890abcdef"

ARTIFACT_NAMES = [name for name, _, _ in ARTIFACT_SPECS]


def write_config(root, repo=REPO, fingerprint=FINGERPRINT):
    os.makedirs(os.path.join(root, "tron-release"), exist_ok=True)
    config = {
        "repo": repo,
        "solcBinRepo": f"{repo}-bin",
        "signingKeyFingerprint": fingerprint,
        "qaReviewers": [],
    }
    path = os.path.join(root, "tron-release", "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)


def download_argv(workdir, tag=TAG, repo=REPO):
    patterns = []
    for name in ARTIFACT_NAMES + ["shasum.txt", "manifest.json"]:
        patterns += ["-p", name]
    return ["gh", "release", "download", tag, "--repo", repo,
            "-D", workdir, "--clobber"] + patterns


def gpg_argv(path, fingerprint=FINGERPRINT):
    return ["gpg", "--armor", "--detach-sign", "--local-user", fingerprint,
            "--output", path + ".sig", path]


def upload_argv(sig_paths, public_key, tag=TAG, repo=REPO):
    return (["gh", "release", "upload", tag, "--repo", repo, "--clobber"]
            + sig_paths + [public_key])


class TestVerifyDownloaded(unittest.TestCase):
    """`verify_downloaded` is what lets the signing-key holder refuse to just
    trust that CI produced the bytes it claims -- it must fail closed on
    anything shasum.txt might contain besides a clean, complete description
    of exactly the configured release artifacts.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.contents = {name: f"fake-{name}-bytes".encode() for name in ARTIFACT_NAMES}
        for name, data in self.contents.items():
            with open(os.path.join(self.dir, name), "wb") as handle:
                handle.write(data)

    def digest(self, name):
        return hashlib.sha256(self.contents[name]).hexdigest()

    def line(self, name, digest=None):
        return f"{digest if digest is not None else self.digest(name)}  {name}"

    def full_shasum_text(self, skip=()):
        lines = [self.line(name) for name in ARTIFACT_NAMES if name not in skip]
        return "\n".join(lines) + "\n"

    def test_accepts_matching_digests_for_all_artifacts(self):
        cli.verify_downloaded(self.dir, self.full_shasum_text())

    def test_rejects_mismatched_digest(self):
        text = self.full_shasum_text(skip=("solc-macos",))
        text += self.line("solc-macos", digest="0" * 64) + "\n"
        with self.assertRaisesRegex(GateError, "solc-macos"):
            cli.verify_downloaded(self.dir, text)

    def test_rejects_missing_file(self):
        os.unlink(os.path.join(self.dir, "soljson.js"))
        with self.assertRaisesRegex(GateError, "soljson.js"):
            cli.verify_downloaded(self.dir, self.full_shasum_text())

    def test_rejects_empty_shasum_file(self):
        with self.assertRaisesRegex(GateError, "no entries"):
            cli.verify_downloaded(self.dir, "\n")

    def test_rejects_malformed_line(self):
        """A line separated by one space (not two) must not be silently skipped."""
        lines = [self.line(name) for name in ARTIFACT_NAMES]
        malformed = f"{self.digest('solc-macos')} solc-macos"  # one space, not two
        lines.insert(2, malformed)
        with self.assertRaisesRegex(GateError, "malformed"):
            cli.verify_downloaded(self.dir, "\n".join(lines) + "\n")

    def test_rejects_line_with_three_spaces(self):
        """Three spaces must not be accepted as "two spaces plus a leading-space name"."""
        lines = [self.line(name) for name in ARTIFACT_NAMES]
        malformed = f"{self.digest('solc-macos')}   solc-macos"  # three spaces
        lines.insert(1, malformed)
        with self.assertRaisesRegex(GateError, "malformed"):
            cli.verify_downloaded(self.dir, "\n".join(lines) + "\n")

    def test_rejects_digest_with_wrong_length(self):
        """63 or 65 hex characters -- one short of, or one over, a real
        sha256 digest -- must still be rejected by `_is_sha256_hex`'s length
        check, even though the separator and name are perfectly well-formed.
        """
        for digest in ("a" * 63, "a" * 65):
            with self.subTest(digest_length=len(digest)):
                lines = [self.line(name) for name in ARTIFACT_NAMES]
                malformed = f"{digest}  solc-macos"
                lines.insert(2, malformed)
                with self.assertRaisesRegex(GateError, "malformed"):
                    cli.verify_downloaded(self.dir, "\n".join(lines) + "\n")

    def test_rejects_digest_with_non_hex_character(self):
        """A 64-character digest containing a non-hex character (`g`) must be
        rejected by `_is_sha256_hex`'s charset check, not accepted just
        because the length happens to be right.
        """
        lines = [self.line(name) for name in ARTIFACT_NAMES]
        malformed = f"{'g' * 64}  solc-macos"
        lines.insert(2, malformed)
        with self.assertRaisesRegex(GateError, "malformed"):
            cli.verify_downloaded(self.dir, "\n".join(lines) + "\n")

    def test_rejects_unrecognized_artifact_name(self):
        """A rogue extra line must not cause an unexpected file to be signed."""
        lines = [self.line(name) for name in ARTIFACT_NAMES]
        rogue = f"{'a' * 64}  solc-macos.asc"
        lines.insert(2, rogue)
        with self.assertRaisesRegex(GateError, re.escape("solc-macos.asc")):
            cli.verify_downloaded(self.dir, "\n".join(lines) + "\n")

    def test_rejects_incomplete_coverage(self):
        """Signing fewer than all configured artifacts must not silently succeed."""
        with self.assertRaisesRegex(GateError, "soljson.js"):
            cli.verify_downloaded(self.dir, self.full_shasum_text(skip=("soljson.js",)))

    def test_names_every_missing_artifact_at_once(self):
        with self.assertRaises(GateError) as ctx:
            cli.verify_downloaded(
                self.dir, self.full_shasum_text(skip=("soljson.js", "solc-windows.exe"))
            )
        message = str(ctx.exception)
        self.assertIn("soljson.js", message)
        self.assertIn("solc-windows.exe", message)


class TestCmdSign(unittest.TestCase):
    """`cmd_sign` orchestrates download -> verify -> sign -> upload.

    Every `run` call is faked; no test here spawns a subprocess or invokes
    real `gpg`. The workdir is pre-seeded with all configured artifacts and a
    matching shasum.txt, standing in for what a real `gh release download`
    would have produced.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-release-sign-root-")
        self.workdir = tempfile.mkdtemp(prefix="test-release-sign-workdir-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        write_config(self.root)

        self.contents = {name: f"fake-{name}-bytes".encode() for name in ARTIFACT_NAMES}
        lines = []
        for name, data in self.contents.items():
            with open(os.path.join(self.workdir, name), "wb") as handle:
                handle.write(data)
            lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
        with open(os.path.join(self.workdir, "shasum.txt"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        digests = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "keccak256": "0" * 64}
            for name, data in self.contents.items()
        }
        man = manifest.Manifest(
            version="1.2.3", tag=TAG, codename="Test", commit=COMMIT,
            tested_commit="1" * 40,
            qa_approval={"pr": 1, "reviewer": "qa", "submittedAt": "2026-01-01"},
            prs={"upstreamMerge": [], "features": []},
            artifacts=manifest.build_artifacts("1.2.3+commit.cafef00d"),
        ).with_digests(digests)
        with open(os.path.join(self.workdir, "manifest.json"), "w", encoding="utf-8") as handle:
            handle.write(man.to_json())

        signed_names = ARTIFACT_NAMES + ["manifest.json"]
        self.sig_paths = [os.path.join(self.workdir, name) + ".sig" for name in signed_names]
        self.public_key = os.path.join(self.workdir, "signing-key.asc")

    def args(self, dry_run):
        return argparse.Namespace(tag=TAG, workdir=self.workdir, dry_run=dry_run)

    def build_run(self, include_upload):
        table = {tuple(download_argv(self.workdir)): ""}
        for name in ARTIFACT_NAMES + ["manifest.json"]:
            table[tuple(gpg_argv(os.path.join(self.workdir, name)))] = ""
        table[("gpg", "--batch", "--armor", "--export", FINGERPRINT)] = (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\nfixture\n-----END PGP PUBLIC KEY BLOCK-----\n"
        )
        if include_upload:
            table[tuple(upload_argv(self.sig_paths, self.public_key))] = ""
        return FakeRun(table)

    def test_dry_run_signs_but_does_not_upload(self):
        run = self.build_run(include_upload=False)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_sign(self.args(dry_run=True), run=run, repo_root=self.root)
        self.assertEqual(result, 0)

        upload_calls = [c for c in run.calls if c[:3] == ["gh", "release", "upload"]]
        self.assertEqual(upload_calls, [])
        self.assertIn("[dry-run]", captured.getvalue())
        self.assertIn("match shasum.txt", captured.getvalue())
        for name in ARTIFACT_NAMES + ["manifest.json"]:
            self.assertIn(gpg_argv(os.path.join(self.workdir, name)), run.calls)

    def test_uploads_signatures_when_not_dry_run(self):
        run = self.build_run(include_upload=True)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_sign(self.args(dry_run=False), run=run, repo_root=self.root)
        self.assertEqual(result, 0)
        self.assertIn(upload_argv(self.sig_paths, self.public_key), run.calls)
        self.assertIn(f"uploaded {len(self.sig_paths)} signatures", captured.getvalue())

    def test_explicitly_reuses_complete_workdir_without_downloading(self):
        args = self.args(dry_run=True)
        args.reuse_workdir = True
        run = self.build_run(include_upload=False)
        del run.table[tuple(download_argv(self.workdir))]

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = cli.cmd_sign(args, run=run, repo_root=self.root)

        self.assertEqual(result, 0)
        self.assertFalse(any(call[:3] == ["gh", "release", "download"]
                             for call in run.calls))
        self.assertIn("reusing", captured.getvalue())

    def test_reuse_workdir_fails_closed_when_an_asset_is_missing(self):
        os.unlink(os.path.join(self.workdir, "soljson.js"))
        args = self.args(dry_run=True)
        args.reuse_workdir = True

        with self.assertRaisesRegex(GateError, "soljson.js"):
            cli.cmd_sign(args, run=FakeRun({}), repo_root=self.root)

    def test_removes_stale_signature_before_signing(self):
        stale = self.sig_paths[0]
        with open(stale, "wb") as handle:
            handle.write(b"stale signature bytes")

        run = self.build_run(include_upload=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_sign(self.args(dry_run=False), run=run, repo_root=self.root)

        # FakeRun never actually writes a new .sig -- if cmd_sign had not
        # removed the stale one before calling gpg, it would still be sitting
        # there with its original bytes.
        self.assertFalse(os.path.exists(stale))
        self.assertIn(gpg_argv(os.path.join(self.workdir, ARTIFACT_NAMES[0])), run.calls)

    def test_verification_failure_aborts_before_any_signing(self):
        """If G-sign's shasum check fails, gpg must never be invoked.

        The fake run table below has no gpg/upload entries -- if cmd_sign
        called them anyway, FakeRun would raise AssertionError (unexpected
        command) instead of the expected GateError.
        """
        with open(os.path.join(self.workdir, "shasum.txt"), "w", encoding="utf-8") as handle:
            handle.write(f"{'0' * 64}  {ARTIFACT_NAMES[0]}\n")
        run = FakeRun({tuple(download_argv(self.workdir)): ""})

        with self.assertRaisesRegex(GateError, ARTIFACT_NAMES[0]):
            cli.cmd_sign(self.args(dry_run=False), run=run, repo_root=self.root)
        self.assertEqual(len(run.calls), 1)

    def test_reads_no_aws_pat_or_mail_environment_variables(self):
        """The key holder has none of this pipeline's AWS/PAT/mail secrets.

        Builds an environment with none of them set and confirms cmd_sign
        still completes -- if the code read any of them via require_env, this
        would raise ValueError instead.
        """
        forbidden = (
            "S3_BUCKET_PROD", "S3_BUCKET_TEST",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "RELEASE_PAT", "GH_PAT", "GITHUB_PAT",
            "SMTP_PASSWORD", "MAIL_PASSWORD", "NOTIFY_MAIL_PASSWORD",
        )
        run = self.build_run(include_upload=False)
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in forbidden:
                os.environ.pop(name, None)
            with contextlib.redirect_stdout(io.StringIO()):
                result = cli.cmd_sign(self.args(dry_run=True), run=run, repo_root=self.root)
        self.assertEqual(result, 0)


class TestSignDependencySurface(unittest.TestCase):
    """`cmd_sign` is the one command handed to the signing-key holder, who is
    not a developer on this project and has neither this pipeline's AWS/PAT/
    mail secrets (see `TestCmdSign.test_reads_no_aws_pat_or_mail_environment_variables`
    above) nor `pycryptodome` installed -- `sign` must run with nothing beyond
    `gh` and `gpg`. That is why `checksums.digests()` imports `Crypto.Hash.keccak`
    lazily, inside the function body: a module-level import would make merely
    `import release.cli` require pycryptodome, breaking `sign` before it ever
    reaches `digests()` (which `sign` never calls in the first place).

    This used to be checked with `assertFalse(hasattr(checksums, "keccak"))`,
    which only catches the literal `from Crypto.Hash import keccak` spelling:
    it is blind to `from Crypto.Hash import keccak as _keccak` (binds
    `_keccak`, never `keccak`) and to `import Crypto.Hash.keccak` (binds
    `Crypto`, not `keccak`) -- both still force pycryptodome to be importable
    the moment `checksums` is imported, and both left the old check green. A
    `sys.modules` check is no more reliable: other tests in this same process
    call `digests()` and permanently populate
    `sys.modules["Crypto.Hash.keccak"]`, so that key can be present regardless
    of what `checksums.py` itself imports; subprocess-isolated checks are off
    the table for this project (no test may spawn a subprocess).

    So instead this parses `checksums.py`'s own source with `ast` and
    inspects only its module-level statements (`ast.parse(source).body`, not
    `ast.walk`, which would also descend into function bodies). It asserts
    that none of those top-level statements is an `Import`/`ImportFrom` node
    referencing `Crypto` in any form -- plain, `from X import Y`, or
    aliased -- because it checks the imported *module path*, not whatever
    local name it happens to be bound to. The lazy import inside `digests()`
    is a statement inside a function body, so it never appears in
    `tree.body` and is invisible to this scan, exactly as it should be.
    """

    @staticmethod
    def _module_level_crypto_imports(source, filename="<checksums>"):
        """Return `(lineno, statement_text)` for every module-level import in
        `source` that references `Crypto` -- `import Crypto...`, `from
        Crypto... import ...`, or either aliased with `as`. Only
        `ast.parse(source).body` (the module's direct statements) is
        inspected, so an import nested inside a function or class body is
        never considered, matching the legitimate lazy import in `digests()`.
        """
        tree = ast.parse(source, filename=filename)
        offenders = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                module_paths = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module_paths = [node.module] if node.module else []
            else:
                continue
            references_crypto = any(
                path == "Crypto" or path.startswith("Crypto.") for path in module_paths
            )
            if references_crypto:
                statement = ast.get_source_segment(source, node) or source.splitlines()[node.lineno - 1]
                offenders.append((node.lineno, statement))
        return offenders

    def test_checksums_has_no_module_level_crypto_import(self):
        """`sign` must run on a machine without pycryptodome installed, so
        `checksums.py` may only import `Crypto` lazily, inside a function
        body -- never at module scope.
        """
        source_path = checksums.__file__
        with open(source_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        offenders = self._module_level_crypto_imports(source, filename=source_path)
        details = "; ".join(f"{source_path}:{lineno}: {statement!r}" for lineno, statement in offenders)
        self.assertEqual(offenders, [], f"module-level Crypto import(s) found: {details}")

    def test_detects_aliased_crypto_import(self):
        """Must survive `from Crypto.Hash import keccak as _keccak`: the
        alias binds a different local name, but the module-level dependency
        on pycryptodome is identical to the unaliased form.
        """
        offenders = self._module_level_crypto_imports("from Crypto.Hash import keccak as _keccak\n")
        self.assertEqual(len(offenders), 1)

    def test_detects_plain_submodule_import(self):
        """Must survive `import Crypto.Hash.keccak`: no `from`, no alias,
        still a module-level dependency on pycryptodome.
        """
        offenders = self._module_level_crypto_imports("import Crypto.Hash.keccak\n")
        self.assertEqual(len(offenders), 1)

    def test_detects_from_crypto_import_hash(self):
        """Must survive `from Crypto import Hash`: the imported path is
        exactly `Crypto`, with no dotted submodule, so the check must not
        rely on `str.startswith("Crypto.")` alone.
        """
        offenders = self._module_level_crypto_imports("from Crypto import Hash\n")
        self.assertEqual(len(offenders), 1)

    def test_ignores_lazy_import_inside_function_body(self):
        """Must not flag the legitimate lazy import inside `digests()` --
        nested one level inside a function, exactly like the real
        `checksums.py`.
        """
        source = (
            "def digests(path):\n"
            "    from Crypto.Hash import keccak\n"
            "    return keccak.new(digest_bits=256)\n"
        )
        offenders = self._module_level_crypto_imports(source)
        self.assertEqual(offenders, [])


class TestMainRegistersSign(unittest.TestCase):
    def test_parses_tag_workdir_and_dry_run(self):
        with mock.patch.object(cli, "cmd_sign", return_value=0) as mocked:
            result = cli.main(["sign", TAG, "--workdir", "/tmp/example", "--dry-run"])
        self.assertEqual(result, 0)
        mocked.assert_called_once()
        args = mocked.call_args[0][0]
        self.assertEqual(args.tag, TAG)
        self.assertEqual(args.workdir, "/tmp/example")
        self.assertTrue(args.dry_run)

    def test_defaults_workdir_to_none_and_dry_run_to_false(self):
        with mock.patch.object(cli, "cmd_sign", return_value=0) as mocked:
            cli.main(["sign", TAG])
        args = mocked.call_args[0][0]
        self.assertIsNone(args.workdir)
        self.assertFalse(args.dry_run)


if __name__ == "__main__":
    unittest.main()
