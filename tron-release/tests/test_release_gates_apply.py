#!/usr/bin/env python

import os
import shutil
import tempfile
import unittest
from unittest import mock

# pragma pylint: disable=import-error
from release import gates, manifest
from release.errors import GateError
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

FINGERPRINT = "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5"
CONFIG = {"signingKeyFingerprint": FINGERPRINT}

# The signing *subkey* fingerprint -- VALIDSIG's first field (right after the
# tag), never its last. Pinning this instead of the primary key fingerprint
# must be rejected: see TestG5.test_rejects_pinned_fingerprint_that_is_the_subkey.
SUBKEY_FINGERPRINT = "1254F859D2B1BD9F66E7107DF859BCB44A28290B"

# Real `gpg --status-fd 1 --verify solc-macos.sig solc-macos 2>/dev/null` output,
# captured against the published tv_0.8.27 solc-macos/solc-macos.sig. With
# --status-fd 1, gpg's stdout carries *only* machine-readable "[GNUPG:] ..."
# lines -- the human-readable "Primary key fingerprint: ..." line that plain
# `gpg --verify` prints goes to stderr, which cli.run() never returns.
GOOD_GPG = (
    "[GNUPG:] NEWSIG\n"
    "[GNUPG:] KEY_CONSIDERED 07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5 0\n"
    "[GNUPG:] SIG_ID /Mh/tJVKBFF6Ks92yyyF1JH0s3U 2026-05-27 1779873591\n"
    "[GNUPG:] GOODSIG F859BCB44A28290B build_tron <build@tron.network>\n"
    "[GNUPG:] VALIDSIG 1254F859D2B1BD9F66E7107DF859BCB44A28290B 2026-05-27 "
    "1779873591 0 4 0 1 8 00 07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5\n"
    "[GNUPG:] TRUST_UNDEFINED 0 pgp\n"
)

# Same signature, but VALIDSIG's *last* field (the primary key fingerprint)
# now points at a different key; GOODSIG (which names the signing subkey) is
# unchanged. `gpg --verify` exits 0 for this -- a good signature from an
# untrusted/wrong key still returns 0 -- so G5 must catch it from VALIDSIG's
# last field alone, not from the exit code.
WRONG_KEY_GPG = GOOD_GPG.replace(
    "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5\n",
    "DEADBEEFAEA4E006BD9A42DE785FB96D2C7C3CA5\n",
)

NO_GOODSIG_GPG = "".join(
    line for line in GOOD_GPG.splitlines(keepends=True) if "GOODSIG" not in line
)

NO_VALIDSIG_GPG = "".join(
    line for line in GOOD_GPG.splitlines(keepends=True) if "VALIDSIG" not in line
)

DOUBLE_VALIDSIG_GPG = GOOD_GPG + (
    "[GNUPG:] VALIDSIG 1254F859D2B1BD9F66E7107DF859BCB44A28290B 2026-05-27 "
    "1779873591 0 4 0 1 8 00 07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5\n"
)


