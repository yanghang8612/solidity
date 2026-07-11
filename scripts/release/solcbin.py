"""Project the manifest into tronprotocol/solc-bin's list.json entries.

The published list.json files are not byte-consistent in indentation:
entries up to 0.8.25 use 12-space field indent, 0.8.26 and 0.8.27 use 10.
Byte-exact round-tripping is therefore not achievable and not the goal --
`append_entry` always emits the majority style (4 spaces per nesting level,
so fields land at 12 spaces) and callers are expected to validate
parsed-entry semantic equality, not byte equality.
"""

import json
import os
import shutil
from typing import Callable, List

from release.manifest import Artifact, Manifest

Run = Callable[[List[str]], str]

_INDENT = 4


def build_entry(artifact: Artifact, man: Manifest) -> dict:
    """Project one manifest artifact into a solc-bin list.json entry.

    `keccak256`/`sha256` carry a `0x` prefix here -- unlike shasum.txt and
    keccak256.txt (see checksums.render), which carry none. This divergence
    is exactly why list.json entries are a code-generated projection rather
    than a copy of those files.
    """
    if artifact.sha256 is None:
        raise ValueError(f"{artifact.name} has no sha256; run it through with_digests first")
    if artifact.keccak256 is None:
        raise ValueError(f"{artifact.name} has no keccak256; run it through with_digests first")
    return {
        "path": os.path.basename(artifact.solc_bin_path),
        "version": man.version,
        "build": f"commit.{man.short_commit}",
        "longVersion": man.long_version,
        "keccak256": "0x" + artifact.keccak256,
        "sha256": "0x" + artifact.sha256,
        "urls": [],
    }


def append_entry(list_json_text: str, entry: dict) -> str:
    """Append `entry` to the `builds` array of a solc-bin list.json file."""
    data = json.loads(list_json_text)
    if "builds" not in data:
        raise ValueError("list.json has no 'builds' key")
    if not isinstance(data["builds"], list):
        raise ValueError(
            f"list.json 'builds' must be a list, found {type(data['builds']).__name__}"
        )
    if "path" not in entry:
        raise ValueError("entry has no 'path' key")

    builds: List[dict] = data["builds"]
    if any(b.get("path") == entry["path"] for b in builds):
        raise ValueError(f"{entry['path']!r} is already present in list.json")

    builds.append(entry)
    return json.dumps(data, indent=_INDENT, ensure_ascii=False) + "\n"


def open_solcbin_pr(run: Run, solcbin_repo: str, man: Manifest, workdir: str,
                     dry_run: bool) -> str:
    """Push this release's binaries to a `solc-bin` branch and open the PR.

    Re-runnable: an `apply` invocation that already got this far in a
    previous, later-failing run (e.g. G7 or the mail send failed afterwards)
    must not open a second PR for the same release. `gh pr list --head
    <branch>` is checked first -- it needs only `solcbin_repo` and the
    deterministic `binaries-for-<version>` branch name, not a clone -- and if
    it already names an open PR, that URL is reused immediately: no clone, no
    branch, no commit, no push.

    The existence check uses `--jq "[.[].url]"`, the same idiom
    `github.pr_for_commit` uses: a filter that always yields valid JSON
    (`[]` when nothing matches), parsed with `json.loads` and checked by
    emptiness in Python. This is deliberate, not `--jq ".[0].url"`: that
    alternative happens to work under `gh`'s bundled gojq, which suppresses a
    bare `null` on stdout entirely for an empty match, but standard `jq`
    prints the four bytes `null\n` for the same query, which a bare
    `.strip()` + `if existing:` truthiness check would treat as a real (and
    wrong) PR URL. Relying on which jq flavor happens to be on PATH is not a
    bet this function should make; `[.[].url]` sidesteps the question
    entirely by never producing a bare `null` in the first place -- see
    `test_never_mistakes_a_literal_null_for_a_pr_url` in
    test_release_solcbin.py.

    A response that fails to parse as JSON at all (a `gh` regression, or a
    warning printed to stdout instead of stderr) raises `ValueError` naming
    the failing command, rather than letting `json.JSONDecodeError` escape
    bare.

    In `--dry-run`, everything up to and including the local commit still
    runs (so a dry run exercises the branch/copy/list.json-append machinery
    for real), but the push and `gh pr create` never do; a placeholder URL is
    returned instead so the rest of `apply` (G7, the email body) has
    something to render.
    """
    branch = f"binaries-for-{man.version}"

    list_argv = ["gh", "pr", "list", "--repo", solcbin_repo, "--head", branch,
                 "--json", "url", "--jq", "[.[].url]"]
    raw = run(list_argv)
    try:
        urls = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{' '.join(list_argv)}: expected JSON from --jq, got {raw!r}"
        ) from error
    if urls:
        existing = urls[0]
        print(f"solc-bin PR for {branch} already exists: {existing}")
        return existing

    checkout = os.path.join(workdir, "solc-bin")
    run(["gh", "repo", "clone", solcbin_repo, checkout, "--", "--depth", "1"])
    run(["git", "-C", checkout, "checkout", "-b", branch])

    for artifact in man.artifacts:
        target = os.path.join(checkout, artifact.solc_bin_path)
        shutil.copyfile(os.path.join(workdir, artifact.name), target)
        shutil.copyfile(os.path.join(workdir, artifact.name + ".sig"), target + ".sig")

        list_path = os.path.join(checkout, artifact.solc_bin_dir, "list.json")
        with open(list_path, "r", encoding="utf-8") as handle:
            updated = append_entry(handle.read(), build_entry(artifact, man))
        with open(list_path, "w", encoding="utf-8") as handle:
            handle.write(updated)

    run(["git", "-C", checkout, "add", "-A"])
    run(["git", "-C", checkout, "commit", "-m", f"Binaries for {man.version}"])

    if dry_run:
        print(f"[dry-run] committed locally; would push {branch} to {solcbin_repo} "
              "and open a PR")
        return "https://example.invalid/dry-run"

    run(["git", "-C", checkout, "push", "origin", branch])
    url = run(["gh", "pr", "create", "--repo", solcbin_repo, "--head", branch,
               "--title", f"Binaries for {man.version}", "--body", ""])
    return url.strip()
