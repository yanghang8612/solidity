"""Read-only GitHub queries via the `gh` CLI."""

import json
from typing import Callable, List

Run = Callable[[List[str]], str]


def pr_for_commit(run: Run, repo: str, sha: str) -> int:
    """The number of the pull request associated with `sha`.

    The GitHub API returns all pull requests associated with the commit;
    this raises unless exactly one of them is found.
    """
    out = run(["gh", "api", f"repos/{repo}/commits/{sha}/pulls", "--jq", "[.[].number]"])
    numbers = json.loads(out)
    if len(numbers) != 1:
        raise ValueError(f"expected exactly one PR for {sha}, got {numbers}")
    return numbers[0]


def reviews(run: Run, repo: str, pr: int) -> List[dict]:
    """All reviews on `pr`, newest last."""
    out = run(["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "--paginate"])
    return json.loads(out)


_RELEASE_VIEW_FIELDS = ("tagName", "isDraft", "assets", "targetCommitish")


def release_view(run: Run, repo: str, tag: str) -> dict:
    """Metadata for the (possibly draft) release named `tag`.

    Fails closed if any of the four requested fields is absent from `gh`'s
    output -- the same fail-closed defect class already fixed in
    `cli.load_config`, `gates.g2_qa_approval`, `gates.g4_version_evidence`,
    and `Manifest.with_digests`. `cli.cmd_apply` indexes the returned dict
    with a bare `release["isDraft"]` before ever passing it to G7, and does
    no validation of its own -- this is the one place that can catch a
    missing field before it becomes a bare `KeyError` deep inside the apply
    stage, so it is validated here once for every caller rather than at each
    call site.
    """
    out = run([
        "gh", "release", "view", tag, "--repo", repo,
        "--json", "tagName,isDraft,assets,targetCommitish",
    ])
    data = json.loads(out)
    missing = [field for field in _RELEASE_VIEW_FIELDS if field not in data]
    if missing:
        raise ValueError(
            f"gh release view {tag} --repo {repo}: missing required field(s): {missing}"
        )
    return data
