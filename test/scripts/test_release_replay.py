#!/usr/bin/env python

"""Replay the published tv_0.8.27 release and assert we reproduce it.

This is the acceptance test for the whole release pipeline: if it cannot
reproduce a release that has already happened, the pipeline must not ship.
It is the one test file in this suite allowed to shell out to `git`/`gh` and
touch the network -- every other test file stays subprocess/network-free.
Do not import anything from this module into another test file.

Requires network. Enable with TRON_RELEASE_REPLAY=1. When disabled (the
default), the whole class is skipped -- nothing here runs, and nothing here
prints, so the default suite stays at 0 bytes of stdout.

Fixture reuse: downloading the four release binaries is ~40MB. If
TRON_RELEASE_REPLAY_FIXTURE_DIR is set and points at a directory already
containing all four binaries plus shasum.txt and keccak256.txt, those files
are used as-is and `gh release download` is skipped. Otherwise the binaries
are downloaded fresh into a temporary directory. Either way, `gh api` is
still used to fetch the published solc-bin list.json entries, and local
`git`/`gh` calls still walk real release-tag history -- only the four binary
downloads are short-circuited by the fixture directory.

Two published artifacts are deliberately NOT byte-compared here:
  * list.json -- the published file has inconsistent indentation (12-space
    fields up to 0.8.25, 10-space for 0.8.26/0.8.27). We compare parsed
    entries instead (see test_solcbin_entries_match_published_semantically).
  * the source tarball -- not exercised by this test at all; gzip embeds an
    mtime, so it could never be byte-identical either.
"""

import base64
import json
import os
import subprocess
import tempfile
import unittest
from typing import List, Optional

# pragma pylint: disable=import-error
from release import checksums, gates, gitinfo, manifest, solcbin
from release.errors import GateError
# pragma pylint: enable=import-error


REPO = "tronprotocol/solidity"
TAG = "tv_0.8.27"
PREV_TAG = "tv_0.8.26"
CODENAME = "Democritus_v4.8.1"
COMMIT = "19164bedaa1a6ad09e7a9bf461d5ce73423d7611"
TESTED = "c7c21da02b4a8e82a2abbd9228d7c5969f700621"
STALE_TESTED = "0a5e50fe38e63e23a600a9e8e32de1aea6e11332"  # the commit right before TESTED
LONG_VERSION = "0.8.27+commit.19164bed"
RELEASE_PR = 116
QA_LOGIN = "qa"
QA_SUBMITTED_AT = "2026-04-13T11:02:00Z"
NAMES = ["solc-macos", "solc-static-linux", "solc-windows.exe", "soljson.js"]
FIXTURE_FILES = NAMES + ["shasum.txt", "keccak256.txt"]


def sh(argv: List[str]) -> str:
    """Real subprocess runner matching the `Run = Callable[[List[str]], str]`
    shape `gitinfo` and `gates` expect. This is the one place in the whole
    test suite allowed to shell out -- see the module docstring."""
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def _resolve_fixture_dir() -> Optional[str]:
    """TRON_RELEASE_REPLAY_FIXTURE_DIR if it holds all six fixture files, else None."""
    fixture_dir = os.environ.get("TRON_RELEASE_REPLAY_FIXTURE_DIR")
    if not fixture_dir:
        return None
    if all(os.path.isfile(os.path.join(fixture_dir, name)) for name in FIXTURE_FILES):
        return fixture_dir
    return None


@unittest.skipUnless(os.environ.get("TRON_RELEASE_REPLAY") == "1",
                     "set TRON_RELEASE_REPLAY=1 to run (needs network)")
