# Running the release pipeline on a fork (fork-test mode)

This branch (`fork-test`) is the production pipeline plus scaffolding that lets
the whole thing run end-to-end on `yanghang8612/solidity` with **no self-hosted
runner and no S3 bucket**. None of this scaffolding belongs in the PR to
`tronprotocol/solidity` — it lives only here.

What the scaffolding changes, all behind switches so production is untouched:

- **`runs-on: macos-latest`** in all three workflows (was `self-hosted`).
- **`fetch.py`** grows a `TRON_RELEASE_ARTIFACT_RELEASE` override: when set to
  `<owner>/<repo>@<tag>`, `prepare` pulls the four binaries from that published
  GitHub release instead of S3. Unset ⇒ S3, exactly as production.
- **G4 downgraded to a warning** in `prepare` when that override is active —
  the borrowed `tv_0.8.27` binaries carry `0.8.27+commit.19164bed`, which can't
  match a fork commit's version. Every other gate (G1/G2/G3/G5/G6/G7) runs for real.
- **`.github/release-config.json`** points at `yanghang8612/*` and your own
  signing key `E3673E008F6D506E`.

## 0. One-time setup on `yanghang8612/solidity`

1. **Push this branch to your fork:**
   ```
   git push fork fork-test
   ```
   GitHub Actions reads workflows from the branch you dispatch, so the workflow
   files must exist on the fork.

2. **Enable Actions** on the fork (Settings → Actions → Allow all actions) if
   it isn't already.

3. **Repo variable + placeholder mail secrets** — already configured by the
   assistant: `TRON_RELEASE_ARTIFACT_RELEASE = tronprotocol/solidity@tv_0.8.27`
   and the five `RELEASE_MAIL_*` placeholders. `S3_BUCKET_PROD` is not needed in
   fork-test mode.

4. **`SOLC_BIN_TOKEN`** — you must add this one yourself (it's a real credential,
   so it shouldn't pass through the assistant). Generate a fine-grained PAT with
   `contents:write` + `pull_requests:write` on `yanghang8612/solc-bin`, then:
   ```
   gh secret set SOLC_BIN_TOKEN --repo yanghang8612/solidity --body "<your-PAT>"
   ```
   Only `apply` needs it; `prepare` and `sign` run without it.

Nothing here sends email or publishes anything until you explicitly run
`publish` without `--dry-run`.

## Why the test branch is the baseline, not develop

Your fork's `develop` is 479 commits behind tronprotocol, is version 0.8.26, and
does not contain `scripts/release/` — so it can't host the pipeline. `prepare`
runs `python3 -m release.cli` from the *checked-out commit's* tree, so that
commit must carry this code. We therefore treat the **`fork-test` branch itself
as the baseline** (it's tronprotocol develop 9c4253d2b + the pipeline + this
scaffolding, version 0.8.29). Fork-test mode relaxes G1's develop-ancestry check
accordingly (it still enforces the 2-parent merge shape).

## 1. Create the G2 target: a merge commit with your own approval, on fork-test

G2 binds the release to a QA-approved commit. On the fork you play QA. The PR's
**base must be `fork-test`** so the merge commit carries the pipeline code:

1. Branch off `fork-test`, make a trivial change, open a PR into `fork-test`:
   ```
   git fetch fork fork-test
   git checkout -b fork-test/dummy-release fork/fork-test
   echo "" >> README.md && git commit -asm "test: fork-test dummy release"
   git push fork fork-test/dummy-release
   gh pr create --repo yanghang8612/solidity --base fork-test \
     --head fork-test/dummy-release --title "fork-test dummy release" --body ""
   ```
2. **Approve the PR as one of your alt accounts** (GitHub blocks self-approval on
   your own PR — use `lily309`/`TonyStank911`/`ouy95917`), and put that same
   login into `qaReviewers` in `.github/release-config.json` on the `fork-test`
   branch (it currently lists `yanghang8612`). Approve at the PR's current head.
3. **Merge it** (`gh pr merge <n> --repo yanghang8612/solidity --merge`). The
   resulting merge commit on `fork-test` is your release commit; its `^2` is what
   your approval pointed at. Note the merge commit sha — that's the `commit`
   input to `prepare`.

> The release-notes file `release-notes/tv_0.8.29.md` already exists on
> `fork-test`, so the merge commit will carry it (version 0.8.29 ⇒ that name).

## 2. Write the release notes and merge them

```
# on develop, add release-notes/tv_<version>.md (version = fork's CMakeLists, e.g. 0.8.29)
```
Use `/tron-release` (the skill) to draft it, or copy the three-section skeleton
from `release-notes/README.md`. Merge it into `develop` before running prepare.

## 3. prepare

```
gh workflow run "Release / prepare draft" --repo yanghang8612/solidity \
  --ref fork-test -f commit=<merge-commit-sha>
```
Watch it in the Actions tab. Expect: G1/G2/G3 pass, **G4 prints
`[fork-test] G4 downgraded to warning`**, a draft release appears on the fork.

## 4. sign (locally, your machine, your GPG key)

```
PYTHONPATH=scripts python3 -m release.cli sign tv_<version>
```
Downloads the draft's binaries, re-checks sha256, signs with `E3673E008F6D506E`,
uploads four `.sig` files.

## 5. apply (dry-run first)

```
gh workflow run "Release / apply" --repo yanghang8612/solidity \
  --ref fork-test -f tag=tv_<version> -f dry_run=true
```
Expect G5/G6/G7 to pass; a solc-bin PR opens on `yanghang8612/solc-bin`; the
rendered email uploads as the `release-request-email` artifact (download and
inspect it). Nothing is sent.

## 6. publish (dry-run)

```
gh workflow run "Release / publish" --repo yanghang8612/solidity \
  --ref fork-test -f tag=tv_<version> -f dry_run=true
```
Re-verifies G5/G6, prints what it *would* do, flips nothing.

Drop `-f dry_run=true` on apply/publish only when you want to see the real
side effects on your own fork (email actually sends, draft actually publishes,
solc-bin PR actually merges). Everything targets `yanghang8612/*`, never
`tronprotocol/*`.
