#!/usr/bin/env python

import dataclasses
import re
import unittest

# pragma pylint: disable=import-error
from release import manifest
# pragma pylint: enable=import-error


REAL_SUBJECTS = [
    "Merge pull request #116 from tronprotocol/release_0.8.27",
    "Merge pull request #115 from yanghang8612/fix/freeze-builtin-ice",
    "Merge pull request #114 from yanghang8612/merge_from_v0.8.27",
    "Merge branch 'release_0.8.27' into merge_from_v0.8.27",
    "Merge tag 'v0.8.27' into merge_from_v0.8.27",
    "Merge pull request #15399 from ethereum/set-changelog-release-date",
    "Merge pull request #15395 from ethereum/sort-changelog-and-fix-inconsistencies",
]


class TestDerivePrs(unittest.TestCase):
    def test_reproduces_the_0827_pr_classification(self):
        self.assertEqual(
            manifest.derive_prs(REAL_SUBJECTS, release_pr=116),
            {"upstreamMerge": [114], "features": [115]},
        )

    def test_excludes_upstream_ethereum_prs(self):
        subjects = ["Merge pull request #15399 from ethereum/set-changelog-release-date"]
        self.assertEqual(manifest.derive_prs(subjects, release_pr=116),
                         {"upstreamMerge": [], "features": []})

    def test_excludes_the_release_pr_itself(self):
        subjects = ["Merge pull request #116 from tronprotocol/release_0.8.27"]
        self.assertEqual(manifest.derive_prs(subjects, release_pr=116),
                         {"upstreamMerge": [], "features": []})

    def test_ignores_non_pr_merge_subjects(self):
        subjects = ["Merge branch 'release_0.8.27' into merge_from_v0.8.27"]
        self.assertEqual(manifest.derive_prs(subjects, release_pr=116),
                         {"upstreamMerge": [], "features": []})

    def test_sorts_ascending(self):
        subjects = [
            "Merge pull request #120 from tronprotocol/b",
            "Merge pull request #118 from tronprotocol/a",
        ]
        self.assertEqual(manifest.derive_prs(subjects, release_pr=1)["features"], [118, 120])

    def test_owner_filter_alone_cannot_exclude_contributor_fork_prs(self):
        """Documents *why* callers must pass exclude_refs=upstream_tags(run).

        This is not desired end-to-end behavior: it is a regression test
        proving that the `owner == "ethereum"` check inside derive_prs is
        insufficient by itself. Real upstream solidity PRs are routinely
        merged from contributor forks (not the ethereum/* org), e.g.:
            Merge pull request #15105 from pgebal/smtchecker/refactor_bmc_targets_code
        Since derive_prs has no way to distinguish that from a genuine TRON
        feature PR by subject text alone, it *does* misclassify it as a
        feature here. The real exclusion must happen topologically before
        subjects ever reach derive_prs, via:
            merge_subjects(run, from_ref, to_ref, exclude_refs=upstream_tags(run))
        If this test starts failing, it means someone "fixed" derive_prs to
        reject this case directly — which would remove the reason the
        topology filter exists, and the caller-side exclude_refs plumbing
        would silently become dead weight.
        """
        subjects = REAL_SUBJECTS + [
            "Merge pull request #15105 from pgebal/smtchecker/refactor_bmc_targets_code",
        ]
        result = manifest.derive_prs(subjects, release_pr=116)
        self.assertIn(15105, result["features"])


class TestBuildArtifacts(unittest.TestCase):
    def test_order_matches_published_shasum_txt(self):
        names = [a.name for a in manifest.build_artifacts("0.8.27+commit.19164bed")]
        self.assertEqual(names,
                         ["solc-macos", "solc-static-linux", "solc-static-linux-arm",
                          "solc-windows.exe", "soljson.js"])

    def test_solc_bin_paths(self):
        arts = {a.name: a.solc_bin_path
                for a in manifest.build_artifacts("0.8.27+commit.19164bed")}
        self.assertEqual(arts["solc-macos"],
                         "macosx-amd64/solc-macosx-amd64-v0.8.27+commit.19164bed")
        self.assertEqual(arts["solc-static-linux"],
                         "linux-amd64/solc-linux-amd64-v0.8.27+commit.19164bed")
        self.assertEqual(arts["solc-static-linux-arm"],
                         "linux-arm64/solc-linux-arm64-v0.8.27+commit.19164bed")
        self.assertEqual(arts["solc-windows.exe"],
                         "windows-amd64/solc-windows-amd64-v0.8.27+commit.19164bed.exe")
        self.assertEqual(arts["soljson.js"],
                         "wasm/soljson-v0.8.27+commit.19164bed.js")


