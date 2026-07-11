#!/usr/bin/env python

import email
import email.policy
import json
import os
import tempfile
import unittest
from email.message import EmailMessage

# pragma pylint: disable=import-error
from release import mailer, manifest
# pragma pylint: enable=import-error


def make_manifest(qa_approval=None, prs=None):
    digests = {name: {"sha256": "a" * 64, "keccak256": "b" * 64}
               for name, _, _ in manifest.ARTIFACT_SPECS}
    return manifest.Manifest(
        version="0.8.27", tag="tv_0.8.27", codename="Democritus_v4.8.1",
        commit="19164bedaa1a6ad09e7a9bf461d5ce73423d7611",
        tested_commit="c7c21da02b4a8e82a2abbd9228d7c5969f700621",
        qa_approval=qa_approval if qa_approval is not None else {
            "pr": 116, "reviewer": "sophia-qa", "submittedAt": "2026-04-13T11:02:00Z",
        },
        prs=prs if prs is not None else {"upstreamMerge": [114], "features": [115]},
        artifacts=manifest.build_artifacts("0.8.27+commit.19164bed"),
    ).with_digests(digests)


def make_message():
    message = EmailMessage()
    message["Subject"] = "hello"
    message["From"] = "sender@example.invalid"
    message["To"] = "recipient@example.invalid"
    message.set_content("hi")
    return message


FRONT = {"version": "0.8.27", "codename": "Democritus_v4.8.1",
         "testReport": "https://internal.tron.network/reports/0827"}
PR_URL = "https://github.com/tronprotocol/solc-bin/pull/11"

# Obviously-fake addresses only: tronprotocol/solidity is a public repository
# and the real recipient list is internal staff email addresses. See
# mailer.py's module docstring and Task 10's dispatch Override 1.
RECIPIENTS = {
    "from": "release-bot@example.invalid",
    "to": ["qa-lead@example.invalid"],
    "cc": ["release-manager@example.invalid"],
}


class TestRenderSubject(unittest.TestCase):
    def test_matches_the_established_format(self):
        man = make_manifest()
        self.assertEqual(mailer.render_subject(man), f"[发版申请] solidity_v{man.version} 版本")


class TestRenderBody(unittest.TestCase):
    """Every assertion here derives its expected value from the same manifest
    (or FRONT/PR_URL) fed into render_body -- not from a second, hand-typed
    copy of the body -- so a real regression in one field cannot be masked by
    also changing the fixture to match.
    """

    def setUp(self):
        self.man = make_manifest()
        self.body = mailer.render_body(self.man, FRONT, PR_URL)

    def test_contains_release_name(self):
        self.assertIn(f"版本名称：{self.man.release_name}", self.body)

    def test_contains_tag(self):
        self.assertIn(f"版本TAG：{self.man.tag}", self.body)

    def test_contains_full_commit_id(self):
        self.assertEqual(len(self.man.commit), 40, "fixture must carry a full sha")
        self.assertIn(f"Commit ID：{self.man.commit}", self.body)

    def test_lists_upstream_merge_pr(self):
        expected = "、".join(f"#{n}" for n in self.man.prs["upstreamMerge"])
        self.assertIn(f"以太坊代码合并的PR：{expected}", self.body)

    def test_lists_feature_pr(self):
        expected = "、".join(f"#{n}" for n in self.man.prs["features"])
        self.assertIn(f"特性开发的PR：{expected}", self.body)

    def test_links_solc_bin_pr(self):
        self.assertIn(PR_URL, self.body)

    def test_links_test_report(self):
        self.assertIn(FRONT["testReport"], self.body)

    def test_contains_qa_approval_line(self):
        qa = self.man.qa_approval
        expected = (
            f"QA 批准：{qa['reviewer']} 于 {qa['submittedAt']} 批准 PR #{qa['pr']}，"
            f"对应 commit {self.man.tested_commit}"
        )
        self.assertIn(expected, self.body)

    def test_never_mentions_another_version(self):
        """The real 0.8.27 release email said '以下为solidity_v0.8.26的测试报告' --
        the wrong version number, because a human transcribed facts that
        already existed elsewhere. render_body projects the manifest instead
        of letting anyone hand-type a version string, so this must never
        recur.
        """
        for other in ("0.8.26", "0.8.28"):
            self.assertNotIn(other, self.body)


