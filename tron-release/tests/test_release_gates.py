#!/usr/bin/env python

import unittest

# pragma pylint: disable=import-error
from release import gates
from release.errors import GateError
# pragma pylint: enable=import-error


PARENTS = ["34b42dead1111111111111111111111111111111",
           "c7c21da02b4a8e82a2abbd9228d7c5969f700621"]
TESTED = PARENTS[1]
OTHER = "0a5e50fe38e63e23a600a9e8e32de1aea6e11332"


def review(login, state="APPROVED", commit_id=TESTED, at="2026-04-13T11:02:00Z"):
    return {"state": state, "commit_id": commit_id, "submitted_at": at,
            "user": {"login": login}}


class TestG1(unittest.TestCase):
    def test_accepts_merge_commit_on_develop(self):
        gates.g1_merge_commit(PARENTS, is_ancestor=True)  # does not raise

    def test_rejects_non_merge_commit(self):
        with self.assertRaisesRegex(GateError, "exactly 2 parents"):
            gates.g1_merge_commit(["34b42dea"], is_ancestor=True)

    def test_rejects_octopus_merge(self):
        with self.assertRaisesRegex(GateError, "exactly 2 parents"):
            gates.g1_merge_commit(PARENTS + [OTHER], is_ancestor=True)

    def test_rejects_commit_not_on_develop(self):
        with self.assertRaisesRegex(GateError, "not an ancestor"):
            gates.g1_merge_commit(PARENTS, is_ancestor=False)


class TestG2(unittest.TestCase):
    def test_accepts_qa_approval_of_second_parent(self):
        approval = gates.g2_qa_approval(PARENTS, [review("sophia-qa")], ["sophia-qa"])
        self.assertEqual(approval["reviewer"], "sophia-qa")
        self.assertEqual(approval["submittedAt"], "2026-04-13T11:02:00Z")

    def test_rejects_when_branch_moved_after_approval(self):
        """QA approved c7c21da0, then someone pushed; merge^2 is now OTHER."""
        parents = [PARENTS[0], OTHER]
        with self.assertRaisesRegex(GateError, "approved .* but the released code is"):
            gates.g2_qa_approval(parents, [review("sophia-qa")], ["sophia-qa"])

    def test_rejects_approval_from_non_qa_reviewer(self):
        with self.assertRaisesRegex(GateError, "no APPROVED review"):
            gates.g2_qa_approval(PARENTS, [review("random-dev")], ["sophia-qa"])

    def test_rejects_non_approved_state(self):
        with self.assertRaisesRegex(GateError, "no APPROVED review"):
            gates.g2_qa_approval(PARENTS, [review("sophia-qa", state="COMMENTED")],
                                 ["sophia-qa"])

    def test_rejects_empty_qa_whitelist_fail_closed(self):
        with self.assertRaisesRegex(GateError, "QA reviewer whitelist is empty"):
            gates.g2_qa_approval(PARENTS, [review("sophia-qa")], [])

    def test_uses_latest_approval_when_several(self):
        stale = review("sophia-qa", commit_id=OTHER, at="2026-04-01T00:00:00Z")
        fresh = review("sophia-qa", commit_id=TESTED, at="2026-04-13T11:02:00Z")
        approval = gates.g2_qa_approval(PARENTS, [stale, fresh], ["sophia-qa"])
        self.assertEqual(approval["submittedAt"], "2026-04-13T11:02:00Z")

    def test_rejects_when_latest_approval_is_stale(self):
        """A newer approval pointing at the wrong commit must not be rescued by an older one."""
        good_old = review("sophia-qa", commit_id=TESTED, at="2026-04-01T00:00:00Z")
        bad_new = review("sophia-qa", commit_id=OTHER, at="2026-04-13T11:02:00Z")
        with self.assertRaisesRegex(GateError, "approved .* but the released code is"):
            gates.g2_qa_approval(PARENTS, [good_old, bad_new], ["sophia-qa"])

    def test_rejects_single_parent_fail_closed(self):
        """Malformed `parents` (e.g. G1 was skipped) must raise GateError, not IndexError."""
        with self.assertRaisesRegex(GateError, "2 parents"):
            gates.g2_qa_approval(["onlyoneparent"], [review("sophia-qa")], ["sophia-qa"])

    def test_rejects_empty_parents_fail_closed(self):
        with self.assertRaisesRegex(GateError, "2 parents"):
            gates.g2_qa_approval([], [review("sophia-qa")], ["sophia-qa"])

    def test_rejects_octopus_merge_fail_closed(self):
        """A 3+-parent list must not silently fall through to `parents[1]`;
        G2 must be fail-closed even when called without G1 first."""
        with self.assertRaisesRegex(GateError, "2 parents"):
            gates.g2_qa_approval(PARENTS + [OTHER], [review("sophia-qa")], ["sophia-qa"])

    def test_rejects_approval_missing_submitted_at(self):
        broken = {k: v for k, v in review("sophia-qa").items() if k != "submitted_at"}
        with self.assertRaisesRegex(GateError, "sophia-qa.*missing.*submitted_at"):
            gates.g2_qa_approval(PARENTS, [broken], ["sophia-qa"])

    def test_rejects_approval_missing_commit_id(self):
        broken = {k: v for k, v in review("sophia-qa").items() if k != "commit_id"}
        with self.assertRaisesRegex(GateError, "sophia-qa.*missing.*commit_id"):
            gates.g2_qa_approval(PARENTS, [broken], ["sophia-qa"])

    def test_rejects_when_newer_approval_is_malformed_despite_older_valid_one(self):
        """A malformed newer approval must never be silently skipped in a way
        that promotes an older, complete approval to "latest" and lets a
        release through on stale evidence."""
        good_old = review("sophia-qa", commit_id=TESTED, at="2026-04-01T00:00:00Z")
        bad_new = {k: v for k, v in review("sophia-qa", at="2026-04-13T11:02:00Z").items()
                   if k != "commit_id"}
        with self.assertRaisesRegex(GateError, "sophia-qa.*missing.*commit_id"):
            gates.g2_qa_approval(PARENTS, [good_old, bad_new], ["sophia-qa"])


if __name__ == "__main__":
    unittest.main()
