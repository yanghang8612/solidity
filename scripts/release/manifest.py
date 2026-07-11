"""The single source of truth for a release. Everything else is a projection of it."""

import json
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

# (artifact name, solc-bin directory, solc-bin filename template)
# Order is load-bearing: it is the line order of shasum.txt and keccak256.txt.
ARTIFACT_SPECS = (
    ("solc-macos", "macosx-amd64", "solc-macosx-amd64-v{long}"),
    ("solc-static-linux", "linux-amd64", "solc-linux-amd64-v{long}"),
    ("solc-windows.exe", "windows-amd64", "solc-windows-amd64-v{long}.exe"),
    ("soljson.js", "wasm", "soljson-v{long}.js"),
)

_MERGE_PR = re.compile(r"^Merge pull request #(\d+) from ([^/\s]+)/(\S+)")
_UPSTREAM_OWNER = "ethereum"
_UPSTREAM_BRANCH_MARKER = "merge_from_v"

# `sha256`/`keccak256` are legitimately allowed to be `None` (a manifest that
# never went through `with_digests`), so they are listed here too -- only
# their *presence as keys* is required, not a non-null value.
_ARTIFACT_REQUIRED_KEYS = ("name", "solcBinDir", "solcBinPath", "sha256", "keccak256")


@dataclass(frozen=True)
class Artifact:
    name: str
    solc_bin_dir: str
    solc_bin_path: str
    sha256: Optional[str] = None
    keccak256: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "solcBinDir": self.solc_bin_dir,
            "solcBinPath": self.solc_bin_path,
            "sha256": self.sha256,
            "keccak256": self.keccak256,
        }

    @staticmethod
    def from_dict(data: dict) -> "Artifact":
        """Fails closed on a truncated/hand-edited artifact dict.

        A bare `data["solcBinDir"]` subscript would raise an undescriptive
        `KeyError` deep inside `Manifest.from_json` -- the same fail-closed
        defect class already fixed in `load_config`, `g2_qa_approval`,
        `github.release_view`, `solcbin.append_entry`, and
        `Manifest.with_digests`. Collect and name every missing key at once,
        matching `load_config`'s style, instead of failing on the first.
        """
        missing = [key for key in _ARTIFACT_REQUIRED_KEYS if key not in data]
        if missing:
            raise ValueError(f"manifest artifact is missing required key(s): {missing}")
        return Artifact(
            name=data["name"],
            solc_bin_dir=data["solcBinDir"],
            solc_bin_path=data["solcBinPath"],
            sha256=data.get("sha256"),
            keccak256=data.get("keccak256"),
        )


def build_artifacts(long_version: str) -> List[Artifact]:
    return [
        Artifact(
            name=name,
            solc_bin_dir=directory,
            solc_bin_path=f"{directory}/{template.format(long=long_version)}",
        )
        for name, directory, template in ARTIFACT_SPECS
    ]


def derive_prs(subjects: List[str], release_pr: int) -> Dict[str, List[int]]:
    """Classify TRON pull requests merged into this release.

    This function does NOT filter out upstream PRs by itself. The `owner ==
    "ethereum"` check below is defense-in-depth only: it catches merges made
    directly from the ethereum/solidity org, but upstream solidity PRs are
    routinely merged from contributor forks (e.g. `ipsilon/eof-prep-0`,
    `zo9999/fix-signature-of-function-operator`, `pgebal/smtchecker/...`),
    so owner-name matching alone cannot and does not exclude them.

    The actual exclusion of upstream PRs is topological and happens in the
    caller, before `subjects` ever reaches this function: pass
    `merge_subjects(run, from_ref, to_ref, exclude_refs=upstream_tags(run))`.
    Upstream commits enter this fork only via upstream release tags (`v*`),
    so excluding everything reachable from those tags leaves exactly the
    TRON merge commits. By the time `subjects` gets here, upstream PRs are
    already gone; the release PR itself is excluded here by number, and
    branches containing `merge_from_v` are classified as upstream-code
    merges while the rest are features.
    """
    upstream: List[int] = []
    features: List[int] = []
    for subject in subjects:
        match = _MERGE_PR.match(subject)
        if match is None:
            continue
        number, owner, branch = int(match.group(1)), match.group(2), match.group(3)
        if owner == _UPSTREAM_OWNER or number == release_pr:
            continue
        (upstream if _UPSTREAM_BRANCH_MARKER in branch else features).append(number)
    return {"upstreamMerge": sorted(upstream), "features": sorted(features)}


# Top-level keys `from_json` requires present in a parsed manifest.json.
# `artifacts` is included here; each element is separately validated by
# `Artifact.from_dict`.
_MANIFEST_REQUIRED_KEYS = (
    "version", "tag", "codename", "commit", "testedCommit", "qaApproval", "prs", "artifacts",
)


@dataclass(frozen=True)
class Manifest:
    version: str
    tag: str
    codename: str
    commit: str
    tested_commit: str
    qa_approval: dict
    prs: Dict[str, List[int]]
    artifacts: List[Artifact] = field(default_factory=list)

    @property
    def short_commit(self) -> str:
        return self.commit[:8]

    @property
    def long_version(self) -> str:
        return f"{self.version}+commit.{self.short_commit}"

    @property
    def release_name(self) -> str:
        return f"{self.version}_{self.codename}"

    def to_json(self) -> str:
        payload = {
            "version": self.version,
            "tag": self.tag,
            "codename": self.codename,
            "releaseName": self.release_name,
            "commit": self.commit,
            "shortCommit": self.short_commit,
            "longVersion": self.long_version,
            "testedCommit": self.tested_commit,
            "qaApproval": self.qa_approval,
            "prs": self.prs,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    @staticmethod
    def from_json(text: str) -> "Manifest":
        """Fails closed on a truncated/hand-edited/corrupt manifest.json.

        A bare `data["testedCommit"]` subscript would raise an
        undescriptive `KeyError` inside `cmd_apply`/`cmd_publish` instead of
        a clean, descriptive error -- the same fail-closed defect class
        already fixed in `load_config`, `g2_qa_approval`,
        `github.release_view`, `solcbin.append_entry`, and
        `Manifest.with_digests`. Collect and name every missing top-level
        key at once, matching `load_config`'s style, instead of failing on
        the first.
        """
        data = json.loads(text)
        missing = [key for key in _MANIFEST_REQUIRED_KEYS if key not in data]
        if missing:
            raise ValueError(f"manifest is missing required key(s): {missing}")
        return Manifest(
            version=data["version"],
            tag=data["tag"],
            codename=data["codename"],
            commit=data["commit"],
            tested_commit=data["testedCommit"],
            qa_approval=data["qaApproval"],
            prs=data["prs"],
            artifacts=[Artifact.from_dict(a) for a in data["artifacts"]],
        )

    def with_digests(self, digests: Dict[str, Dict[str, str]]) -> "Manifest":
        """Return a copy whose artifacts carry sha256/keccak256 from `digests`."""
        updated = []
        for a in self.artifacts:
            if a.name not in digests:
                raise ValueError(f"digests has no entry for artifact {a.name}")
            entry = digests[a.name]
            for algorithm in ("sha256", "keccak256"):
                if algorithm not in entry:
                    raise ValueError(f"digests entry for {a.name} has no {algorithm}")
            updated.append(
                Artifact(
                    name=a.name,
                    solc_bin_dir=a.solc_bin_dir,
                    solc_bin_path=a.solc_bin_path,
                    sha256=entry["sha256"],
                    keccak256=entry["keccak256"],
                )
            )
        return replace(self, artifacts=updated)