class TestRenderBodyFailsClosed(unittest.TestCase):
    """render_body must raise a descriptive ValueError, not a bare KeyError,
    when man.qa_approval or front is missing a key it needs. This defect
    class (bare KeyError surfacing from deep inside the release pipeline
    instead of a catchable, descriptive error) has already been fixed six
    times elsewhere in this project -- this is the seventh place it could
    recur if render_body indexed these dicts directly.
    """

    def test_raises_descriptive_error_when_reviewer_missing(self):
        man = make_manifest(qa_approval={"submittedAt": "2026-04-13T11:02:00Z", "pr": 116})
        with self.assertRaisesRegex(ValueError, "reviewer"):
            mailer.render_body(man, FRONT, PR_URL)

    def test_raises_descriptive_error_when_submitted_at_missing(self):
        man = make_manifest(qa_approval={"reviewer": "sophia-qa", "pr": 116})
        with self.assertRaisesRegex(ValueError, "submittedAt"):
            mailer.render_body(man, FRONT, PR_URL)

    def test_raises_descriptive_error_when_pr_missing(self):
        man = make_manifest(
            qa_approval={"reviewer": "sophia-qa", "submittedAt": "2026-04-13T11:02:00Z"}
        )
        with self.assertRaisesRegex(ValueError, "pr"):
            mailer.render_body(man, FRONT, PR_URL)

    def test_raises_descriptive_error_when_qa_approval_is_empty(self):
        man = make_manifest(qa_approval={})
        with self.assertRaisesRegex(ValueError, "reviewer"):
            mailer.render_body(man, FRONT, PR_URL)

    def test_raises_descriptive_error_when_front_missing_test_report(self):
        front = {"version": "0.8.27", "codename": "Democritus_v4.8.1"}
        with self.assertRaisesRegex(ValueError, "testReport"):
            mailer.render_body(make_manifest(), front, PR_URL)


class TestPrRendering(unittest.TestCase):
    def render_with_prs(self, prs):
        return mailer.render_body(make_manifest(prs=prs), FRONT, PR_URL)

    def test_multiple_upstream_prs_join_with_ideographic_comma(self):
        body = self.render_with_prs({"upstreamMerge": [114, 118, 120], "features": []})
        self.assertIn("以太坊代码合并的PR：#114、#118、#120", body)

    def test_empty_upstream_prs_renders_none(self):
        body = self.render_with_prs({"upstreamMerge": [], "features": [115]})
        self.assertIn("以太坊代码合并的PR：无", body)

    def test_multiple_feature_prs_join_with_ideographic_comma(self):
        body = self.render_with_prs({"upstreamMerge": [114], "features": [115, 119]})
        self.assertIn("特性开发的PR：#115、#119", body)

    def test_empty_feature_prs_renders_none(self):
        body = self.render_with_prs({"upstreamMerge": [114], "features": []})
        self.assertIn("特性开发的PR：无", body)


class TestBuildMessageHeaders(unittest.TestCase):
    def test_sets_subject_from_to_cc(self):
        man = make_manifest()
        message = mailer.build_message(man, FRONT, PR_URL, RECIPIENTS, attachment_path=None)
        self.assertEqual(message["Subject"], mailer.render_subject(man))
        self.assertEqual(message["From"], RECIPIENTS["from"])
        self.assertEqual(message["To"], RECIPIENTS["to"][0])
        self.assertEqual(message["Cc"], RECIPIENTS["cc"][0])

    def test_joins_multiple_to_addresses_with_comma(self):
        recipients = {"from": "release-bot@example.invalid",
                      "to": ["a@example.invalid", "b@example.invalid"], "cc": []}
        message = mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                       attachment_path=None)
        self.assertEqual(message["To"], "a@example.invalid, b@example.invalid")

    def test_omits_cc_header_entirely_when_cc_is_empty(self):
        recipients = {"from": "release-bot@example.invalid",
                      "to": ["a@example.invalid"], "cc": []}
        message = mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                       attachment_path=None)
        self.assertIsNone(message["Cc"])
        self.assertNotIn("Cc", message.keys())

    def test_omits_cc_header_when_cc_key_absent(self):
        recipients = {"from": "release-bot@example.invalid", "to": ["a@example.invalid"]}
        message = mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                       attachment_path=None)
        self.assertIsNone(message["Cc"])
        self.assertNotIn("Cc", message.keys())


