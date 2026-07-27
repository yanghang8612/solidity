#!/usr/bin/env python

import contextlib
import dataclasses
import io
import json
import os
import re
import shutil
import tempfile
import unittest

# pragma pylint: disable=import-error
from release import manifest, solcbin
# pragma pylint: enable=import-error

from release_test_helpers import FakeRun


DIGESTS = {
    "solc-macos": {"sha256": "a" * 64, "keccak256": "b" * 64},
    "solc-static-linux": {
        "sha256": "7a3dfc3d987bbe447c0eba89e5fd89a1fc053e8629799e167810367196a3281e",
        "keccak256": "adf2934186d27983f2e0758f8a99b2f6ae369b752bf01ebb4e99ed1186073106",
    },
    "solc-static-linux-arm": {"sha256": "1" * 64, "keccak256": "2" * 64},
    "solc-windows.exe": {"sha256": "c" * 64, "keccak256": "d" * 64},
    "soljson.js": {"sha256": "e" * 64, "keccak256": "f" * 64},
}


def make_manifest(with_digests=True):
    man = manifest.Manifest(
        version="0.8.27", tag="tv_0.8.27", codename="Democritus_v4.8.1",
        commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
        tested_commit="c" * 40, qa_approval={}, prs={},
        artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
    )
    return man.with_digests(DIGESTS) if with_digests else man


class TestBuildEntry(unittest.TestCase):
    def test_matches_published_linux_entry(self):
        man = make_manifest()
        linux = next(a for a in man.artifacts if a.name == "solc-static-linux")
        self.assertEqual(solcbin.build_entry(linux, man), {
            "path": "solc-linux-amd64-v0.8.27+commit.19164bed",
            "version": "0.8.27",
            "build": "commit.19164bed",
            "longVersion": "0.8.27+commit.19164bed",
            "keccak256": "0xadf2934186d27983f2e0758f8a99b2f6ae369b752bf01ebb4e99ed1186073106",
            "sha256": "0x7a3dfc3d987bbe447c0eba89e5fd89a1fc053e8629799e167810367196a3281e",
            "urls": [],
        })

    def test_path_is_basename_not_full_path(self):
        man = make_manifest()
        wasm = next(a for a in man.artifacts if a.name == "soljson.js")
        self.assertEqual(solcbin.build_entry(wasm, man)["path"],
                         "soljson-v0.8.27+commit.19164bed.js")

    def test_hashes_carry_0x_prefix(self):
        man = make_manifest()
        entry = solcbin.build_entry(man.artifacts[0], man)
        self.assertTrue(entry["sha256"].startswith("0x"))
        self.assertTrue(entry["keccak256"].startswith("0x"))

    def test_raises_when_sha256_is_none(self):
        """A manifest that never went through with_digests (or a hand-edited
        manifest.json with a null sha256) must raise ValueError naming the
        artifact -- not silently concatenate "0x" + None."""
        man = make_manifest(with_digests=False)
        artifact = man.artifacts[0]
        with self.assertRaisesRegex(ValueError, re.escape(artifact.name)):
            solcbin.build_entry(artifact, man)

    def test_raises_when_keccak256_is_none(self):
        """sha256 present but keccak256 missing must be caught independently
        -- not just as a side effect of checking "were any digests set"."""
        man = make_manifest()
        artifact = dataclasses.replace(man.artifacts[0], keccak256=None)
        with self.assertRaisesRegex(ValueError, re.escape(artifact.name)):
            solcbin.build_entry(artifact, man)


def make_entry(**overrides):
    entry = {"path": "p", "version": "0.8.27", "build": "b",
             "longVersion": "lv", "keccak256": "0x1", "sha256": "0x2", "urls": []}
    entry.update(overrides)
    return entry


EXISTING = """{
    "builds": [
        {
            "path": "solc-linux-amd64-v0.8.26+commit.733b4d28",
            "version": "0.8.26",
            "build": "commit.733b4d28",
            "longVersion": "0.8.26+commit.733b4d28",
            "keccak256": "0x00",
            "sha256": "0x11",
            "urls": []
        }
    ]
}
"""


