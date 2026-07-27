#!/usr/bin/env python

import os
import unittest

# pragma pylint: disable=import-error
from release import evidence, gates
from release.errors import GateError
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "release")
LONG = "0.8.27+commit.19164bed"
SHORT = "19164bed"


class TestExecutableEvidence(unittest.TestCase):
    def test_linux_binary_is_executed(self):
        run = FakeRun({
            ("/tmp/solc-static-linux", "--version"):
                "solc, the solidity compiler commandline interface\n"
                "Version: 0.8.27+commit.19164bed.Linux.g++\n",
        })
        self.assertEqual(
            evidence.extract(run, "solc-static-linux", "/tmp/solc-static-linux"),
            "0.8.27+commit.19164bed.Linux.g++",
        )

    def test_soljson_is_evaluated_with_node(self):
        run = FakeRun({
            ("node", "-e",
             "process.stdout.write(require('/tmp/soljson.js')"
             ".cwrap('solidity_version','string',[])())"):
                "0.8.27+commit.19164bed.Emscripten.clang",
        })
        self.assertEqual(evidence.extract(run, "soljson.js", "/tmp/soljson.js"),
                         "0.8.27+commit.19164bed.Emscripten.clang")

    def test_raises_when_version_line_missing(self):
        run = FakeRun({("/tmp/solc-static-linux", "--version"): "no version here\n"})
        with self.assertRaises(ValueError):
            evidence.extract(run, "solc-static-linux", "/tmp/solc-static-linux")


class TestStringEvidence(unittest.TestCase):
    def test_static_binary_yields_version_and_commit(self):
        path = os.path.join(FIXTURES, "fake-static-binary")
        self.assertEqual(
            evidence.extract(None, "solc-macos", path, version="0.8.27",
                             short_commit=SHORT),
            "0.8.27|19164bed",
        )

    def test_static_binary_with_wrong_commit(self):
        path = os.path.join(FIXTURES, "fake-static-binary-wrong-commit")
        self.assertEqual(
            evidence.extract(None, "solc-static-linux-arm", path, version="0.8.27",
                             short_commit=SHORT),
            "0.8.27|",
        )

    def test_static_binary_without_version(self):
        path = os.path.join(FIXTURES, "fake-static-binary-no-version")
        self.assertEqual(
            evidence.extract(None, "solc-windows.exe", path, version="0.8.27",
                             short_commit=SHORT),
            "|",
        )


class TestG4(unittest.TestCase):
    def ok(self):
        return {
            "solc-macos": "0.8.27|19164bed",
            "soljson.js": "0.8.27+commit.19164bed.Emscripten.clang",
            "solc-static-linux": "0.8.27+commit.19164bed.Linux.g++",
            "solc-static-linux-arm": "0.8.27|19164bed",
            "solc-windows.exe": "0.8.27|19164bed",
        }

    def test_accepts_consistent_evidence(self):
        gates.g4_version_evidence(self.ok(), LONG, SHORT)

    def test_rejects_executable_built_from_another_commit(self):
        bad = self.ok()
        bad["solc-static-linux"] = "0.8.27+commit.deadbeef.Linux.g++"
        with self.assertRaisesRegex(GateError, "solc-static-linux"):
            gates.g4_version_evidence(bad, LONG, SHORT)

    def test_rejects_static_binary_missing_commit(self):
        bad = self.ok()
        bad["solc-static-linux-arm"] = "0.8.27|"
        with self.assertRaisesRegex(GateError, "solc-static-linux-arm"):
            gates.g4_version_evidence(bad, LONG, SHORT)

    def test_rejects_missing_artifact(self):
        bad = self.ok()
        del bad["soljson.js"]
        with self.assertRaisesRegex(GateError, "soljson.js"):
            gates.g4_version_evidence(bad, LONG, SHORT)


if __name__ == "__main__":
    unittest.main()
