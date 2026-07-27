#!/usr/bin/env python

import unittest

# pragma pylint: disable=import-error
from release import gitinfo
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun


class TestParents(unittest.TestCase):
    def test_merge_commit_has_two_parents(self):
        run = FakeRun({
            ("git", "rev-list", "--parents", "-n", "1", "19164bed"):
                "19164bed 34b42dea c7c21da0\n",
        })
        self.assertEqual(gitinfo.parents(run, "19164bed"), ["34b42dea", "c7c21da0"])

    def test_root_commit_has_no_parents(self):
        run = FakeRun({
            ("git", "rev-list", "--parents", "-n", "1", "abc1234"): "abc1234\n",
        })
        self.assertEqual(gitinfo.parents(run, "abc1234"), [])


class TestIsAncestor(unittest.TestCase):
    def test_true_when_exit_zero(self):
        run = FakeRun({("git", "merge-base", "--is-ancestor", "a", "b"): ""})
        self.assertTrue(gitinfo.is_ancestor(run, "a", "b"))

    def test_false_when_command_fails(self):
        run = FakeRun({
            ("git", "merge-base", "--is-ancestor", "a", "b"): RuntimeError("exit 1"),
        })
        self.assertFalse(gitinfo.is_ancestor(run, "a", "b"))


class TestMergeSubjects(unittest.TestCase):
    def test_splits_lines_and_drops_blanks(self):
        run = FakeRun({
            ("git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27"):
                "Merge pull request #116 from tronprotocol/release_0.8.27\n"
                "Merge pull request #115 from yanghang8612/fix/freeze-builtin-ice\n"
                "\n",
        })
        self.assertEqual(
            gitinfo.merge_subjects(run, "tv_0.8.26", "tv_0.8.27"),
            [
                "Merge pull request #116 from tronprotocol/release_0.8.27",
                "Merge pull request #115 from yanghang8612/fix/freeze-builtin-ice",
            ],
        )

    def test_no_exclude_refs_builds_original_argv(self):
        run = FakeRun({
            ("git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27"):
                "Merge pull request #116 from tronprotocol/release_0.8.27\n",
        })
        gitinfo.merge_subjects(run, "tv_0.8.26", "tv_0.8.27")
        self.assertEqual(
            run.calls[-1],
            ["git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27"],
        )

    def test_empty_exclude_refs_builds_original_argv(self):
        run = FakeRun({
            ("git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27"):
                "Merge pull request #116 from tronprotocol/release_0.8.27\n",
        })
        gitinfo.merge_subjects(run, "tv_0.8.26", "tv_0.8.27", exclude_refs=[])
        self.assertEqual(
            run.calls[-1],
            ["git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27"],
        )

    def test_exclude_refs_appends_not_clause_in_position(self):
        run = FakeRun({
            (
                "git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27",
                "--not", "refs/tags/v0.8.27",
            ): "Merge pull request #115 from yanghang8612/fix/freeze-builtin-ice\n",
        })
        result = gitinfo.merge_subjects(
            run, "tv_0.8.26", "tv_0.8.27", exclude_refs=["refs/tags/v0.8.27"]
        )
        self.assertEqual(
            run.calls[-1],
            [
                "git", "log", "--merges", "--format=%s", "tv_0.8.26..tv_0.8.27",
                "--not", "refs/tags/v0.8.27",
            ],
        )
        self.assertEqual(
            result, ["Merge pull request #115 from yanghang8612/fix/freeze-builtin-ice"]
        )


class TestUpstreamTags(unittest.TestCase):
    def test_prefixes_and_sorts_and_drops_blanks(self):
        run = FakeRun({
            ("git", "tag", "-l", "v*"): "v0.8.27\nv0.4.10\n\nv0.8.9\n",
        })
        self.assertEqual(
            gitinfo.upstream_tags(run),
            ["refs/tags/v0.4.10", "refs/tags/v0.8.27", "refs/tags/v0.8.9"],
        )


class TestPreviousTag(unittest.TestCase):
    def test_picks_nearest_tv_tag_before_given(self):
        run = FakeRun({
            ("git", "tag", "--list", "tv_*", "--sort=-creatordate"):
                "tv_0.8.27\ntv_0.8.26\ntv_0.8.25\n",
        })
        self.assertEqual(gitinfo.previous_tag(run, "tv_0.8.27"), "tv_0.8.26")

    def test_returns_newest_tag_when_target_not_yet_created(self):
        """cmd_prepare calls previous_tag with the tag of the release being
        prepared (e.g. tv_0.8.28), but that tag does not exist until
        `publish` runs -- GitHub creates it then, not before. So it is never
        present in `git tag --list` at prepare time, and previous_tag must
        still resolve to the newest existing tag instead of raising."""
        run = FakeRun({
            ("git", "tag", "--list", "tv_*", "--sort=-creatordate"):
                "tv_0.8.27\ntv_0.8.26\ntv_0.8.25\n",
        })
        self.assertEqual(gitinfo.previous_tag(run, "tv_0.8.28"), "tv_0.8.27")

    def test_raises_when_no_tags_exist(self):
        run = FakeRun({
            ("git", "tag", "--list", "tv_*", "--sort=-creatordate"): "",
        })
        with self.assertRaises(ValueError):
            gitinfo.previous_tag(run, "tv_0.8.20")

    def test_raises_when_target_is_the_only_and_oldest_tag(self):
        """The in-list branch: if the target IS present but is the last (oldest)
        tag, there is no predecessor to diff against, so it must raise -- not
        silently fall through to the not-in-list `tags[0]` path (which would
        return the target itself)."""
        run = FakeRun({
            ("git", "tag", "--list", "tv_*", "--sort=-creatordate"): "tv_0.8.25\n",
        })
        with self.assertRaises(ValueError):
            gitinfo.previous_tag(run, "tv_0.8.25")


if __name__ == "__main__":
    unittest.main()
