# TRON Solidity release pipeline

Everything specific to the TRON release process lives under `tron-release/`.
The upstream `ReleaseChecklist.md` is intentionally left unchanged.

## Design

The pipeline is manual and fail-closed:

1. `prepare` verifies gates G1-G4, downloads the five binaries from a successful
   `build.yml` Actions run for the exact release commit, generates checksums and
   `manifest.json`, builds the source archive, and creates a draft release.
2. `sign` is run by the key holder. It recomputes every binary's SHA-256, signs
   all five binaries and `manifest.json`, exports the public key, and uploads the
   signatures to the draft.
3. `apply` verifies the pinned signing-key fingerprint and manifest digests,
   then opens or reuses the solc-bin PR. It sends no email.
4. Humans review the draft release and solc-bin PR.
5. `publish` downloads the current assets again, repeats signature/digest
   verification, publishes the draft, and optionally merges the solc-bin PR.

All release workflows use GitHub-hosted `ubuntu-24.04` runners. The repository's
only self-hosted release-related execution remains the final `upload-to-s3` job
in `.github/workflows/build.yml`.

## Repository configuration

Defaults are in `tron-release/config.json`. The following environment variables
override them, which keeps fork rehearsals isolated without changing production
configuration:

- `TRON_RELEASE_REPO`
- `TRON_RELEASE_SOLC_BIN_REPO`
- `TRON_RELEASE_SIGNING_KEY_FINGERPRINT`
- `TRON_RELEASE_QA_REVIEWERS` (comma-separated GitHub logins)

For Actions, configure these repository variables:

- `TRON_RELEASE_QA_REVIEWERS`
- `TRON_RELEASE_SIGNING_KEY_FINGERPRINT` when rehearsing with a non-production key

`SOLC_BIN_TOKEN` must read the selected Solidity repository and write the
selected solc-bin repository. The prepare workflow uses the built-in
`GITHUB_TOKEN` with `actions: read` and `contents: write`; it needs no AWS
credentials.

## Release sequence

Before `prepare`:

- Merge `tron-release/release-notes/tv_<version>.md` before the release PR.
- Obtain QA approval on the final release PR commit. G2 compares the GitHub
  review's `commit_id` with the merge commit's second parent.
- Wait for `build.yml` to succeed for the merge commit and record its run ID.

Then run:

```bash
gh workflow run "Release / prepare draft" \
  -f commit=<merge-sha> \
  -f build_run_id=<actions-run-id> \
  -f release_branch=develop

PYTHONPATH="$PWD/tron-release" python3 -m release.cli sign tv_<version>

gh workflow run "Release / apply" \
  -f tag=tv_<version> \
  -f solc_bin_repo=tronprotocol/solc-bin \
  -f dry_run=true
```

After inspecting the dry run, rerun `apply` with `dry_run=false`. After human
review, run `publish` first as a dry run, then with both `approved=true` and
`dry_run=false`.

Before publish, rollback consists of deleting the draft release and closing the
solc-bin PR. Publishing creates the tag and is irreversible; a failure while
merging the solc-bin PR must be repaired by merging that PR manually.

## Local tests

```bash
PYTHONPATH="$PWD/tron-release:$PWD/tron-release/tests" \
  python3 -m unittest discover -s tron-release/tests -p 'test_release_*.py'
```

The network-backed historical replay is opt-in with
`TRON_RELEASE_REPLAY=1`.