class TestReplay0827(unittest.TestCase):
    """Six independent acceptance checks against the real, already-published
    tv_0.8.27 release. Each test method is one criterion; a failure names,
    via its own test name and assertion message, exactly which part of the
    pipeline regressed."""

    @classmethod
    def setUpClass(cls):
        fixture_dir = _resolve_fixture_dir()
        if fixture_dir is not None:
            cls.dir = fixture_dir
        else:
            cls.dir = tempfile.mkdtemp(prefix="replay-0827-")
            patterns = []
            for name in FIXTURE_FILES:
                patterns += ["-p", name]
            sh(["gh", "release", "download", TAG, "--repo", REPO, "-D", cls.dir,
                "--clobber"] + patterns)

        cls.digests = {n: checksums.digests(os.path.join(cls.dir, n)) for n in NAMES}
        cls.manifest = manifest.Manifest(
            version="0.8.27", tag=TAG, codename=CODENAME, commit=COMMIT,
            tested_commit=TESTED,
            qa_approval={"pr": RELEASE_PR, "reviewer": "replay", "submittedAt": QA_SUBMITTED_AT},
            prs={"upstreamMerge": [114], "features": [115]},
            artifacts=manifest.build_artifacts(LONG_VERSION),
        ).with_digests(cls.digests)

    def _read(self, name: str) -> str:
        with open(os.path.join(self.dir, name), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_shasum_txt_is_byte_identical(self):
        """Criterion 1. Fails if checksums.render's line format, artifact
        order, or trailing newline drifts from the published shasum.txt, or
        if any digest is wrong."""
        self.assertEqual(checksums.render(self.manifest, "sha256"), self._read("shasum.txt"))

    def test_keccak256_txt_is_byte_identical(self):
        """Criterion 2. Same failure mode as above, for keccak256.txt."""
        self.assertEqual(checksums.render(self.manifest, "keccak256"),
                         self._read("keccak256.txt"))

    def test_solcbin_entries_match_published_semantically(self):
        """Criterion 3. Fails if solcbin.build_entry's field set, path
        template, version/build string, or 0x-prefixing diverges from what
        was actually merged into tronprotocol/solc-bin's list.json."""
        for artifact in self.manifest.artifacts:
            raw = sh(["gh", "api",
                      f"repos/tronprotocol/solc-bin/contents/{artifact.solc_bin_dir}/list.json",
                      "--jq", ".content"])
            published = json.loads(base64.b64decode(raw))["builds"][-1]
            self.assertEqual(solcbin.build_entry(artifact, self.manifest), published,
                             f"mismatch for {artifact.name}")

    def test_prs_derive_from_real_git_history(self):
        """Criterion 4. Fails if derive_prs's PR classification regresses, or
        if the topological upstream-tag exclusion stops isolating TRON
        merges: an un-excluded `git log tv_0.8.26..tv_0.8.27` yields 117
        merge subjects / 30 "features" instead of the real 1, because
        upstream solidity PRs are merged from contributor forks, not the
        ethereum/* org (see manifest.derive_prs's docstring)."""
        subjects = gitinfo.merge_subjects(
            sh, PREV_TAG, TAG,
            exclude_refs=gitinfo.upstream_tags(sh),
        )
        self.assertEqual(manifest.derive_prs(subjects, release_pr=RELEASE_PR),
                         {"upstreamMerge": [114], "features": [115]})

    def test_g2_holds_against_real_git_topology(self):
        """Criterion 5. Fails if gitinfo.parents stops reporting the release
        commit's real second parent, or if gates.g2_qa_approval rejects a
        genuinely matching QA approval (a false-positive gate failure that
        would block every legitimate release)."""
        parents = gitinfo.parents(sh, COMMIT)
        self.assertEqual(parents[1], TESTED)
        approval = gates.g2_qa_approval(
            parents,
            [{"state": "APPROVED", "commit_id": TESTED,
              "submitted_at": QA_SUBMITTED_AT, "user": {"login": QA_LOGIN}}],
            [QA_LOGIN],
        )
        self.assertEqual(approval["reviewer"], QA_LOGIN)

    def test_g2_catches_a_branch_that_moved_after_approval(self):
        """Criterion 6. Fails if gates.g2_qa_approval stops fail-closing when
        the QA-approved commit_id no longer matches the code actually
        released (a false-negative that would let an unreviewed branch tip
        ship under cover of a stale approval)."""
        parents = gitinfo.parents(sh, COMMIT)
        with self.assertRaises(GateError):
            gates.g2_qa_approval(
                parents,
                [{"state": "APPROVED", "commit_id": STALE_TESTED,
                  "submitted_at": QA_SUBMITTED_AT, "user": {"login": QA_LOGIN}}],
                [QA_LOGIN],
            )


if __name__ == "__main__":
    unittest.main()
