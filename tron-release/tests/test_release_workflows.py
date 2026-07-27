#!/usr/bin/env python

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def read(name):
    with open(os.path.join(WORKFLOWS, name), "r", encoding="utf-8") as handle:
        return handle.read()


class TestRunnerTopology(unittest.TestCase):
    def test_build_has_exactly_one_self_hosted_job(self):
        text = read("build.yml")
        self.assertEqual(text.count("runs-on: [ self-hosted"), 1)
        self.assertIn("upload-to-s3:", text)
        self.assertIn("if: github.repository == 'tronprotocol/solidity'", text)

    def test_release_workflows_are_github_hosted(self):
        for name in ("release-prepare.yml", "release-apply.yml", "release-publish.yml"):
            with self.subTest(workflow=name):
                text = read(name)
                self.assertIn("runs-on: ubuntu-24.04", text)
                self.assertNotIn("runs-on: [ self-hosted", text)

    def test_release_workflows_have_no_mail_credentials(self):
        combined = "\n".join(read(name) for name in (
            "release-prepare.yml", "release-apply.yml", "release-publish.yml"
        ))
        for forbidden in ("RELEASE_MAIL_", "SMTP", "release-request.eml"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
