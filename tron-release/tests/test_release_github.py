#!/usr/bin/env python

import json
import unittest

# pragma pylint: disable=import-error
from release import github
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun


class TestPrForCommit(unittest.TestCase):
    def test_returns_single_pr_number(self):
        run = FakeRun({
            ("gh", "api", "repos/tronprotocol/solidity/commits/19164bed/pulls",
             "--jq", "[.[].number]"): "[116]\n",
        })
        self.assertEqual(github.pr_for_commit(run, "tronprotocol/solidity", "19164bed"), 116)

    def test_raises_when_no_pr(self):
        run = FakeRun({
            ("gh", "api", "repos/tronprotocol/solidity/commits/deadbeef/pulls",
             "--jq", "[.[].number]"): "[]\n",
        })
        with self.assertRaises(ValueError):
            github.pr_for_commit(run, "tronprotocol/solidity", "deadbeef")

    def test_raises_when_ambiguous(self):
        run = FakeRun({
            ("gh", "api", "repos/tronprotocol/solidity/commits/19164bed/pulls",
             "--jq", "[.[].number]"): "[116, 117]\n",
        })
        with self.assertRaises(ValueError):
            github.pr_for_commit(run, "tronprotocol/solidity", "19164bed")


class TestReviews(unittest.TestCase):
    def test_parses_reviews(self):
        payload = [{
            "state": "APPROVED",
            "commit_id": "c7c21da02b4a8e82a2abbd9228d7c5969f700621",
            "submitted_at": "2026-04-13T11:02:00Z",
            "user": {"login": "sophia-qa"},
        }]
        run = FakeRun({
            ("gh", "api", "repos/tronprotocol/solidity/pulls/116/reviews",
             "--paginate"): json.dumps(payload),
        })
        self.assertEqual(github.reviews(run, "tronprotocol/solidity", 116), payload)


class TestReleaseView(unittest.TestCase):
    def test_parses_release_metadata(self):
        payload = {
            "tagName": "tv_0.8.27",
            "isDraft": False,
            "assets": [{"name": "solc-static-linux"}],
            "targetCommitish": "c7c21da02b4a8e82a2abbd9228d7c5969f700621",
        }
        run = FakeRun({
            ("gh", "release", "view", "tv_0.8.27", "--repo", "tronprotocol/solidity",
             "--json", "tagName,isDraft,assets,targetCommitish"): json.dumps(payload),
        })
        self.assertEqual(
            github.release_view(run, "tronprotocol/solidity", "tv_0.8.27"),
            payload,
        )

    def test_raises_value_error_naming_missing_is_draft(self):
        """`cli.cmd_apply` indexes the returned dict with a bare
        `release["isDraft"]` before ever passing it to G7, with no
        validation of its own. A KeyError here would otherwise surface as an
        unhandled crash deep inside the apply stage instead of a
        descriptive, catchable error -- the same fail-closed defect class
        already fixed in `cli.load_config` and the release gates.
        """
        payload = {
            "tagName": "tv_0.8.27",
            "assets": [{"name": "solc-static-linux"}],
            "targetCommitish": "c7c21da02b4a8e82a2abbd9228d7c5969f700621",
            # isDraft is missing
        }
        run = FakeRun({
            ("gh", "release", "view", "tv_0.8.27", "--repo", "tronprotocol/solidity",
             "--json", "tagName,isDraft,assets,targetCommitish"): json.dumps(payload),
        })
        with self.assertRaisesRegex(ValueError, "isDraft"):
            github.release_view(run, "tronprotocol/solidity", "tv_0.8.27")

    def test_names_every_missing_field_at_once(self):
        """Several missing fields -> one error naming all of them, not just
        the first -- matching `cli.load_config`'s established convention."""
        run = FakeRun({
            ("gh", "release", "view", "tv_0.8.27", "--repo", "tronprotocol/solidity",
             "--json", "tagName,isDraft,assets,targetCommitish"): json.dumps({}),
        })
        with self.assertRaises(ValueError) as ctx:
            github.release_view(run, "tronprotocol/solidity", "tv_0.8.27")
        message = str(ctx.exception)
        for field in ("tagName", "isDraft", "assets", "targetCommitish"):
            self.assertIn(field, message)


if __name__ == "__main__":
    unittest.main()
