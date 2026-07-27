#!/usr/bin/env python

import hashlib
import tempfile
import unittest

# pragma pylint: disable=import-error
from release import checksums, manifest
# pragma pylint: enable=import-error


# Published truth for tv_0.8.27.
TRUTH = {
    "solc-macos": {
        "sha256": "9e369b442b3a835320cf1450a21ce5e8acf0d319a50d10a10a2001aac7ce17aa",
        "keccak256": "7e2edbae005ad04161fdc99c66cce32b9adc12534991de8cf0978129da4de612",
    },
    "solc-static-linux": {
        "sha256": "7a3dfc3d987bbe447c0eba89e5fd89a1fc053e8629799e167810367196a3281e",
        "keccak256": "adf2934186d27983f2e0758f8a99b2f6ae369b752bf01ebb4e99ed1186073106",
    },
    "solc-static-linux-arm": {
        "sha256": "1" * 64,
        "keccak256": "2" * 64,
    },
    "solc-windows.exe": {
        "sha256": "6c387a01ac81ad044c0f37816c059828c1080cc38f5882fd1f08d58f990ac7e0",
        "keccak256": "f9cf52300f8f34e25ba98a203c8f8501f5d2cb9dc9628ba4c49e1376b3463de4",
    },
    "soljson.js": {
        "sha256": "4ba06db3c974d2f495f84c740134313fd0a7b782876cbfe7bdd64fbbab469154",
        "keccak256": "68638be589fba4aff486a07280d09c0367353b97a6b5fd8e1487899eeda34bc5",
    },
}

EXPECTED_SHASUM_TXT = (
    "9e369b442b3a835320cf1450a21ce5e8acf0d319a50d10a10a2001aac7ce17aa  solc-macos\n"
    "7a3dfc3d987bbe447c0eba89e5fd89a1fc053e8629799e167810367196a3281e  solc-static-linux\n"
    + "1" * 64 + "  solc-static-linux-arm\n"
    + "6c387a01ac81ad044c0f37816c059828c1080cc38f5882fd1f08d58f990ac7e0  solc-windows.exe\n"
    + "4ba06db3c974d2f495f84c740134313fd0a7b782876cbfe7bdd64fbbab469154  soljson.js\n"
)

EXPECTED_KECCAK_TXT = (
    "7e2edbae005ad04161fdc99c66cce32b9adc12534991de8cf0978129da4de612  solc-macos\n"
    "adf2934186d27983f2e0758f8a99b2f6ae369b752bf01ebb4e99ed1186073106  solc-static-linux\n"
    + "2" * 64 + "  solc-static-linux-arm\n"
    + "f9cf52300f8f34e25ba98a203c8f8501f5d2cb9dc9628ba4c49e1376b3463de4  solc-windows.exe\n"
    + "68638be589fba4aff486a07280d09c0367353b97a6b5fd8e1487899eeda34bc5  soljson.js\n"
)

# Known-answer test vectors: keccak256("") and sha256("").
EMPTY_KECCAK = "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Known-answer vector for a *non-empty* input, so test_keccak_is_not_sha3 does not depend
# solely on the empty-input vectors above. Independently verified by direct computation with
# both Crypto.Hash.keccak and hashlib.sha3_256:
#   keccak256(_NONEMPTY_INPUT) == c0001cfb23979c79cd7d360490b8a75ca9966e333c38581f7becb77224e56763
#   sha3_256(_NONEMPTY_INPUT)  == cf763d5743162645f65fa8b55d747b2632701b9606d138c47710b78a9a1c871d
_NONEMPTY_INPUT = b"tron-release-automation-task5-nonempty-vector"
_NONEMPTY_KECCAK = "c0001cfb23979c79cd7d360490b8a75ca9966e333c38581f7becb77224e56763"