class TestAppendEntry(unittest.TestCase):
    def test_appends_and_stays_valid_json(self):
        entry = make_entry()
        result = solcbin.append_entry(EXISTING, entry)
        parsed = json.loads(result)
        self.assertEqual(len(parsed["builds"]), 2)
        self.assertEqual(parsed["builds"][-1], entry)

    def test_preserves_existing_entries_verbatim(self):
        before = json.loads(EXISTING)["builds"][0]
        after = json.loads(solcbin.append_entry(EXISTING, make_entry()))["builds"][0]
        self.assertEqual(before, after)

    def test_ends_with_single_newline(self):
        result = solcbin.append_entry(EXISTING, make_entry())
        self.assertTrue(result.endswith("}\n"))
        self.assertFalse(result.endswith("\n\n"))

    def test_rejects_duplicate_path(self):
        entry = make_entry(path="solc-linux-amd64-v0.8.26+commit.733b4d28")
        with self.assertRaisesRegex(ValueError, "already present"):
            solcbin.append_entry(EXISTING, entry)

    def test_field_indent_is_twelve_spaces(self):
        result = solcbin.append_entry(EXISTING, make_entry())
        path_lines = [l for l in result.split("\n") if '"path": "p"' in l]
        self.assertEqual(len(path_lines), 1)
        self.assertEqual(len(path_lines[0]) - len(path_lines[0].lstrip(" ")), 12)

    def test_rejects_missing_builds_key(self):
        """A KeyError here would surface as an unhandled crash deep inside
        the apply stage instead of a descriptive, catchable error."""
        with self.assertRaisesRegex(ValueError, "builds"):
            solcbin.append_entry("{}", make_entry())

    def test_rejects_null_builds_key(self):
        """A hand-edited or corrupt list.json with "builds": null must raise
        the mandated descriptive ValueError, not a TypeError from iterating
        over None deep inside the `any(...)` duplicate-path scan."""
        with self.assertRaisesRegex(ValueError, "builds"):
            solcbin.append_entry('{"builds": null}', make_entry())

    def test_rejects_object_builds_key(self):
        """"builds": {} (an object instead of an array) must raise the same
        descriptive ValueError, not an AttributeError from calling .append()
        on a dict where a list was expected."""
        with self.assertRaisesRegex(ValueError, "builds"):
            solcbin.append_entry('{"builds": {}}', make_entry())

    def test_rejects_entry_without_path(self):
        bad_entry = {k: v for k, v in make_entry().items() if k != "path"}
        with self.assertRaisesRegex(ValueError, "path"):
            solcbin.append_entry(EXISTING, bad_entry)


SOLCBIN_REPO = "tronprotocol/solc-bin"


