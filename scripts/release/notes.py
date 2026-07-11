"""Parse and validate release-notes/tv_X.md.

The frontmatter is a deliberately restricted flat `key: value` block. The repository
has no YAML dependency and this file does not introduce one.
"""

from typing import Dict, Tuple

_FENCE = "---"
_REQUIRED = ("version", "codename", "testReport")


def parse(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError("release notes must start with a `---` frontmatter fence")

    try:
        end = lines.index(_FENCE, 1)
    except ValueError:
        raise ValueError("unterminated frontmatter: no closing `---`") from None

    front: Dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line != line.lstrip():
            raise ValueError(f"frontmatter must be flat, got indented line: {line!r}")
        if ":" not in line:
            raise ValueError(f"frontmatter must be flat `key: value`, got: {line!r}")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not value:
            raise ValueError(f"frontmatter must be flat, key {key!r} has no value")
        if key in front:
            raise ValueError(f"duplicate frontmatter key: {key!r}")
        front[key] = value

    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return front, body


def load(path: str, version: str) -> Tuple[Dict[str, str], str]:
    """Read and validate the notes for `version`."""
    with open(path, "r", encoding="utf-8") as handle:
        front, body = parse(handle.read())

    missing = [key for key in _REQUIRED if key not in front]
    if missing:
        raise ValueError(f"release notes missing frontmatter keys: {missing}")

    if front["version"] != version:
        raise ValueError(
            f"release notes declare version {front['version']!r}, expected {version!r}"
        )

    first_line = body.split("\n", 1)[0]
    expected = (
        f"TRON Solidity compiler {version} is fully compatible with "
        f"Ethereum Solidity {version}."
    )
    if first_line.strip() != expected:
        raise ValueError(
            f"release notes first line must be {expected!r}, got {first_line!r}"
        )

    return front, body
