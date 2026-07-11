---
name: tron-release
description: Use when preparing a TRON Solidity release - drafts release-notes/tv_X.md from the upstream Changelog and TRON PRs, then drives the prepare/sign/apply/publish pipeline.
---

# TRON Solidity Release

The deterministic work lives in `scripts/release/` (`prepare`, `sign`, `apply`,
`publish`) and three `.github/workflows/release-*.yml`. `ReleaseChecklist.md` at the
repo root is the exhaustive, gate-by-gate walkthrough and prerequisites list — read it
once. This skill carries the judgement work the scripts can't: deciding what goes in
the release notes, and shepherding a human through the pipeline.

## Checklist

1. **Draft `release-notes/tv_X.md` and merge it into `develop` before the release PR
   merges — not after.** `prepare` checks out the exact commit you hand it (not
   `develop`'s current tip) and reads the notes file from that checkout, so the notes
   commit must already be an ancestor of the release commit or the file simply won't
   be there. Concretely: open the release PR (`release_X` -> `develop`), get it
   QA-approved, then open a **separate** PR containing only the notes file and merge
   that into `develop` first — only then merge the release PR. This is
   `ReleaseChecklist.md`'s "before the release PR is merged" section, spelled out.
   See "Drafting the notes" below for what goes in the file.

2. **Identify the release commit**, once the release PR has actually merged: it's the
   merge commit on `develop` that merged it. `git log --merges -1 origin/develop`.

3. **Verify QA approved that exact commit.**
   ```
   gh api --paginate repos/tronprotocol/solidity/pulls/<N>/reviews \
     --jq '.[] | select(.state=="APPROVED") | {user: .user.login, commit_id}'
   ```
   `commit_id` must equal `<merge>^2` — the release commit's second parent, which is
   exactly what `gates.g2_qa_approval` checks (as `parents[1]`) before `prepare` will
   proceed. **If it does not match, stop.** The branch moved after QA signed off;
   tell the human, do not work around it.

4. **Run the pipeline, in order.** `prepare` checks gates G1-G4; `apply` checks
   G5-G7; `sign` and `publish` aren't part of that G-numbering — `sign` re-verifies
   every artifact's sha256 against `shasum.txt` itself before signing, and `publish`
   only checks whether the release is already published.
   - `gh workflow run "Release / prepare draft" -f commit=<sha>` — this workflow has
     no dry-run input; it creates the draft release for real. A draft has no public
     tag yet.
   - Tell the signing-key holder to run, locally (no CI workflow triggers this step —
     by design it needs nothing but `gh`/`gpg`, never AWS/PAT/mail secrets):
     `PYTHONPATH="$PWD/scripts" python3 -m release.cli sign tv_X`
   - `gh workflow run "Release / apply" -f tag=tv_X -f dry_run=true` (the default).
     This previews only — no push, no PR, no mail. Review the rendered request email
     by downloading the `release-request-email` artifact the dry-run uploads (it
     contains `release-request.eml` and the `.tgz` bundle), then rerun with
     `-f dry_run=false`. The real run opens the solc-bin PR (branch
     `binaries-for-<version>`; recover the number later with `gh pr list --repo
     tronprotocol/solc-bin --head binaries-for-<version>` if needed) and sends the
     release-request email for real.
   - Wait for humans to approve the release-request email before going further.
     Historically this gap has run from 5 days to 3 months.
   - `gh workflow run "Release / publish" -f tag=tv_X -f solcbin_pr=<N> -f
     dry_run=false` — `dry_run` defaults to `true` here too, so an unmodified run
     only prints what it would do and publishes nothing. This is the one
     irreversible step: it flips the draft to published, which is the point GitHub
     treats the tag as permanent.

## Drafting the notes

Frontmatter is flat `key: value`, no nesting: `version`, `codename` (ask the user —
the matching java-tron GreatVoyage release name, e.g. `Democritus_v4.8.1`),
`testReport` (a URL). `notes.load` rejects the file if any key is missing or if
`version` doesn't match the release commit's own version.

Body, in this exact order:
1. First line, byte-for-byte (`notes.load` checks it literally): `TRON Solidity
   compiler X is fully compatible with Ethereum Solidity X.`
2. `## New Features from TRON` — `scripts/release/manifest.py:derive_prs` gives you
   two flat PR-number lists, `features` and `upstreamMerge`; it does not categorize
   anything or write prose. Grouping the `features` PRs under headings like
   `### Bugfixes:` / `### Build System:` (look each one up with `gh pr view <N>
   --repo tronprotocol/solidity`) is the judgement call this section exists for.
3. `## Major changelog from Ethereum` — a **curated selection** from the matching
   version section of `Changelog.md` at the repo root, not the whole section. Pick
   what a TRON contract author would care about; reusing upstream's own category
   headings (`Language Features:`, `Compiler Features:`, `Bugfixes:`, ...) is fine.

## Never

- Never override a G2 failure. It is the only mechanism guaranteeing the released
  code is the exact code QA tested.
- Never publish before the release-request email has been approved by humans. The
  draft-to-publish gap has historically run from 5 days to 3 months.
- Never hand-edit `shasum.txt`, `keccak256.txt`, or `list.json`. They are projections
  of `manifest.json` — regenerate them by re-running the pipeline, not by editing
  them directly.
