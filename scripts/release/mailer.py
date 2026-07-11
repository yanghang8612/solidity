"""Render and deliver the release request email.

Everything here is a projection of the manifest. No field is typed twice --
the real 0.8.27 release email once said "以下为solidity_v0.8.26的测试报告"
because a human transcribed a version number that already existed elsewhere.
render_body instead fills a fixed template from `man`/`front`, so the version
string can only ever come from one place.

The email is a *request*, not an announcement: when it is sent, the GitHub
release is still a draft, no tag exists yet, and the solc-bin PR is unmerged.

Recipients are deliberately not this module's concern. `tronprotocol/solidity`
is a public repository, so no address literal appears anywhere in this file --
recipients are supplied by the caller (see `parse_recipients` for the shape
`cmd_apply`, a later stage, reads out of the `RELEASE_MAIL_RECIPIENTS` secret).
"""

import json
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from typing import Dict, Optional

from release.manifest import Manifest

_BODY = """\
版本名称：{release_name}

版本TAG：{tag}

Commit ID：{commit}

相关PR：
- 以太坊代码合并的PR：{upstream_prs}
- 特性开发的PR：{feature_prs}

Release Notes：见附件

全部二进制文件：见附件

shasum.txt/keccak256.txt：见附件

GPG签名文件：见附件

源码tar包：见附件

solc-bin仓库的发版PR：{solcbin_pr}

测试报告：{test_report}
QA 批准：{reviewer} 于 {approved_at} 批准 PR #{pr}，对应 commit {tested_commit}
"""

_CREDENTIAL_KEYS = ("host", "port", "user", "password")


def _format_prs(numbers) -> str:
    return "、".join(f"#{n}" for n in numbers) if numbers else "无"


def render_subject(man: Manifest) -> str:
    return f"[发版申请] solidity_v{man.version} 版本"


def render_body(man: Manifest, front: Dict[str, str], solcbin_pr_url: str) -> str:
    """Project `man`/`front`/`solcbin_pr_url` into the release-request body.

    Fails closed with a descriptive ValueError -- never a bare KeyError --
    if `man.qa_approval` or `front` lacks a key this template needs. Both are
    plain, unvalidated dicts (qa_approval flows straight out of gates.py;
    front out of notes.parse()), so a missing key here must not surface as an
    opaque KeyError deep inside string formatting.
    """
    qa = man.qa_approval
    missing_qa = [key for key in ("reviewer", "submittedAt", "pr") if key not in qa]
    if missing_qa:
        raise ValueError(
            f"manifest qa_approval is missing key(s) {missing_qa}; "
            "cannot render the QA approval line"
        )
    if "testReport" not in front:
        raise ValueError(
            "release notes frontmatter ('front') is missing 'testReport'; "
            "cannot render the test-report line"
        )

    return _BODY.format(
        release_name=man.release_name,
        tag=man.tag,
        commit=man.commit,
        upstream_prs=_format_prs(man.prs.get("upstreamMerge", [])),
        feature_prs=_format_prs(man.prs.get("features", [])),
        solcbin_pr=solcbin_pr_url,
        test_report=front["testReport"],
        reviewer=qa["reviewer"],
        approved_at=qa["submittedAt"],
        pr=qa["pr"],
        tested_commit=man.tested_commit,
    )


def build_message(
    man: Manifest,
    front: Dict[str, str],
    solcbin_pr_url: str,
    recipients: Dict,
    attachment_path: Optional[str],
) -> EmailMessage:
    """Build the release-request EmailMessage. Never sends anything.

    Refuses to build a message with no sender or no recipient -- `to` empty
    or entirely absent, or `from` empty or absent -- rather than silently
    producing a message addressed to nobody.
    """
    if not recipients.get("from"):
        raise ValueError("recipients has no 'from' address; cannot send without a sender")
    if not recipients.get("to"):
        raise ValueError("recipients has no 'to' recipient(s); refusing to send to nobody")

    message = EmailMessage()
    message["Subject"] = render_subject(man)
    message["From"] = recipients["from"]
    message["To"] = ", ".join(recipients["to"])
    if recipients.get("cc"):
        message["Cc"] = ", ".join(recipients["cc"])
    message.set_content(render_body(man, front, solcbin_pr_url))

    if attachment_path is not None:
        guessed, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (guessed or "application/octet-stream").split("/", 1)
        with open(attachment_path, "rb") as handle:
            message.add_attachment(
                handle.read(), maintype=maintype, subtype=subtype,
                filename=os.path.basename(attachment_path),
            )
    return message


def send(
    message: EmailMessage,
    credentials: Dict[str, str],
    smtp_factory=smtplib.SMTP_SSL,
) -> None:
    """Send `message` over SMTPS built from `credentials`.

    `smtp_factory` defaults to `smtplib.SMTP_SSL` but exists so tests can
    inject a fake transport -- this project's test suite may never open a
    socket. Every required credential key is checked before `smtp_factory` is
    called at all, so a misconfigured secret fails before anything network-
    shaped happens, and `login` raising (bad credentials) must leave
    `send_message` uncalled, which the plain call sequence below already
    guarantees: an exception from `login` jumps straight out of the `with`
    block, past `send_message`, while still running `server.__exit__`.
    """
    missing = [key for key in _CREDENTIAL_KEYS if key not in credentials]
    if missing:
        raise ValueError(f"credentials missing key(s): {missing}")

    with smtp_factory(credentials["host"], int(credentials["port"])) as server:
        server.login(credentials["user"], credentials["password"])
        server.send_message(message)


def parse_recipients(text: str) -> Dict:
    """Parse and validate the shape of the RELEASE_MAIL_RECIPIENTS secret.

    Not this module's own caller -- `cmd_apply` (a later pipeline stage) reads
    the secret and calls this to validate it before ever building a message.
    Kept here rather than there because the shape it enforces
    (`{"from": ..., "to": [...], "cc": [...]}`) is this module's contract.

    Raises `ValueError` naming exactly what is wrong: the JSON does not
    decode (already a ValueError, via `json.JSONDecodeError`), the top-level
    value is not a JSON object, `to` is absent, `to` is not a list, `to` is
    empty, or some entry of `to` is not a string.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"RELEASE_MAIL_RECIPIENTS must be a JSON object, got {type(data).__name__}"
        )
    if "to" not in data:
        raise ValueError("RELEASE_MAIL_RECIPIENTS has no 'to' key")
    if not isinstance(data["to"], list):
        raise ValueError(
            f"RELEASE_MAIL_RECIPIENTS 'to' must be a list, got {type(data['to']).__name__}"
        )
    if not data["to"]:
        raise ValueError("RELEASE_MAIL_RECIPIENTS 'to' list is empty")
    for entry in data["to"]:
        if not isinstance(entry, str):
            raise ValueError(
                "RELEASE_MAIL_RECIPIENTS 'to' contains a non-string entry: "
                f"{entry!r} ({type(entry).__name__})"
            )
    return data
