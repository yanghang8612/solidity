"""tron_release -- the release pipeline entry point."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Callable, List, Tuple

from release import bundle, checksums, evidence, fetch, gates, github, gitinfo, mailer, notes, solcbin
from release.errors import GateError
from release.manifest import ARTIFACT_SPECS, Manifest, build_artifacts, derive_prs

# scripts/release/cli.py -> scripts/release -> scripts -> repo root.
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


def require_env(name: str) -> str:
    """Read an environment variable, failing closed with a descriptive error."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"required environment variable {name!r} is not set")
    return value


_REQUIRED_CONFIG_KEYS = ("repo", "solcBinRepo", "signingKeyFingerprint", "qaReviewers")


def load_config(repo_root: str) -> dict:
    """Read and validate `.github/release-config.json`.

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
    path = os.path.join(repo_root, ".github", "release-config.json")
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

    return config


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
    notes_path = os.path.join(repo_root, "release-notes", f"{tag}.md")
    front, body = notes.load(notes_path, version)

    # Fork-test mode (fetch.artifact_release() set) borrows artifacts from a
    # published release instead of S3, releases off the test branch rather than
    # develop, and can't match version evidence. It therefore relaxes the develop
    # ancestry check (G1 still enforces the 2-parent merge shape), needs no S3
    # bucket, and downgrades G4 to a warning below. Production leaves it unset.
    borrowed = fetch.artifact_release()

    parents = gitinfo.parents(run, commit)
    on_develop = True if borrowed else gitinfo.is_ancestor(run, commit, "origin/develop")
    gates.g1_merge_commit(parents, on_develop)

    pull = github.pr_for_commit(run, repo, commit)
    approval = gates.g2_qa_approval(parents, github.reviews(run, repo, pull),
                                     config["qaReviewers"])
    approval["pr"] = pull

    bucket = "" if borrowed else require_env("S3_BUCKET_PROD")
    gates.g3_artifacts_present(fetch.list_keys(run, bucket, commit))

    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-release-")
    artifacts_dir = os.path.join(workdir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    names = [name for name, _, _ in ARTIFACT_SPECS]
    fetch.download(run, bucket, commit, names, artifacts_dir)

    short_commit = commit[:8]
    long_version = f"{version}+commit.{short_commit}"

    evidence_by_name = {}
    for name in names:
        path = os.path.join(artifacts_dir, name)
        os.chmod(path, 0o755)
        evidence_by_name[name] = evidence.extract(
            run, name, path, version=version, short_commit=short_commit
        )
    if borrowed:
        # The borrowed artifacts belong to another commit, so their version
        # evidence cannot match this fork commit's longVersion. Downgrade G4
        # to a warning rather than failing -- this branch is unreachable in
        # production, where artifacts always come from S3 keyed by this commit.
        try:
            gates.g4_version_evidence(evidence_by_name, long_version, short_commit)
        except GateError as error:
            print(f"[fork-test] G4 downgraded to warning: {error}")
    else:
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
      - fewer than all four artifacts covered (signing three of four must
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
    for name in names + ["shasum.txt"]:
        patterns += ["-p", name]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "shasum.txt"), "r", encoding="utf-8") as handle:
        verify_downloaded(workdir, handle.read())
    print("all four artifacts match shasum.txt")

    signatures = []
    for name in names:
        path = os.path.join(workdir, name)
        sig = path + ".sig"
        if os.path.exists(sig):
            os.unlink(sig)
        run(["gpg", "--armor", "--detach-sign", "--local-user",
             config["signingKeyFingerprint"], "--output", sig, path])
        signatures.append(sig)
        print(f"signed {name}")

    if args.dry_run:
        print(f"[dry-run] would upload {len(signatures)} signatures to {args.tag}")
        return 0

    run(["gh", "release", "upload", args.tag, "--repo", repo, "--clobber"] + signatures)
    print(f"uploaded {len(signatures)} signatures to draft release {args.tag}")
    return 0


def cmd_apply(args, run: Run = run, repo_root: str = REPO_ROOT) -> int:
    """Open the solc-bin PR and send the release-request email.

    This is the only stage that emits anything outward, and the ordering
    below is deliberate and load-bearing, not incidental:

      1. download the signed release assets + manifest
      2. G5 (signatures), G6 (asset digests match the manifest)
      3. open (or reuse) the solc-bin PR
      4. pack the `<stem>.tgz` email attachment
      5. G7 -- fetched fresh from `github.release_view`, never assumed --
         must see the release still a draft and the solc-bin PR URL non-empty
      6. only then render and (outside --dry-run) send the request email

    The email is a *request*, not an announcement: G7 is what guarantees
    that when it goes out, no tag exists yet, the release is still a draft,
    and the solc-bin PR is open but unmerged -- so the worst case of ever
    sending it wrongly is an embarrassing email, never a poisoned CDN.

    Every `RELEASE_MAIL_*` secret is read via `require_env` and held only in
    local variables passed straight to `mailer` -- never printed, logged, or
    written anywhere (the rendered `.eml` necessarily contains the
    recipients, which is fine; the credentials are never part of it).
    """
    config = load_config(repo_root)
    repo, solcbin_repo = config["repo"], config["solcBinRepo"]
    names = [name for name, _, _ in ARTIFACT_SPECS]

    workdir = args.workdir or tempfile.mkdtemp(prefix="tron-apply-")
    patterns = []
    for name in names:
        patterns += ["-p", name, "-p", name + ".sig"]
    patterns += ["-p", "shasum.txt", "-p", "keccak256.txt", "-p", "manifest.json"]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "manifest.json"), "r", encoding="utf-8") as handle:
        man = Manifest.from_json(handle.read())

    gates.g5_signatures(run, config, workdir, names)
    gates.g6_asset_digests(workdir, man)
    print("G5/G6 passed")

    pr_url = solcbin.open_solcbin_pr(run, solcbin_repo, man, workdir, dry_run=args.dry_run)
    tarball = bundle.pack_tgz(man, workdir, repo_root)

    release = github.release_view(run, repo, args.tag)
    gates.g7_ready_to_mail(release["isDraft"], pr_url)

    notes_path = os.path.join(repo_root, "release-notes", f"{man.tag}.md")
    front, _ = notes.load(notes_path, man.version)

    recipients = mailer.parse_recipients(require_env("RELEASE_MAIL_RECIPIENTS"))
    message = mailer.build_message(man, front, pr_url, recipients, tarball)

    if args.dry_run:
        eml = os.path.join(workdir, "release-request.eml")
        write(eml, message.as_string())
        print(f"[dry-run] release request email rendered to {eml}; not sent")
        return 0

    # SMTP credentials are read only on the real-send path: a --dry-run
    # preview needs none of them.
    credentials = {
        "host": require_env("RELEASE_MAIL_HOST"),
        "port": require_env("RELEASE_MAIL_PORT"),
        "user": require_env("RELEASE_MAIL_USER"),
        "password": require_env("RELEASE_MAIL_PASSWORD"),
    }
    mailer.send(message, credentials)
    print(f"release request email sent for {man.tag}")
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

    The draft-to-publish gap can run from 5 days to 3 months (see
    ReleaseChecklist.md) -- long enough for someone to swap a binary on the
    draft release out of band. G5 (signatures) and G6 (asset digests vs. the
    manifest) already ran once, back in `apply`, but that check goes stale
    the instant a human can touch the externally-reachable release in
    between -- the same "never trust previously-observed state, re-check
    what's reachable right now" principle `g7_ready_to_mail` already applies
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
    patterns += ["-p", "manifest.json"]
    run(["gh", "release", "download", args.tag, "--repo", repo,
         "-D", workdir, "--clobber"] + patterns)

    with open(os.path.join(workdir, "manifest.json"), "r", encoding="utf-8") as handle:
        man = Manifest.from_json(handle.read())

    gates.g5_signatures(run, config, workdir, names)
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
        "apply", help="open the solc-bin PR and send the release-request email"
    )
    apply_cmd.add_argument("tag", help="e.g. tv_0.8.27")
    apply_cmd.add_argument("--workdir", default=None,
                            help="directory to download artifacts into (default: a fresh tmpdir)")
    apply_cmd.add_argument("--dry-run", action="store_true",
                            help="render the email and commit locally, but do not push, "
                                 "open the PR, or send mail")
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