class TestBuildMessageFailsClosed(unittest.TestCase):
    """build_message must never produce a message aimed at nobody, and never
    one with no sender."""

    def test_rejects_empty_to_list(self):
        recipients = {"from": "release-bot@example.invalid", "to": [], "cc": []}
        with self.assertRaisesRegex(ValueError, "recipient"):
            mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                 attachment_path=None)

    def test_rejects_missing_to_key(self):
        recipients = {"from": "release-bot@example.invalid"}
        with self.assertRaisesRegex(ValueError, "recipient"):
            mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                 attachment_path=None)

    def test_rejects_missing_from_key(self):
        recipients = {"to": ["a@example.invalid"]}
        with self.assertRaisesRegex(ValueError, "from"):
            mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                 attachment_path=None)

    def test_rejects_empty_from(self):
        recipients = {"from": "", "to": ["a@example.invalid"]}
        with self.assertRaisesRegex(ValueError, "from"):
            mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                 attachment_path=None)


class TestBuildMessageRejectsHeaderInjection(unittest.TestCase):
    """Pins a property `build_message` relies on but does not itself
    implement: `EmailMessage`'s default `EmailPolicy` (active because
    `build_message` never constructs `EmailMessage` with the legacy
    `compat32` policy) refuses to fold a header value containing a bare CR or
    LF into the message at assignment time. Task 10's review flagged this
    safety as real but untested -- a `to` entry of
    `"victim@example.invalid\\r\\nBcc: attacker@evil.invalid"` is exactly the
    classic header-injection payload (smuggling an extra `Bcc:` header past a
    naive string-concatenation mailer), and this is the one thing standing
    between that payload and a real message. This test adds no validation
    to `mailer.py` -- it only proves the stdlib guarantee `build_message`
    already depends on is actually there, so a future refactor that swaps in
    a policy without this protection (or hand-builds headers instead of
    using `EmailMessage.__setitem__`) fails loudly here instead of silently
    reopening the injection.
    """

    def test_raises_on_crlf_in_a_to_address(self):
        recipients = {
            "from": "release-bot@example.invalid",
            "to": ["victim@example.invalid\r\nBcc: attacker@evil.invalid"],
        }
        with self.assertRaises(ValueError):
            mailer.build_message(make_manifest(), FRONT, PR_URL, recipients,
                                 attachment_path=None)


class TestAttachment(unittest.TestCase):
    def test_attaches_real_file_with_filename_and_nonempty_payload(self):
        with tempfile.NamedTemporaryFile(suffix=".tar.gz") as handle:
            handle.write(b"fake-tarball-bytes-for-task-10")
            handle.flush()
            message = mailer.build_message(make_manifest(), FRONT, PR_URL, RECIPIENTS,
                                           attachment_path=handle.name)
            expected_name = os.path.basename(handle.name)

        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), expected_name)
        payload = attachments[0].get_payload(decode=True)
        self.assertTrue(len(payload) > 0)
        self.assertEqual(payload, b"fake-tarball-bytes-for-task-10")

    def test_attaches_nothing_when_path_is_none(self):
        message = mailer.build_message(make_manifest(), FRONT, PR_URL, RECIPIENTS,
                                       attachment_path=None)
        self.assertEqual(list(message.iter_attachments()), [])


class TestNonAsciiRoundTrip(unittest.TestCase):
    """A naive EmailMessage/MIME usage can mangle non-ASCII text on
    serialisation (e.g. an implicit us-ascii default, or hand-rolled base64
    with no matching Content-Transfer-Encoding header). Build a real
    message, serialise it with .as_string(), and re-parse it exactly the
    way an MTA or a mail client would, to prove the Chinese subject and body
    text is not corrupted.
    """

    def test_subject_and_body_survive_serialisation_round_trip(self):
        man = make_manifest()
        message = mailer.build_message(man, FRONT, PR_URL, RECIPIENTS, attachment_path=None)

        raw = message.as_string()
        # policy=email.policy.default is what makes header/body access return
        # already-decoded text -- the legacy compat32 default would hand back
        # a raw "=?utf-8?b?...?=" Subject and require a manual decode step.
        reparsed = email.message_from_string(raw, policy=email.policy.default)

        self.assertEqual(reparsed["Subject"], mailer.render_subject(man))
        body_text = reparsed.get_content()
        self.assertIn(f"版本名称：{man.release_name}", body_text)
        self.assertIn(f"Commit ID：{man.commit}", body_text)
        self.assertIn("QA 批准", body_text)
        self.assertIn("，", body_text)  # ideographic comma in the QA approval line


class FakeSMTP:
    """Stand-in transport for smtplib.SMTP_SSL. Records calls; opens no socket."""

    def __init__(self, calls, login_error=None):
        self._calls = calls
        self._login_error = login_error
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False

    def login(self, user, password):
        self._calls.append(("login", user, password))
        if self._login_error is not None:
            raise self._login_error

    def send_message(self, message):
        self._calls.append(("send_message", message))


