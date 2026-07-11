#!/usr/bin/env python

import os
import re
import shutil
import tarfile
import tempfile
import unittest

# pragma pylint: disable=import-error
from release import bundle, manifest
from release.errors import GateError
# pragma pylint: enable=import-error

VERSION = "0.8.27"
TAG = f"tv_{VERSION}"
CODENAME = "Democritus_v4.8.1"

DIGESTS = {
    "solc-macos": {"sha256": "a" * 64, "keccak256": "b" * 64},
    "solc-static-linux": {"sha256": "c" * 64, "keccak256": "d" * 64},
    "solc-windows.exe": {"sha256": "e" * 64, "keccak256": "f" * 64},
    "soljson.js": {"sha256": "0" * 64, "keccak256": "1" * 64},
}

ARTIFACT_NAMES = [name for name, _, _ in manifest.ARTIFACT_SPECS]


def make_manifest():
    man = manifest.Manifest(
        version=VERSION, tag=TAG, codename=CODENAME,
        commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
        tested_commit="c" * 40, qa_approval={}, prs={},
        artifacts=manifest.build_artifacts(f"{VERSION}+commit.19164bed"),
    )
    return man.with_digests(DIGESTS)


def write_notes(repo_root, version, codename):
    os.makedirs(os.path.join(repo_root, "release-notes"), exist_ok=True)
    text = (
        "---\n"
        f"version: {version}\n"
        f"codename: {codename}\n"
        "testReport: https://example.test/report\n"
        "---\n"
        f"TRON Solidity compiler {version} is fully compatible with "
        f"Ethereum Solidity {version}.\n"
        "\n"
        "Fixture release notes body for pack_tgz tests.\n"
    )
    with open(os.path.join(repo_root, "release-notes", f"tv_{version}.md"),
              "w", encoding="utf-8") as handle:
        handle.write(text)


class TestPackTgz(unittest.TestCase):
    """`pack_tgz` must fail closed, naming every missing artifact/signature/
    sums file at once, and must never silently produce an attachment the
    request email's "见附件" claims are complete but are not.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="test-release-bundle-workdir-")
        self.repo_root = tempfile.mkdtemp(prefix="test-release-bundle-root-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.repo_root, ignore_errors=True)
        self.man = make_manifest()
        write_notes(self.repo_root, VERSION, CODENAME)

    def write_all_artifacts(self):
        for name in ARTIFACT_NAMES:
            with open(os.path.join(self.workdir, name), "wb") as handle:
                handle.write(f"fake-{name}-bytes".encode())
            with open(os.path.join(self.workdir, name + ".sig"), "wb") as handle:
                handle.write(f"fake-{name}-sig-bytes".encode())
        with open(os.path.join(self.workdir, "shasum.txt"), "w", encoding="utf-8") as handle:
            handle.write("fake shasum contents\n")
        with open(os.path.join(self.workdir, "keccak256.txt"), "w", encoding="utf-8") as handle:
            handle.write("fake keccak256 contents\n")

    def test_raises_when_a_binary_is_missing(self):
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "solc-macos"))
        with self.assertRaisesRegex(GateError, "solc-macos"):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)

    def test_raises_when_a_signature_is_missing(self):
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "soljson.js.sig"))
        with self.assertRaisesRegex(GateError, re.escape("soljson.js.sig")):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)

    def test_raises_when_shasum_is_missing(self):
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "shasum.txt"))
        with self.assertRaisesRegex(GateError, "shasum.txt"):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)

    def test_raises_when_keccak256_sums_is_missing(self):
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "keccak256.txt"))
        with self.assertRaisesRegex(GateError, "keccak256.txt"):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)

    def test_raises_when_release_notes_missing(self):
        """A missing release-notes file must be reported as a GateError
        alongside any other missing item, not as a bare FileNotFoundError
        thrown later while copying it into the bundle.
        """
        self.write_all_artifacts()
        os.unlink(os.path.join(self.repo_root, "release-notes", f"{TAG}.md"))
        with self.assertRaisesRegex(GateError, re.escape(f"{TAG}.md")):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)

    def test_names_every_missing_item_at_once(self):
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "solc-macos"))
        os.unlink(os.path.join(self.workdir, "solc-windows.exe.sig"))
        os.unlink(os.path.join(self.workdir, "shasum.txt"))
        os.unlink(os.path.join(self.repo_root, "release-notes", f"{TAG}.md"))
        with self.assertRaises(GateError) as ctx:
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        message = str(ctx.exception)
        self.assertIn("solc-macos", message)
        self.assertIn("solc-windows.exe.sig", message)
        self.assertIn("shasum.txt", message)
        self.assertIn(f"{TAG}.md", message)

    def test_does_not_write_a_tgz_when_something_is_missing(self):
        """A failed pack must not leave a partial/misleading tgz behind."""
        self.write_all_artifacts()
        os.unlink(os.path.join(self.workdir, "soljson.js"))
        with self.assertRaises(GateError):
            bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        self.assertFalse(os.path.exists(os.path.join(self.workdir, f"{stem}.tgz")))

    def test_produced_tgz_is_named_after_the_stem(self):
        self.write_all_artifacts()
        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        self.assertEqual(os.path.basename(out), f"{bundle.bundle_stem(VERSION)}.tgz")
        self.assertEqual(os.path.dirname(out), self.workdir)

    def test_tgz_contains_every_binary_signature_and_sums_file_under_the_stem(self):
        self.write_all_artifacts()
        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())
        expected = {stem} | {
            f"{stem}/{member}" for member in
            ARTIFACT_NAMES + [n + ".sig" for n in ARTIFACT_NAMES]
            + ["shasum.txt", "keccak256.txt", "ReleaseNotes.md"]
        }
        self.assertEqual(names, expected)

    def test_tgz_binary_contents_match_the_downloaded_bytes_exactly(self):
        self.write_all_artifacts()
        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        with tarfile.open(out, "r:gz") as tar:
            member = tar.extractfile(f"{stem}/solc-macos")
            self.assertEqual(member.read(), b"fake-solc-macos-bytes")

    def test_tgz_release_notes_match_the_source_file_exactly(self):
        self.write_all_artifacts()
        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        with open(os.path.join(self.repo_root, "release-notes", f"{TAG}.md"),
                   "r", encoding="utf-8") as handle:
            expected = handle.read()
        with tarfile.open(out, "r:gz") as tar:
            member = tar.extractfile(f"{stem}/ReleaseNotes.md")
            self.assertEqual(member.read().decode("utf-8"), expected)

    def test_omits_source_tarball_when_not_present(self):
        self.write_all_artifacts()
        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        self.assertFalse(any(n.endswith(".tar.gz") for n in names), names)
        self.assertNotIn(f"{stem}/solidity_{VERSION}.tar.gz", names)

    def test_includes_source_tarball_when_present(self):
        self.write_all_artifacts()
        upload_dir = os.path.join(self.repo_root, "upload")
        os.makedirs(upload_dir, exist_ok=True)
        tarball_name = f"solidity_{VERSION}.tar.gz"
        with open(os.path.join(upload_dir, tarball_name), "wb") as handle:
            handle.write(b"fake source tarball bytes")

        out = bundle.pack_tgz(self.man, self.workdir, self.repo_root)
        stem = bundle.bundle_stem(VERSION)
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn(f"{stem}/{tarball_name}", names)
            member = tar.extractfile(f"{stem}/{tarball_name}")
            self.assertEqual(member.read(), b"fake source tarball bytes")


if __name__ == "__main__":
    unittest.main()