class TestG5(unittest.TestCase):
    def make_run(self, output):
        return FakeRun({
            ("gpg", "--status-fd", "1", "--verify",
             "/tmp/a/solc-macos.sig", "/tmp/a/solc-macos"): output,
        })

    def test_accepts_good_signature_from_pinned_key(self):
        gates.g5_signatures(self.make_run(GOOD_GPG), CONFIG, "/tmp/a", ["solc-macos"])

    def test_rejects_signature_from_another_key(self):
        with self.assertRaisesRegex(GateError, "fingerprint"):
            gates.g5_signatures(self.make_run(WRONG_KEY_GPG), CONFIG, "/tmp/a",
                                 ["solc-macos"])

    def test_rejects_pinned_fingerprint_that_is_the_subkey(self):
        """VALIDSIG's *last* field is the primary key fingerprint; its
        *first* field (right after the tag) is the signing subkey --
        1254F859D2B1BD9F66E7107DF859BCB44A28290B in GOOD_GPG, whose short id
        F859BCB44A28290B is exactly what GOODSIG names. A config that pins
        the subkey fingerprint instead of the primary key must still be
        rejected: it is a real fingerprint gpg reports for this signature,
        just not the one release-config.json is supposed to pin against.

        This is the regression this test exists to catch: a refactor that
        changed `_validsig_primary_fingerprint` from `line.split()[-1]` to
        `line.split()[2]` -- the subkey's real index, since `[0]` is the
        `[GNUPG:]` tag and `[1]` is the literal `VALIDSIG` -- would make G5
        accept a signature from *any* subkey of *any* key whose fingerprint
        happens to be pinned, silently. Nothing else in this suite pins the
        subkey, so nothing else would catch that."""
        subkey_config = {"signingKeyFingerprint": SUBKEY_FINGERPRINT}
        with self.assertRaisesRegex(GateError, "fingerprint"):
            gates.g5_signatures(self.make_run(GOOD_GPG), subkey_config, "/tmp/a",
                                 ["solc-macos"])

    def test_rejects_when_gpg_fails(self):
        run = FakeRun({
            ("gpg", "--status-fd", "1", "--verify", "/tmp/a/solc-macos.sig",
             "/tmp/a/solc-macos"): RuntimeError("BAD signature"),
        })
        with self.assertRaisesRegex(GateError, "solc-macos"):
            gates.g5_signatures(run, CONFIG, "/tmp/a", ["solc-macos"])

    def test_rejects_missing_goodsig(self):
        with self.assertRaisesRegex(GateError, "GOODSIG"):
            gates.g5_signatures(self.make_run(NO_GOODSIG_GPG), CONFIG, "/tmp/a",
                                 ["solc-macos"])

    def test_rejects_missing_validsig(self):
        with self.assertRaisesRegex(GateError, "VALIDSIG"):
            gates.g5_signatures(self.make_run(NO_VALIDSIG_GPG), CONFIG, "/tmp/a",
                                 ["solc-macos"])

    def test_rejects_multiple_validsig_lines(self):
        """Two VALIDSIG lines must never be resolved by silently taking the
        first one -- that would defeat the entire point of the check."""
        with self.assertRaisesRegex(GateError, "VALIDSIG"):
            gates.g5_signatures(self.make_run(DOUBLE_VALIDSIG_GPG), CONFIG, "/tmp/a",
                                 ["solc-macos"])

    def test_pins_fingerprint_case_insensitively_and_ignoring_spaces(self):
        """release-config.json may be hand-edited with the spaced, lowercase
        format `gpg --fingerprint` prints for humans to read and copy-paste."""
        spaced_config = {
            "signingKeyFingerprint": "07b2 3298 aea4 e006 bd9a  42de 785f b96d 2c7c 3ca5"
        }
        gates.g5_signatures(self.make_run(GOOD_GPG), spaced_config, "/tmp/a", ["solc-macos"])

    def test_checks_every_artifact_and_names_the_failing_one(self):
        run = FakeRun({
            ("gpg", "--status-fd", "1", "--verify", "/tmp/a/solc-macos.sig",
             "/tmp/a/solc-macos"): GOOD_GPG,
            ("gpg", "--status-fd", "1", "--verify", "/tmp/a/soljson.js.sig",
             "/tmp/a/soljson.js"): WRONG_KEY_GPG,
        })
        with self.assertRaisesRegex(GateError, "soljson.js"):
            gates.g5_signatures(run, CONFIG, "/tmp/a", ["solc-macos", "soljson.js"])

    def test_rejects_config_missing_signing_key_fingerprint(self):
        """g5_signatures is unit-tested as a standalone function taking an
        arbitrary `config: dict` -- a config lacking 'signingKeyFingerprint'
        must raise GateError naming the missing key, not a bare KeyError.
        Same defect class already fixed elsewhere in this file (e.g.
        g4_version_evidence's `if name not in evidence_by_name` guard)."""
        with self.assertRaisesRegex(GateError, "signingKeyFingerprint"):
            gates.g5_signatures(self.make_run(GOOD_GPG), {}, "/tmp/a", ["solc-macos"])