class TestDigests(unittest.TestCase):
    def test_empty_file_matches_known_vectors(self):
        with tempfile.NamedTemporaryFile() as handle:
            result = checksums.digests(handle.name)
        self.assertEqual(result["keccak256"], EMPTY_KECCAK)
        self.assertEqual(result["sha256"], EMPTY_SHA256)

    def test_keccak_is_not_sha3(self):
        """Guards against `checksums.digests` being "simplified" from Keccak-256 to SHA3-256.

        `keccak.new(digest_bits=256)` (pycryptodome) is Keccak-256 (Ethereum/TRON), not NIST
        SHA3-256 -- they produce completely different digests for the same input. This must
        drive the real `checksums.digests()` on actual files (not compare two hardcoded string
        literals) so that a future regression -- replacing the Keccak-256 call with
        `hashlib.sha3_256` -- actually fails here instead of passing silently.
        """
        with tempfile.NamedTemporaryFile() as handle:
            empty_result = checksums.digests(handle.name)
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(_NONEMPTY_INPUT)
            handle.flush()
            nonempty_result = checksums.digests(handle.name)

        # Positive: matches the known Keccak-256 vector, for both an empty and a non-empty input.
        self.assertEqual(empty_result["keccak256"], EMPTY_KECCAK)
        self.assertEqual(nonempty_result["keccak256"], _NONEMPTY_KECCAK)

        # Negative: does not match hashlib's NIST SHA3-256 of the same bytes.
        self.assertNotEqual(empty_result["keccak256"], hashlib.sha3_256(b"").hexdigest())
        self.assertNotEqual(
            nonempty_result["keccak256"], hashlib.sha3_256(_NONEMPTY_INPUT).hexdigest()
        )

    def test_no_0x_prefix(self):
        with tempfile.NamedTemporaryFile() as handle:
            result = checksums.digests(handle.name)
        self.assertFalse(result["sha256"].startswith("0x"))
        self.assertFalse(result["keccak256"].startswith("0x"))


class TestSha256(unittest.TestCase):
    """`checksums.sha256` is what `cli.verify_downloaded` calls -- stdlib
    `hashlib` only, so `tron_release sign` never needs pycryptodome. It must
    agree with `digests()["sha256"]` exactly, for both an empty and a
    non-empty file.
    """

    def test_empty_file_matches_known_vector(self):
        with tempfile.NamedTemporaryFile() as handle:
            result = checksums.sha256(handle.name)
        self.assertEqual(result, EMPTY_SHA256)

    def test_empty_file_agrees_with_digests(self):
        with tempfile.NamedTemporaryFile() as handle:
            result = checksums.sha256(handle.name)
            expected = checksums.digests(handle.name)["sha256"]
        self.assertEqual(result, expected)

    def test_nonempty_file_agrees_with_digests(self):
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(_NONEMPTY_INPUT)
            handle.flush()
            result = checksums.sha256(handle.name)
            expected = checksums.digests(handle.name)["sha256"]
        self.assertEqual(result, expected)

    def test_no_0x_prefix(self):
        with tempfile.NamedTemporaryFile() as handle:
            result = checksums.sha256(handle.name)
        self.assertFalse(result.startswith("0x"))


def make_manifest():
    man = manifest.Manifest(
        version="0.8.27", tag="tv_0.8.27", codename="Democritus_v4.8.1",
        commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
        tested_commit="c7c21da02b4a8e82a2abbd9228d7c5969f700621",
        qa_approval={}, prs={"upstreamMerge": [114], "features": [115]},
        artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
    )
    return man.with_digests(TRUTH)


class TestRender(unittest.TestCase):
    def test_shasum_txt_matches_five_artifact_projection(self):
        self.assertEqual(checksums.render(make_manifest(), "sha256"), EXPECTED_SHASUM_TXT)

    def test_keccak256_txt_matches_five_artifact_projection(self):
        self.assertEqual(checksums.render(make_manifest(), "keccak256"), EXPECTED_KECCAK_TXT)

    def test_rejects_unknown_algorithm(self):
        with self.assertRaisesRegex(ValueError, "md5"):
            checksums.render(make_manifest(), "md5")

    def test_rejects_manifest_without_digests(self):
        man = manifest.Manifest(
            version="0.8.27", tag="tv_0.8.27", codename="x", commit="19164bed" * 5,
            tested_commit="c" * 40, qa_approval={}, prs={},
            artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
        )
        with self.assertRaisesRegex(ValueError, "solc-macos"):
            checksums.render(man, "sha256")


if __name__ == "__main__":
    unittest.main()
