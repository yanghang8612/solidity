"""tron_release -- the release pipeline entry point."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Callable, List, Tuple

from release import checksums, evidence, fetch, gates, github, gitinfo, notes, solcbin
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS, Manifest, build_artifacts, derive_prs

# tron-release/release/cli.py -> release -> tron-release -> repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

Run = Callable[[List[str]], str]


def run(argv: List[str]) -> str:
    """Execute argv, returning stdout.

    `subprocess.CalledProcessError.__str__()` does not include stderr, which
    turns a failing `gh`/`aws` invocation into a useless traceback. Re-raise
    with the command and stderr spelled out. This must keep raising on
    failure -- `gitinfo.is_ancestor` depends on a non-zero exit propagating
    as an exception.
    """
    try:
        result = subprocess.run(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").strip()
        raise RuntimeError(
            f"command failed ({error.returncode}): {' '.join(argv)}\n{stderr}"
        ) from error
    return result.stdout


_REQUIRED_CONFIG_KEYS = ("repo", "solcBinRepo", "signingKeyFingerprint", "qaReviewers")


def load_config(repo_root: str) -> dict:
    """Read and validate `tron-release/config.json`, then apply env overrides.

    Fails closed on a missing or misconfigured key instead of letting a bare
    `KeyError` surface later, deep inside `cmd_prepare` -- the same fail-closed
    defect class already fixed in `gates.g2_qa_approval`, `gates.g4_version_evidence`,
    and `Manifest.with_digests`. `qaReviewers` is hand-edited by a maintainer
    before a real release, so a typo there is a live scenario, not a hypothetical.

    An absent `qaReviewers` is a broken config and raises here. A *present but
    empty* `qaReviewers` is the deliberate ship-state (nothing whitelisted yet)
    and is intentionally let through -- `gates.g2_qa_approval` already fails
    closed on that case with its own descriptive error.
    """
    path = os.path.join(repo_root, "tron-release", "config.json")
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    missing = [key for key in _REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        raise ValueError(f"{path} is missing required key(s): {missing}")

    if not isinstance(config["qaReviewers"], list):
        raise ValueError(
            f"{path}: 'qaReviewers' must be a list, got "
            f"{type(config['qaReviewers']).__name__}"
        )

    string_overrides = {
        "repo": "TRON_RELEASE_REPO",
        "solcBinRepo": "TRON_RELEASE_SOLC_BIN_REPO",
        "signingKeyFingerprint": "TRON_RELEASE_SIGNING_KEY_FINGERPRINT",
    }
    for key, env_name in string_overrides.items():
        if os.environ.get(env_name):
            config[key] = os.environ[env_name]

    reviewers = os.environ.get("TRON_RELEASE_QA_REVIEWERS")
    if reviewers:
        config["qaReviewers"] = [login.strip() for login in reviewers.split(",") if login.strip()]

    for key in ("repo", "solcBinRepo", "signingKeyFingerprint"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{path}: {key!r} must be a non-empty string")
    return config


def validate_manifest(man: Manifest, tag: str) -> None:
    """Bind an externally downloaded manifest to the requested release."""
    if man.tag != tag:
        raise GateError(f"manifest tag {man.tag!r} does not match requested tag {tag!r}")
    if man.tag != f"tv_{man.version}":
        raise GateError(
            f"manifest tag {man.tag!r} is inconsistent with version {man.version!r}"
        )
    if len(man.commit) != 40 or any(c not in _HEX_CHARS for c in man.commit):
        raise GateError(f"manifest commit is not a full hexadecimal SHA: {man.commit!r}")
    expected = build_artifacts(man.long_version)
    actual_projection = [(a.name, a.solc_bin_dir, a.solc_bin_path) for a in man.artifacts]
    expected_projection = [(a.name, a.solc_bin_dir, a.solc_bin_path) for a in expected]
    if actual_projection != expected_projection:
        raise GateError(
            "manifest artifact names/paths do not match the release pipeline's fixed projection"
        )


def import_release_key(run: Run, workdir: str) -> None:
    key_path = os.path.join(workdir, "signing-key.asc")
    if not os.path.isfile(key_path):
        raise GateError("signing-key.asc is missing from the draft release")
    run(["gpg", "--batch", "--import", key_path])


def project_version(run: Run, repo_root: str) -> str:
    return run([os.path.join(repo_root, "scripts", "get_version.sh")]).strip()


def build_source_tarball(run: Run, repo_root: str) -> str:
    """Run create_source_tarball.sh, preserving any pre-existing prerelease.txt.

    `scripts/create_source_tarball.sh` names the tarball `solidity_<version>.tar.gz`
    only when `prerelease.txt` exists in the repo root *and* is zero bytes;
    otherwise it produces a `-nightly-<date>-<hash>` name. The file is untracked
    by git, so the release process owns it -- but only the copy it creates itself:

      - absent             -> create it empty here, then remove it again after.
      - present, empty     -> leave it exactly as found; it was not ours to delete.
      - present, non-empty -> refuse. That means a nightly build is in progress
                              on this checkout, not a release.
    """
    marker = os.path.join(repo_root, "prerelease.txt")
    created_by_us = False
    if os.path.exists(marker):
        if os.path.getsize(marker) > 0:
            raise GateError(
                f"{marker} exists and is not empty; refusing to build a release "
                "tarball (this looks like a nightly build, not a release)"
            )
    else:
        with open(marker, "w", encoding="utf-8"):
            pass
        created_by_us = True

    try:
        run([os.path.join(repo_root, "scripts", "create_source_tarball.sh")])
    finally:
        if created_by_us:
            os.unlink(marker)

    version = project_version(run, repo_root)
    return os.path.join(repo_root, "upload", f"solidity_{version}.tar.gz")


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def cmd_prepare(args, run: Run = run, repo_root: str = REPO_ROOT) -> int:
    config = load_config(repo_root)
    repo = config["repo"]
    commit = run(["git", "rev-parse", args.commit]).strip()

    version = project_version(run, repo_root)
    tag = f"tv_{version}"
    notes_path = os.path.join(
        repo_root, "tron-release", "release-notes", f"{tag}.md"
    )
    front, body = notes.load(notes_path, version)

    parents = gitinfo.parents(run, commit)
    gates.g1_merge_commit(parents, gitinfo.is_ancestor(run, commit, args.release_ref))

    pull = github.pr_for_commit(run, repo, commit)
    approval = gates.g2_qa_approval(parents, github.reviews(run, repo, pull),
                                     config["qaReviewers"])
    approval["pr"] = pull

    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-release-")
    artifacts_dir = os.path.join(workdir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    names = [name for name, _, _ in ARTIFACT_SPECS]
    fetch.verify_run(run, repo, args.build_run_id, commit)
    fetch.download(run, repo, args.build_run_id, names, artifacts_dir)
    gates.g3_artifacts_present(os.listdir(artifacts_dir))

    short_commit = commit[:8]
    long_version = f"{version}+commit.{short_commit}"

    evidence_by_name = {}
    for name in names:
        path = os.path.join(artifacts_dir, name)
        if name in ("solc-static-linux", "soljson.js"):
            os.chmod(path, 0o755)
        evidence_by_name[name] = evidence.extract(
            run, name, path, version=version, short_commit=short_commit
        )
    gates.g4_version_evidence(evidence_by_name, long_version, short_commit)

    digests = {name: checksums.digests(os.path.join(artifacts_dir, name))
               for name in names}

    subjects = gitinfo.merge_subjects(
        run, gitinfo.previous_tag(run, tag), commit,
        exclude_refs=gitinfo.upstream_tags(run),
    )
    man = Manifest(
        version=version, tag=tag, codename=front["codename"], commit=commit,
        tested_commit=parents[1], qa_approval=approval,
        prs=derive_prs(subjects, release_pr=pull),
        artifacts=build_artifacts(long_version),
    ).with_digests(digests)

    write(os.path.join(workdir, "manifest.json"), man.to_json())
    write(os.path.join(workdir, "shasum.txt"), checksums.render(man, "sha256"))
    write(os.path.join(workdir, "keccak256.txt"), checksums.render(man, "keccak256"))
    body_path = os.path.join(workdir, "body.md")
    write(body_path, body)

    tarball = build_source_tarball(run, repo_root)

    if args.dry_run:
        print(f"[dry-run] would create draft release {tag} at {commit}")
        print(f"[dry-run] workdir: {workdir}")
        return 0

    assets = [os.path.join(artifacts_dir, n) for n in names]
    assets += [os.path.join(workdir, f) for f in
               ("shasum.txt", "keccak256.txt", "manifest.json")]
    assets.append(tarball)
    run(["gh", "release", "create", tag, "--repo", repo, "--draft",
         "--target", commit, "--title", man.release_name,
         "--notes-file", body_path] + assets)
    print(f"draft release {tag} created at {commit}")
    return 0


_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(c in _HEX_CHARS for c in value)


def _parse_shasum_line(line: str) -> Tuple[str, str]:
    """Split one shasum.txt line into (lowercase digest, name), or raise GateError.

    The only accepted format is exactly `<64-hex sha256>  <name>` -- two
    spaces, no more, no fewer. `str.partition("  ")` finds the *first* such
    pair, so a line with three (or more) spaces between digest and name would
    otherwise be silently accepted with a mangled, space-prefixed name; the
    `name[0] == " "` check below catches that instead.
    """
    digest, sep, name = line.partition("  ")
    if sep != "  " or not name or name[0] == " " or not _is_sha256_hex(digest):
        raise GateError(f"shasum.txt: malformed line: {line!r}")
    return digest.lower(), name


def verify_downloaded(artifacts_dir: str, shasum_text: str) -> None:
    """Recompute sha256 of every downloaded artifact and compare with shasum.txt.

    This recomputation is what lets the signing-key holder refuse to simply
    trust that CI produced the bytes it claims -- so every way `shasum.txt`
    could fail to fully and correctly describe the four downloaded artifacts
    must raise `GateError` rather than silently pass or skip:

      - no entries at all
      - a line that is not exactly `<64-hex sha256>  <name>`
      - a name that is not one of the four release artifacts (a rogue extra
        line must never cause an unexpected file to get signed)
      - a named artifact that was not actually downloaded
      - a digest that does not match the recomputed sha256
      - fewer than all configured artifacts covered
        not silently succeed)
    """
    entries = [line for line in shasum_text.split("\n") if line.strip()]
    if not entries:
        raise GateError("shasum.txt has no entries")

    names = [name for name, _, _ in ARTIFACT_SPECS]
    valid_names = set(names)

    seen = {}
    for line in entries:
        digest, name = _parse_shasum_line(line)
        if name not in valid_names:
            raise GateError(
                f"shasum.txt names {name!r}, which is not one of the release "
                f"artifacts {names}"
            )
        seen[name] = digest

    for name in names:
        if name not in seen:
            continue
        path = os.path.join(artifacts_dir, name)
        if not os.path.exists(path):
            raise GateError(f"{name} was not downloaded")
        actual = checksums.sha256(path)
        if actual != seen[name]:
            raise GateError(f"{name}: sha256 {actual} != {seen[name]} from shasum.txt")

    missing = [name for name in names if name not in seen]
    if missing:
        raise GateError(f"shasum.txt does not cover: {missing}")


def cmd_sign(args, run: Run = run, repo_root: str = REPO_ROOT) -> int:
    """Download a draft release's binaries, verify them, and sign them.

    This is the only command the signing-key holder runs, and that person is
    not a project developer -- they have none of this pipeline's AWS
    credentials, PATs, or mail secrets, so this command must depend on
    nothing but `gh` and `gpg`. `verify_downloaded` is what lets them refuse
    to simply trust that CI produced the bytes it claims.
    """
    config = load_config(repo_root)
    repo = config["repo"]
    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-sign-")
    names = [name for name, _, _ in ARTIFACT_SPECS]

    patterns = []
    for name in names + ["shasum.txt", "manifest.json"]:
        patterns += ["-p", name]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "shasum.txt"), "r", encoding="utf-8") as handle:
        verify_downloaded(workdir, handle.read())
    print(f"all {len(names)} artifacts match shasum.txt")

    with open(os.path.join(workdir, "manifest.json"), "r", encoding="utf-8") as handle:
        man = Manifest.from_json(handle.read())
    validate_manifest(man, args.tag)

    signatures = []
    for name in names + ["manifest.json"]:
        path = os.path.join(workdir, name)
        sig = path + ".sig"
        if os.path.exists(sig):
            os.unlink(sig)
        run(["gpg", "--armor", "--detach-sign", "--local-user",
             config["signingKeyFingerprint"], "--output", sig, path])
        signatures.append(sig)
        print(f"signed {name}")

    public_key = os.path.join(workdir, "signing-key.asc")
    write(public_key, run([
        "gpg", "--batch", "--armor", "--export", config["signingKeyFingerprint"],
    ]))
    if os.path.getsize(public_key) == 0:
        raise GateError("gpg exported an empty signing-key.asc")

    if args.dry_run:
        print(f"[dry-run] would upload {len(signatures)} signatures to {args.tag}")
        return 0

    run(["gh", "release", "upload", args.tag, "--repo", repo, "--clobber"]
        + signatures + [public_key])
    print(f"uploaded {len(signatures)} signatures and the public key to {args.tag}")
    return 0


def cmd_apply(args, run: Run = run, repo_root: str = REPO_ROOT) -> int:
    """Verify the signed draft and open or reuse its solc-bin PR."""
    config = load_config(repo_root)
    repo, solcbin_repo = config["repo"], config["solcBinRepo"]
    names = [name for name, _, _ in ARTIFACT_SPECS]

    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-apply-")
    patterns = []
    for name in names:
        patterns += ["-p", name, "-p", name + ".sig"]
    patterns += [
        "-p", "shasum.txt", "-p", "keccak256.txt",
        "-p", "manifest.json", "-p", "manifest.json.sig", "-p", "signing-key.asc",
    ]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "manifest.json"), "r", encoding="utf-8") as handle:
        man = Manifest.from_json(handle.read())

    validate_manifest(man, args.tag)
    import_release_key(run, workdir)
    gates.g5_signatures(run, config, workdir, names + ["manifest.json"])
    gates.g6_asset_digests(workdir, man)
    print("G5/G6 passed")

    pr_url = solcbin.open_solcbin_pr(run, solcbin_repo, man, workdir, dry_run=args.dry_run)

    release = github.release_view(run, repo, args.tag)
    if release["targetCommitish"] != man.commit:
        raise GateError(
            f"draft release targets {release['targetCommitish']}, manifest targets {man.commit}"
        )
    gates.g7_ready_for_review(release["isDraft"], pr_url)
    print(f"solc-bin PR ready for review: {pr_url}")
    return 0


def cmd_publish(args, run: Run = run, repo_root: str = REPO_ROOT) -> int:
    """Re-verify signatures and digests, then flip the draft release to
    published and merge the solc-bin PR.

    This is the pipeline's one irreversible step: `gh release edit
    --draft=false` is the moment GitHub actually creates the `tag` tag.
    Everything before this point -- a draft release, a solc-bin PR that is
    open but unmerged -- is still cheaply reversible (delete the draft,
    close the PR); this call is not, which is why `release_view` is fetched
    fresh here rather than assumed, exactly as `cmd_apply`'s G7 check does.
    Re-running this against an already-published release must be a safe
    no-op, not a redundant (and on GitHub's side, failing) `gh release edit`.

    The draft-to-publish gap may be long enough for someone to swap a binary on the
    draft release out of band. G5 (signatures) and G6 (asset digests vs. the
    manifest) already ran once, back in `apply`, but that check goes stale
    the instant a human can touch the externally-reachable release in
    between -- the same "never trust previously-observed state, re-check
    what's reachable right now" principle G7 already applies
    to `isDraft` and the solc-bin PR URL. So immediately before the flip,
    this command re-downloads the draft's *current* binaries, signatures,
    and manifest -- never a cached copy from `apply` -- and re-runs G5/G6
    against them, exactly as `cmd_apply` itself does after its own download.
    A tampered binary is caught here, before publish, not after.

    This re-verification (download + G5 + G6) happens even under --dry-run:
    that is exactly the value of a publish dry-run. Only `gh release edit`
    and `gh pr merge` are skipped in --dry-run.

    Recovery note: because the already-published branch returns early, if the
    `gh release edit` succeeds but the subsequent `gh pr merge` fails (or
    `--solcbin-pr` was omitted on the first successful run), re-running this
    command will NOT retry the merge -- it will see the release is already
    published and no-op. Merge the solc-bin PR by hand in that case:
    `gh pr merge <pr> --repo <solcBinRepo> --merge`. Only the tag creation is
    irreversible; the PR merge is a separate, manually retryable step.
    """
    config = load_config(repo_root)
    repo = config["repo"]

    release = github.release_view(run, repo, args.tag)
    if not release["isDraft"]:
        print(f"{args.tag} is already published")
        return 0

    names = [name for name, _, _ in ARTIFACT_SPECS]
    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-publish-")
    patterns = []
    for name in names:
        patterns += ["-p", name, "-p", name + ".sig"]
    patterns += [
        "-p", "manifest.json", "-p", "manifest.json.sig", "-p", "signing-key.asc",
    ]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "manifest.json"), "r", encoding="utf-8") as handle:
        man = Manifest.from_json(handle.read())

    validate_manifest(man, args.tag)
    if release["targetCommitish"] != man.commit:
        raise GateError(
            f"draft release targets {release['targetCommitish']}, manifest targets {man.commit}"
        )
    import_release_key(run, workdir)
    gates.g5_signatures(run, config, workdir, names + ["manifest.json"])
    gates.g6_asset_digests(workdir, man)
    print("G5/G6 passed")

    if args.dry_run:
        if args.solcbin_pr:
            print(f"[dry-run] would publish {args.tag} and merge solc-bin PR "
                  f"{args.solcbin_pr}")
        else:
            print(f"[dry-run] would publish {args.tag}; no solc-bin PR to merge")
        return 0

    run(["gh", "release", "edit", args.tag, "--repo", repo, "--draft=false"])
    print(f"published {args.tag}; GitHub created the tag")

    if args.solcbin_pr:
        run(["gh", "pr", "merge", args.solcbin_pr, "--repo", config["solcBinRepo"],
             "--merge"])
        print(f"merged solc-bin PR {args.solcbin_pr}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tron_release")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="build artifacts and create a draft release")
    prepare.add_argument("commit", help="the merge commit on develop to release")
    prepare.add_argument(
        "--build-run-id", required=True,
        help="successful build.yml Actions run whose head SHA equals the release commit",
    )
    prepare.add_argument(
        "--release-ref", default="origin/develop",
        help="remote branch that must contain the release merge commit",
    )
    prepare.add_argument("--workdir", default=None,
                          help="directory for manifest/checksums/artifacts (default: a fresh tmpdir)")
    prepare.add_argument("--dry-run", action="store_true",
                          help="do everything except publish the draft release")
    prepare.set_defaults(func=cmd_prepare)

    sign = sub.add_parser(
        "sign", help="download, verify, and sign a draft release's binaries (key holder only)"
    )
    sign.add_argument("tag", help="e.g. tv_0.8.27")
    sign.add_argument("--workdir", default=None,
                       help="directory to download artifacts into (default: a fresh tmpdir)")
    sign.add_argument("--dry-run", action="store_true",
                       help="sign but do not upload the signatures")
    sign.set_defaults(func=cmd_sign)

    apply_cmd = sub.add_parser(
        "apply", help="verify the signed draft and open the solc-bin PR"
    )
    apply_cmd.add_argument("tag", help="e.g. tv_0.8.27")
    apply_cmd.add_argument("--workdir", default=None,
                            help="directory to download artifacts into (default: a fresh tmpdir)")
    apply_cmd.add_argument("--dry-run", action="store_true",
                            help="commit the solc-bin projection locally, but do not push "
                                 "or open the PR")
    apply_cmd.set_defaults(func=cmd_apply)

    publish = sub.add_parser(
        "publish", help="publish the draft release after approval (irreversible)"
    )
    publish.add_argument("tag", help="e.g. tv_0.8.27")
    publish.add_argument("--solcbin-pr", default=None,
                          help="solc-bin PR number to merge (optional)")
    publish.add_argument("--workdir", default=None,
                          help="directory to download and re-verify assets into "
                               "(default: a fresh tmpdir)")
    publish.add_argument("--dry-run", action="store_true",
                          help="do everything except publish the release or merge the PR")
    publish.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GateError as error:
        print(f"GATE FAILED: {error}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
