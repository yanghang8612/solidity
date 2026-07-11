# TRON Solidity Release Checklist

The pipeline lives in `scripts/release/` and `.github/workflows/release-*.yml`.
Read `release-notes/README.md` before you start.

## 0. Prerequisites (once)

- [ ] `.github/release-config.json` lists the QA reviewers' GitHub logins in
      `qaReviewers`. **If it is empty, G2 fails and no release can be prepared** —
      this is deliberate fail-closed behavior, not a bug.
- [ ] Repository secrets exist: `S3_BUCKET_PROD`, `SOLC_BIN_TOKEN`,
      `RELEASE_MAIL_RECIPIENTS`, `RELEASE_MAIL_HOST`, `RELEASE_MAIL_PORT`,
      `RELEASE_MAIL_USER`, `RELEASE_MAIL_PASSWORD`.
- [ ] `SOLC_BIN_TOKEN` has **write** access to both `tronprotocol/solc-bin` and
      `tronprotocol/solidity`. Write to `solidity` is easy to under-provision:
      `apply` only reads/writes solc-bin and reads solidity, but `publish`
      calls `gh release edit --draft=false` (the step that actually creates
      the tag) using this same token instead of the built-in `GITHUB_TOKEN`,
      so anything less than write on `solidity` makes `publish` fail.
- [ ] The signing key holder can run the `sign` command below — it needs only
      `gh` and `gpg`, no AWS/PAT/mail credentials.

## 1. Before the release PR is merged

- [ ] Open a PR merging `release_X` into `develop`.
- [ ] **QA approves that PR on GitHub.** The approval's commit_id is the
      machine-readable proof of exactly what was tested — G2 binds the released
      code to it. Nothing else will do.
- [ ] Open a separate PR adding `release-notes/tv_X.md` and merge it into
      `develop`. See `release-notes/README.md` for the required frontmatter
      (`version`, `codename`, `testReport`) and the fixed first line of the
      body — `prepare` refuses the release if either is wrong.

## 2. prepare

- [ ] Run the `Release / prepare draft` workflow with the merge commit sha.
      Unlike `apply`/`publish` below, this workflow has no dry-run option —
      running it creates the draft release for real.
- [ ] Gates G1–G4 must pass. A G2 failure means the branch moved after QA
      signed off — **do not override it.**
- [ ] A draft release appears. **No tag exists yet** — GitHub creates it only on
      publish, which is what keeps rollback free.

## 3. sign (key holder)

- [ ] `PYTHONPATH="$PWD/scripts" python3 -m release.cli sign tv_X`
- [ ] It re-verifies each binary's sha256 against `shasum.txt` before signing,
      so the key holder need not trust CI's bytes.
- [ ] Four `.sig` files are uploaded to the draft release.

## 4. apply

- [ ] Run `Release / apply` with `dry_run: true` (the default). Inspect the
      rendered `.eml`.
- [ ] Run it again with `dry_run: false`. Gates G5–G7 must pass.
- [ ] A solc-bin PR is opened and the release-request email goes out. The
      release is still a draft at this point — nothing is public.

## 5. Approval

- [ ] Wait for sign-off on the release-request email. Historically this gap has
      run from 5 days to 3 months.

## 6. publish

- [ ] Run `Release / publish` with the tag and the solc-bin PR number, and
      **explicitly set `dry_run: false`** — it also defaults to `true`, so an
      unmodified run only prints what would happen and publishes nothing.
- [ ] The draft becomes a release, GitHub creates the tag, the solc-bin PR
      merges. If the PR merge fails (or `solcbin_pr` was omitted), merge it by
      hand: `gh pr merge <pr> --repo tronprotocol/solc-bin --merge` — re-running
      `publish` will not retry the merge once the release shows as published.

## Rollback

Before publish, rollback is free: delete the draft release and close the
solc-bin PR. No tag exists, no binary is public. After publish, the only
remedy is a patch release.
