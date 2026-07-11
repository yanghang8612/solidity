#!/usr/bin/env python

import os
import tempfile
import unittest

# pragma pylint: disable=import-error
from release import notes
# pragma pylint: enable=import-error


VALID = """---
version: 0.8.27
codename: Democritus_v4.8.1
testReport: https://internal.tron.network/reports/0827
---
TRON Solidity compiler 0.8.27 is fully compatible with Ethereum Solidity 0.8.27.

## New Features from TRON

### Bugfixes:

 * Analyzer: Remove the broken global `freeze` builtins.

## Major changelog from Ethereum

### Language Features:

 * Accept declarations of state variables with `transient` data location.
"""


class TestParse(unittest.TestCase):
    def test_extracts_frontmatter_and_body(self):
        front, body = notes.parse(VALID)
        self.assertEqual(front["version"], "0.8.27")
        self.assertEqual(front["codename"], "Democritus_v4.8.1")
        self.assertEqual(front["testReport"], "https://internal.tron.network/reports/0827")
        self.assertTrue(body.startswith("TRON Solidity compiler 0.8.27 is fully compatible"))

    def test_body_has_no_leading_blank_line(self):
        # Own fixture, not VALID: VALID's body starts right after the closing
        # fence with no blank line in between, so `.lstrip("\n")` has nothing
        # to strip there and removing it wouldn't move this assertion. Insert
        # a blank line after the fence so the lstrip is actually exercised.
        text = VALID.replace("---\nTRON", "---\n\nTRON")
        _, body = notes.parse(text)
        self.assertFalse(body.startswith("\n"))
        self.assertTrue(body.startswith("TRON Solidity compiler"))

    def test_rejects_missing_frontmatter(self):
        # Regex tightened to "must start with": plain "frontmatter" also
        # appears in the unterminated-fence message, so if the opening-fence
        # check were ever removed, this input would fall through to that
        # other branch and still satisfy a looser regex for the wrong reason.
        with self.assertRaisesRegex(ValueError, "must start with"):
            notes.parse("no frontmatter here\n")

    def test_rejects_unterminated_frontmatter(self):
        # Regex tightened to "unterminated": plain "frontmatter" is shared by
        # most other error messages in this module.
        with self.assertRaisesRegex(ValueError, "unterminated"):
            notes.parse("---\nversion: 0.8.27\nbody\n")

    def test_rejects_nested_yaml(self):
        # A bare `nested:\n  a: 1` never reaches the indentation check: the
        # loop raises on the `nested:` line itself first (its own line has no
        # value, which is a different guard). Give the indented line its own
        # value so indentation is the only problem the parser can see.
        text = "---\nversion: 0.8.27\n  nested: value\n---\nbody\n"
        with self.assertRaisesRegex(ValueError, "indented"):
            notes.parse(text)

    def test_rejects_key_with_no_value(self):
        # This class used to be hit only by accident, via test_rejects_nested_yaml's
        # old fixture. Now that that test isolates indentation instead, give
        # "key has no value" its own dedicated fixture.
        text = "---\nversion: 0.8.27\ncodename:\n---\nbody\n"
        with self.assertRaisesRegex(ValueError, "has no value"):
            notes.parse(text)

    def test_rejects_duplicate_key(self):
        text = "---\nversion: 0.8.27\nversion: 0.8.28\n---\nbody\n"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            notes.parse(text)


class TestLoad(unittest.TestCase):
    def write(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(text)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_valid_notes(self):
        front, body = notes.load(self.write(VALID), "0.8.27")
        self.assertEqual(front["codename"], "Democritus_v4.8.1")
        self.assertIn("Major changelog from Ethereum", body)

    def test_rejects_version_mismatch_between_frontmatter_and_argument(self):
        # VALID's body first line also says "0.8.27", so loading it as "0.8.28"
        # trips the *first-line* check too, and that message also embeds
        # "0.8.28" (from `expected`) -- so a loose regex on the version number
        # matches even if the version-mismatch check itself is deleted.
        # Rewrite the body's first line to agree with the argument version so
        # that, with the version check gone, nothing else would raise; and
        # tighten the regex to text unique to the version-mismatch message.
        text = VALID.replace(
            "TRON Solidity compiler 0.8.27 is fully compatible with Ethereum Solidity 0.8.27.",
            "TRON Solidity compiler 0.8.28 is fully compatible with Ethereum Solidity 0.8.28.",
        )
        with self.assertRaisesRegex(ValueError, "declare version"):
            notes.load(self.write(text), "0.8.28")

    def test_rejects_missing_required_key(self):
        text = VALID.replace("codename: Democritus_v4.8.1\n", "")
        with self.assertRaisesRegex(ValueError, "codename"):
            notes.load(self.write(text), "0.8.27")

    def test_rejects_body_whose_first_line_names_another_version(self):
        text = VALID.replace(
            "TRON Solidity compiler 0.8.27 is fully compatible with Ethereum Solidity 0.8.27.",
            "TRON Solidity compiler 0.8.26 is fully compatible with Ethereum Solidity 0.8.26.",
        )
        with self.assertRaisesRegex(ValueError, "first line"):
            notes.load(self.write(text), "0.8.27")

    def test_rejects_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            notes.load("/nonexistent/tv_9.9.9.md", "9.9.9")


if __name__ == "__main__":
    unittest.main()