def seed_checkout(checkout_dir, builds_by_dir=None):
    """Create the configured solc-bin directories, each with a `list.json` -- what
    a real `gh repo clone` would have produced. `run()` is faked everywhere
    in this test file, so nothing else creates this directory structure."""
    builds_by_dir = builds_by_dir or {}
    for _, directory, _ in manifest.ARTIFACT_SPECS:
        target_dir = os.path.join(checkout_dir, directory)
        os.makedirs(target_dir, exist_ok=True)
        payload = {"builds": builds_by_dir.get(directory, [])}
        with open(os.path.join(target_dir, "list.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))


def write_artifact_files(workdir, names):
    """Stand in for what `gh release download` would have produced in `apply`'s workdir."""
    for name in names:
        with open(os.path.join(workdir, name), "wb") as handle:
            handle.write(f"fake-{name}-bytes".encode())
        with open(os.path.join(workdir, name + ".sig"), "wb") as handle:
            handle.write(f"fake-{name}-sig-bytes".encode())


class TestOpenSolcbinPr(unittest.TestCase):
    """`open_solcbin_pr` orchestrates the solc-bin clone/branch/commit/push/PR
    sequence. Every `run()` call is faked; no test here spawns a subprocess,
    touches the network, or invokes real `git`/`gh`.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="test-release-solcbin-")
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.man = make_manifest()
        self.names = [a.name for a in self.man.artifacts]
        write_artifact_files(self.workdir, self.names)
        self.checkout = os.path.join(self.workdir, "solc-bin")
        self.branch = f"binaries-for-{self.man.version}"

    def list_argv(self):
        return ("gh", "pr", "list", "--repo", SOLCBIN_REPO, "--head", self.branch,
                "--json", "url", "--jq", "[.[].url]")

    def clone_argv(self):
        return ("gh", "repo", "clone", SOLCBIN_REPO, self.checkout, "--", "--depth", "1")

    def checkout_branch_argv(self):
        return ("git", "-C", self.checkout, "checkout", "-b", self.branch)

    def add_argv(self):
        return ("git", "-C", self.checkout, "add", "-A")

    def config_name_argv(self):
        return ("git", "-C", self.checkout, "config", "user.name", "tron-release-bot")

    def config_email_argv(self):
        return ("git", "-C", self.checkout, "config", "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com")

    def commit_argv(self):
        return ("git", "-C", self.checkout, "commit", "-m", f"Binaries for {self.man.version}")

    def push_argv(self):
        return ("git", "-C", self.checkout, "push", "origin", self.branch)

    def pr_create_argv(self):
        return ("gh", "pr", "create", "--repo", SOLCBIN_REPO, "--head", self.branch,
                "--title", f"Binaries for {self.man.version}", "--body", "")

    def test_reuses_existing_pr_without_touching_git_at_all(self):
        """A prior run already opened this PR (e.g. it failed at a later
        gate); a re-run must reuse that PR rather than attempt a second
        clone/branch/commit/push. Proven by giving FakeRun no table entries
        at all beyond the list check -- an attempt at any of those would
        raise AssertionError (unexpected command), not silently succeed.
        """
        existing_url = "https://github.com/tronprotocol/solc-bin/pull/42"
        run = FakeRun({self.list_argv(): json.dumps([existing_url]) + "\n"})

        with contextlib.redirect_stdout(io.StringIO()):
            result = solcbin.open_solcbin_pr(run, SOLCBIN_REPO, self.man, self.workdir,
                                              dry_run=False)

        self.assertEqual(result, existing_url)
        self.assertEqual(run.calls, [list(self.list_argv())])

    def test_never_mistakes_a_literal_null_for_a_pr_url(self):
        """Regression guard for the exact hazard `open_solcbin_pr`'s
        `--jq "[.[].url]"` idiom is chosen to avoid.

        `gh` embeds gojq, which suppresses a bare `null` on stdout entirely
        for an empty `.[0].url` match -- but standard `jq` prints the four
        bytes `null\n` for the very same query. If this function ever
        regressed to `--jq ".[0].url"` parsed by `.strip()` + a bare
        `if existing:` truthiness check, a `"null"` string would be truthy in
        Python and would be returned as though it were a real PR URL --
        silently skipping the entire clone/branch/commit/push/create flow.

        With the current `[.[].url]` + `json.loads` implementation, feeding
        the fake `run` the literal string `"null\n"` for the list-check
        command parses to `None`, which is just as falsy as `[]`: this test
        proves `open_solcbin_pr` treats it as "no existing PR" and proceeds
        through the full flow, never returning the string "null".
        """
        seed_checkout(self.checkout)
        pr_url = "https://github.com/tronprotocol/solc-bin/pull/99"
        run = FakeRun({
            self.list_argv(): "null\n",
            self.clone_argv(): "",
            self.checkout_branch_argv(): "",
            self.config_name_argv(): "",
            self.config_email_argv(): "",
            self.add_argv(): "",
            self.commit_argv(): "",
            self.push_argv(): "",
            self.pr_create_argv(): pr_url + "\n",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            result = solcbin.open_solcbin_pr(run, SOLCBIN_REPO, self.man, self.workdir,
                                              dry_run=False)

        self.assertEqual(result, pr_url)
        self.assertNotEqual(result, "null")
        self.assertIn(list(self.clone_argv()), run.calls)
        self.assertIn(list(self.checkout_branch_argv()), run.calls)
        self.assertIn(list(self.commit_argv()), run.calls)
        self.assertIn(list(self.push_argv()), run.calls)
        self.assertIn(list(self.pr_create_argv()), run.calls)

    def test_raises_value_error_when_pr_list_output_is_not_json(self):
        """A `gh` failure mode that prints non-JSON to stdout (a warning
        landing on stdout instead of stderr, or a future `gh` regression)
        must raise a descriptive ValueError naming the failing command,
        not let json.JSONDecodeError escape bare from deep inside this
        function.
        """
        run = FakeRun({self.list_argv(): "not json at all"})
        with self.assertRaisesRegex(ValueError, "gh pr list"):
            solcbin.open_solcbin_pr(run, SOLCBIN_REPO, self.man, self.workdir,
                                     dry_run=False)

    def test_creates_branch_copies_binaries_and_appends_list_json(self):
        seed_checkout(self.checkout)
        pr_url = "https://github.com/tronprotocol/solc-bin/pull/99"
        run = FakeRun({
            self.list_argv(): "[]\n",
            self.clone_argv(): "",
            self.checkout_branch_argv(): "",
            self.config_name_argv(): "",
            self.config_email_argv(): "",
            self.add_argv(): "",
            self.commit_argv(): "",
            self.push_argv(): "",
            self.pr_create_argv(): pr_url + "\n",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            result = solcbin.open_solcbin_pr(run, SOLCBIN_REPO, self.man, self.workdir,
                                              dry_run=False)

        self.assertEqual(result, pr_url)
        # The existence check is the very first call -- it needs only the
        # repo and the deterministic branch name, not a clone -- so it must
        # run before anything else, not merely "somewhere before create".
        self.assertEqual(run.calls[0], list(self.list_argv()))
        for artifact in self.man.artifacts:
            target = os.path.join(self.checkout, artifact.solc_bin_path)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), f"fake-{artifact.name}-bytes".encode())
            with open(target + ".sig", "rb") as handle:
                self.assertEqual(handle.read(), f"fake-{artifact.name}-sig-bytes".encode())

            list_path = os.path.join(self.checkout, artifact.solc_bin_dir, "list.json")
            with open(list_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(len(data["builds"]), 1)
            self.assertEqual(data["builds"][0]["path"], os.path.basename(artifact.solc_bin_path))
            self.assertEqual(data["builds"][0]["sha256"], "0x" + artifact.sha256)

        self.assertIn(list(self.push_argv()), run.calls)
        self.assertIn(list(self.pr_create_argv()), run.calls)

    def test_initializes_new_linux_arm64_list(self):
        seed_checkout(self.checkout)
        shutil.rmtree(os.path.join(self.checkout, "linux-arm64"))
        run = FakeRun({
            self.list_argv(): "[]\n",
            self.clone_argv(): "",
            self.checkout_branch_argv(): "",
            self.config_name_argv(): "",
            self.config_email_argv(): "",
            self.add_argv(): "",
            self.commit_argv(): "",
        })

        with contextlib.redirect_stdout(io.StringIO()):
            solcbin.open_solcbin_pr(
                run, SOLCBIN_REPO, self.man, self.workdir, dry_run=True
            )

        with open(os.path.join(self.checkout, "linux-arm64", "list.json"),
                  "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(len(data["builds"]), 1)
        self.assertEqual(data["builds"][0]["version"], "0.8.27")

    def test_dry_run_commits_locally_but_never_pushes_or_creates_a_pr(self):
        seed_checkout(self.checkout)
        run = FakeRun({
            self.list_argv(): "[]\n",
            self.clone_argv(): "",
            self.checkout_branch_argv(): "",
            self.config_name_argv(): "",
            self.config_email_argv(): "",
            self.add_argv(): "",
            self.commit_argv(): "",
            # Deliberately no push / pr-create entries: if dry-run ever
            # reached them, FakeRun would raise AssertionError.
        })

        with contextlib.redirect_stdout(io.StringIO()):
            result = solcbin.open_solcbin_pr(run, SOLCBIN_REPO, self.man, self.workdir,
                                              dry_run=True)

        self.assertTrue(result)
        self.assertIn(list(self.commit_argv()), run.calls)
        push_calls = [c for c in run.calls if "push" in c]
        self.assertEqual(push_calls, [])
        create_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "create"]]
        self.assertEqual(create_calls, [])

        # "up to and including the local commit" must still have appended
        # the list.json entries, even though nothing was pushed.
        first_artifact = self.man.artifacts[0]
        list_path = os.path.join(self.checkout, first_artifact.solc_bin_dir, "list.json")
        with open(list_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(len(data["builds"]), 1)

if __name__ == "__main__":
    unittest.main()
