#!/usr/bin/env python

import unittest

# pragma pylint: disable=import-error
from release import gates
from release.errors import GateError
# pragma pylint: enable=import-error

PR_URL = "https://github.com/tronprotocol/solc-bin/pull/11"


class TestG7(unittest.TestCase):
    """G7: the release request email may only go out while nothing is public yet.

    `is_draft` must always be a fresh value the caller obtained from
    `github.release_view` -- see `cli.cmd_apply`, which passes through its
    `isDraft` field unmodified rather than assuming the release is still a
    draft just because `prepare` created it as one.
    """

    def test_accepts_draft_with_open_solcbin_pr(self):
        gates.g7_ready_to_mail(is_draft=True, solcbin_pr_url=PR_URL)

    def test_rejects_already_published_release(self):
        with self.assertRaisesRegex(GateError, "already published"):
            gates.g7_ready_to_mail(is_draft=False, solcbin_pr_url=PR_URL)

    def test_rejects_missing_solcbin_pr(self):
        with self.assertRaisesRegex(GateError, "solc-bin"):
            gates.g7_ready_to_mail(is_draft=True, solcbin_pr_url=None)

    def test_rejects_empty_solcbin_pr_url(self):
        with self.assertRaisesRegex(GateError, "solc-bin"):
            gates.g7_ready_to_mail(is_draft=True, solcbin_pr_url="")

    def test_rejects_published_release_even_without_a_pr_url(self):
        """Both problems can be true at once; the draft check must still
        fire first with its own message rather than being masked by the
        missing-PR message."""
        with self.assertRaisesRegex(GateError, "already published"):
            gates.g7_ready_to_mail(is_draft=False, solcbin_pr_url=None)


if __name__ == "__main__":
    unittest.main()