CREDENTIALS = {"host": "smtp.example.invalid", "port": 465, "user": "bot", "password": "hunter2"}


def _unreachable_factory(host, port):
    raise AssertionError(f"smtp_factory must not be called with invalid credentials "
                         f"(got host={host!r}, port={port!r})")


class TestSend(unittest.TestCase):
    def test_enters_and_exits_context_then_logs_in_and_sends(self):
        calls = []
        server = FakeSMTP(calls)
        hosts = []

        def factory(host, port):
            hosts.append((host, port))
            return server

        message = make_message()
        mailer.send(message, CREDENTIALS, smtp_factory=factory)

        self.assertTrue(server.entered)
        self.assertTrue(server.exited)
        self.assertEqual(hosts, [("smtp.example.invalid", 465)])
        self.assertEqual(calls, [("login", "bot", "hunter2"), ("send_message", message)])

    def test_never_sends_if_login_raises(self):
        calls = []
        server = FakeSMTP(calls, login_error=RuntimeError("bad credentials"))

        def factory(host, port):  # pylint: disable=unused-argument
            return server

        with self.assertRaisesRegex(RuntimeError, "bad credentials"):
            mailer.send(make_message(), CREDENTIALS, smtp_factory=factory)

        self.assertTrue(server.entered)
        self.assertTrue(server.exited)
        self.assertEqual(calls, [("login", "bot", "hunter2")])


class TestSendFailsClosedOnCredentials(unittest.TestCase):
    """Every required credential key is checked before smtp_factory is ever
    called -- _unreachable_factory raising AssertionError if invoked is what
    proves that, not just the ValueError itself.
    """

    def test_rejects_missing_host(self):
        creds = {"port": 465, "user": "bot", "password": "hunter2"}
        with self.assertRaisesRegex(ValueError, "host"):
            mailer.send(make_message(), creds, smtp_factory=_unreachable_factory)

    def test_rejects_missing_port(self):
        creds = {"host": "smtp.example.invalid", "user": "bot", "password": "hunter2"}
        with self.assertRaisesRegex(ValueError, "port"):
            mailer.send(make_message(), creds, smtp_factory=_unreachable_factory)

    def test_rejects_missing_user(self):
        creds = {"host": "smtp.example.invalid", "port": 465, "password": "hunter2"}
        with self.assertRaisesRegex(ValueError, "user"):
            mailer.send(make_message(), creds, smtp_factory=_unreachable_factory)

    def test_rejects_missing_password(self):
        creds = {"host": "smtp.example.invalid", "port": 465, "user": "bot"}
        with self.assertRaisesRegex(ValueError, "password"):
            mailer.send(make_message(), creds, smtp_factory=_unreachable_factory)


class TestParseRecipients(unittest.TestCase):
    """RELEASE_MAIL_RECIPIENTS is a secret cmd_apply (Task 11) reads at
    runtime; mailer.parse_recipients is the one place its JSON shape is
    validated, so Task 11 never has to.
    """

    def test_parses_well_formed_json(self):
        data = {"from": "a@example.invalid", "to": ["b@example.invalid"], "cc": []}
        self.assertEqual(mailer.parse_recipients(json.dumps(data)), data)

    def test_parses_without_cc_key(self):
        data = {"from": "a@example.invalid", "to": ["b@example.invalid"]}
        self.assertEqual(mailer.parse_recipients(json.dumps(data)), data)

    def test_rejects_invalid_json_syntax(self):
        with self.assertRaises(ValueError):
            mailer.parse_recipients("not json")

    def test_rejects_top_level_list(self):
        with self.assertRaisesRegex(ValueError, "object"):
            mailer.parse_recipients(json.dumps(["a@example.invalid"]))

    def test_rejects_missing_to_key(self):
        with self.assertRaisesRegex(ValueError, "to"):
            mailer.parse_recipients(json.dumps({"from": "a@example.invalid"}))

    def test_rejects_to_not_a_list(self):
        data = {"from": "a@example.invalid", "to": "b@example.invalid"}
        with self.assertRaisesRegex(ValueError, "to"):
            mailer.parse_recipients(json.dumps(data))

    def test_rejects_empty_to_list(self):
        data = {"from": "a@example.invalid", "to": []}
        with self.assertRaisesRegex(ValueError, "empty"):
            mailer.parse_recipients(json.dumps(data))

    def test_rejects_non_string_entry_in_to(self):
        data = {"from": "a@example.invalid", "to": [123]}
        with self.assertRaisesRegex(ValueError, "to"):
            mailer.parse_recipients(json.dumps(data))


if __name__ == "__main__":
    unittest.main()