class TestManifestRoundTrip(unittest.TestCase):
    def make(self):
        return manifest.Manifest(
            version="0.8.27",
            tag="tv_0.8.27",
            codename="Democritus_v4.8.1",
            commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
            tested_commit="c7c21da02b4a8e82a2abbd9228d7c5969f700621",
            qa_approval={"pr": 116, "reviewer": "sophia-qa",
                         "submittedAt": "2026-04-13T11:02:00Z"},
            prs={"upstreamMerge": [114], "features": [115]},
            artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
        )

    def test_derived_fields(self):
        man = self.make()
        self.assertEqual(man.short_commit, "19164bed")
        self.assertEqual(man.long_version, "0.8.27+commit.19164bed")
        self.assertEqual(man.release_name, "0.8.27_Democritus_v4.8.1")

    def test_json_round_trip(self):
        man = self.make()
        self.assertEqual(manifest.Manifest.from_json(man.to_json()), man)


class TestWithDigests(unittest.TestCase):
    def make(self):
        return manifest.Manifest(
            version="0.8.27",
            tag="tv_0.8.27",
            codename="Democritus_v4.8.1",
            commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
            tested_commit="c7c21da02b4a8e82a2abbd9228d7c5969f700621",
            qa_approval={"pr": 116, "reviewer": "sophia-qa",
                         "submittedAt": "2026-04-13T11:02:00Z"},
            prs={"upstreamMerge": [114], "features": [115]},
            artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
        )

    def digest_map(self, man):
        return {
            a.name: {"sha256": f"sha256-of-{a.name}", "keccak256": f"keccak256-of-{a.name}"}
            for a in man.artifacts
        }

    def test_applies_the_right_digest_to_each_artifact(self):
        man = self.make()
        updated = man.with_digests(self.digest_map(man))
        for artifact in updated.artifacts:
            self.assertEqual(artifact.sha256, f"sha256-of-{artifact.name}")
            self.assertEqual(artifact.keccak256, f"keccak256-of-{artifact.name}")

    def test_does_not_mutate_the_original_manifest(self):
        man = self.make()
        man.with_digests(self.digest_map(man))
        for artifact in man.artifacts:
            self.assertIsNone(artifact.sha256)
            self.assertIsNone(artifact.keccak256)

    def test_preserves_every_other_manifest_field(self):
        """Guards the with_digests refactor against silent field loss.

        If a future change re-lists Manifest's fields by hand (instead of
        dataclasses.replace) and drops one, this must fail rather than pass
        silently. Iterating dataclasses.fields (instead of hand-listing
        version/tag/codename/...) means a field added to Manifest later is
        covered automatically, without this test needing an update.
        """
        man = self.make()
        updated = man.with_digests(self.digest_map(man))
        for field_ in dataclasses.fields(manifest.Manifest):
            if field_.name == "artifacts":
                continue
            self.assertEqual(
                getattr(updated, field_.name), getattr(man, field_.name),
                f"field {field_.name!r} was not preserved by with_digests",
            )

    def test_raises_value_error_on_a_digest_map_missing_an_artifact(self):
        """Missing artifact name -> descriptive ValueError, not a bare KeyError.

        `with_digests` is the join point where an externally-assembled,
        name-keyed dict meets the manifest -- the same shape of bug this
        project has already had to fix twice in gates.py. assertRaisesRegex
        with ValueError (not just assertRaises) means a regression back to
        the bare `digests[a.name]` subscript, which raises KeyError instead,
        makes this test error out rather than silently pass.
        """
        man = self.make()
        incomplete = self.digest_map(man)
        missing_name = man.artifacts[0].name
        del incomplete[missing_name]
        with self.assertRaisesRegex(ValueError, re.escape(missing_name)):
            man.with_digests(incomplete)

    def test_raises_value_error_when_entry_missing_sha256(self):
        man = self.make()
        incomplete = self.digest_map(man)
        target_name = man.artifacts[0].name
        del incomplete[target_name]["sha256"]
        with self.assertRaisesRegex(ValueError, re.escape(target_name)):
            man.with_digests(incomplete)

    def test_raises_value_error_when_entry_missing_keccak256(self):
        man = self.make()
        incomplete = self.digest_map(man)
        target_name = man.artifacts[0].name
        del incomplete[target_name]["keccak256"]
        with self.assertRaisesRegex(ValueError, re.escape(target_name)):
            man.with_digests(incomplete)


if __name__ == "__main__":
    unittest.main()
