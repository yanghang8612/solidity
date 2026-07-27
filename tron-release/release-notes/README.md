# Release Notes

One file per release: `tv_<version>.md` in this directory. It is read by the release pipeline
(`.github/workflows/release-prepare.yml`) and becomes the GitHub Release body verbatim.

Merge it into the release target branch in its own pull request **before** running the release
pipeline. `prepare` fails if the file is absent or inconsistent with the manifest.

## Format

```markdown
---
version: 0.8.27
codename: Democritus_v4.8.1
testReport: <url of the QA test report>
---
TRON Solidity compiler 0.8.27 is fully compatible with Ethereum Solidity 0.8.27.

## New Features from TRON

### Bugfixes:

 * ...

## Major changelog from Ethereum

### Language Features:

 * ...
```

The frontmatter is a flat `key: value` block — no nesting, no lists.

`## Major changelog from Ethereum` is a **curated selection** from the upstream
`Changelog.md` section for the corresponding version, not the whole thing.