class TestValidsigPrimaryFingerprint(unittest.TestCase):
    """Direct unit tests for `_validsig_primary_fingerprint`, independent of
    `g5_signatures`'s plumbing -- the "last field is the primary key, first
    field is the subkey" contract must be provable from the helper alone,
    not just inferred from g5_signatures's end-to-end behavior."""

    # pylint: disable=protected-access

    LINE = (
        "[GNUPG:] VALIDSIG 1254F859D2B1BD9F66E7107DF859BCB44A28290B 2026-05-27 "
        "1779873591 0 4 0 1 8 00 07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5"
    )

    def test_returns_last_field(self):
        self.assertEqual(
            gates._validsig_primary_fingerprint(self.LINE),
            "07B23298AEA4E006BD9A42DE785FB96D2C7C3CA5",
        )

    def test_does_not_return_first_field(self):
        """The first field (right after the VALIDSIG tag) is the signing
        subkey, not the primary key -- it must never be what's returned."""
        result = gates._validsig_primary_fingerprint(self.LINE)
        self.assertNotEqual(result, "1254F859D2B1BD9F66E7107DF859BCB44A28290B")


HELLO_SHA256 = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


class TestG6(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "wb") as handle:
            handle.write(data)

    def write_all_as_hello(self):
        for name, _, _ in manifest.ARTIFACT_SPECS:
            self.write(name, b"hello")

    def make_manifest(self, digests):
        return manifest.Manifest(
            version="0.8.27", tag="tv_0.8.27", codename="x", commit="1" * 40,
            tested_commit="c" * 40, qa_approval={}, prs={},
            artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
        ).with_digests(digests)

    def matching_digests(self):
        return {name: {"sha256": HELLO_SHA256, "keccak256": "0" * 64}
                for name, _, _ in manifest.ARTIFACT_SPECS}

    def test_accepts_when_all_digests_match(self):
        self.write_all_as_hello()
        gates.g6_asset_digests(self.dir, self.make_manifest(self.matching_digests()))

    def test_rejects_replaced_asset(self):
        self.write_all_as_hello()
        self.write("soljson.js", b"tampered")
        with self.assertRaisesRegex(GateError, "soljson.js"):
            gates.g6_asset_digests(self.dir, self.make_manifest(self.matching_digests()))

    def test_rejects_missing_asset(self):
        self.write_all_as_hello()
        os.unlink(os.path.join(self.dir, "solc-windows.exe"))
        with self.assertRaisesRegex(GateError, "solc-windows.exe"):
            gates.g6_asset_digests(self.dir, self.make_manifest(self.matching_digests()))

    def test_rejects_manifest_artifact_with_no_sha256(self):
        """A manifest artifact with sha256=None (never digested, or a
        hand-edited/corrupt manifest.json) must fail this gate, not vacuously
        pass it or raise a bare TypeError comparing None to a hex string."""
        self.write_all_as_hello()
        man = manifest.Manifest(
            version="0.8.27", tag="tv_0.8.27", codename="x", commit="1" * 40,
            tested_commit="c" * 40, qa_approval={}, prs={},
            artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
        )  # no with_digests -- every artifact's sha256/keccak256 stays None
        with self.assertRaisesRegex(GateError, "solc-macos"):
            gates.g6_asset_digests(self.dir, man)

    def test_uses_sha256_not_digests(self):
        """G6 must call checksums.sha256 (stdlib-only), never
        checksums.digests (which lazily imports pycryptodome the moment it
        runs) -- computing keccak256 here would be pure waste and would
        needlessly require pycryptodome on the apply-stage host. Patching
        checksums.digests to explode proves g6 never calls it."""
        self.write_all_as_hello()

        def explode(_path):
            raise AssertionError("g6_asset_digests must not call checksums.digests")

        with mock.patch("release.checksums.digests", side_effect=explode):
            gates.g6_asset_digests(self.dir, self.make_manifest(self.matching_digests()))


if __name__ == "__main__":
    unittest.main()
