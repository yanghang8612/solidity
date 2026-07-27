"""Read-only git queries used by the release pipeline."""

from typing import Callable, List, Optional

Run = Callable[[List[str]], str]


def parents(run: Run, commit: str) -> List[str]:
    """Return the parent hashes of `commit`, in order."""
    out = run(["git", "rev-list", "--parents", "-n", "1", commit])
    return out.split()[1:]


def is_ancestor(run: Run, commit: str, ref: str) -> bool:
    """True if `commit` is an ancestor of `ref` (or equal to it)."""
    try:
        run(["git", "merge-base", "--is-ancestor", commit, ref])
    except Exception:  # pylint: disable=broad-except
        return False
    return True


def upstream_tags(run: Run) -> List[str]:
    """Fully-qualified refs for every upstream release tag (`v*`), sorted.

    Upstream (ethereum/solidity) commits only ever enter the fork through
    these tags, so excluding everything reachable from them (via
    `merge_subjects(..., exclude_refs=upstream_tags(run))`) isolates the
    TRON-only merge commits. Ref names are fully qualified (`refs/tags/v...`)
    because a bare `v*` tag list is ambiguous with branches of the same name.
    """
    out = run(["git", "tag", "-l", "v*"])
    tags = [line.strip() for line in out.split("\n") if line.strip()]
    return sorted(f"refs/tags/{tag}" for tag in tags)


def merge_subjects(
    run: Run, from_ref: str, to_ref: str, exclude_refs: Optional[List[str]] = None
) -> List[str]:
    """Subjects of merge commits reachable from `to_ref` but not `from_ref`.

    `exclude_refs`, when non-empty, is appended as `--not <refs...>` so
    commits reachable from those refs (e.g. upstream release tags from
    `upstream_tags`) are excluded too. This is the topology filter that
    actually separates TRON merges from upstream ones — see
    `manifest.derive_prs`.
    """
    argv = ["git", "log", "--merges", "--format=%s", f"{from_ref}..{to_ref}"]
    if exclude_refs:
        argv = argv + ["--not"] + list(exclude_refs)
    out = run(argv)
    return [line.strip() for line in out.split("\n") if line.strip()]


def previous_tag(run: Run, tag: str) -> str:
    """The tv_* tag created immediately before `tag`.

    `cmd_prepare` calls this with the tag of the release *being prepared*,
    which deliberately does not exist yet -- GitHub only creates it when
    `publish` runs (see `cmd_publish`). So the common case is `tag` being
    absent from `tags`, and the predecessor is simply the newest tag
    published so far (`tags[0]`), since the release being prepared is newer
    than everything else. When `tag` already exists (e.g. replaying a past
    release), the predecessor is the next tag after it in creation-date
    order, as before.
    """
    out = run(["git", "tag", "--list", "tv_*", "--sort=-creatordate"])
    tags = [line for line in out.split("\n") if line.strip()]
    if not tags:
        raise ValueError("no tv_* tags exist; there is no predecessor to diff against")
    if tag not in tags:
        return tags[0]
    index = tags.index(tag)
    if index + 1 >= len(tags):
        raise ValueError(f"{tag} has no predecessor")
    return tags[index + 1]
