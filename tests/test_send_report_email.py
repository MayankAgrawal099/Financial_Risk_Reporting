"""Tests for the email sender (no network: dry run and message building only)."""
import email
from email import policy

import pytest

from send_report_email import build_message, email_report, send, settings_from_env, write_eml


@pytest.fixture()
def pdf(tmp_path):
    p = tmp_path / "board_report_2025-12.pdf"
    p.write_bytes(b"%PDF-1.4 test")
    return p


def test_message_has_recipients_subject_and_attachment(pdf):
    m = build_message(pdf, "Board report", "Hello", "a@x.com", ["b@x.com", "c@x.com"])
    assert m["To"] == "b@x.com, c@x.com" and m["Subject"] == "Board report"
    att = [p for p in m.iter_attachments()]
    assert len(att) == 1 and att[0].get_filename() == pdf.name and att[0].get_content_type() == "application/pdf"
    assert att[0].get_payload(decode=True) == b"%PDF-1.4 test"


def test_no_recipient_and_missing_pdf_are_errors(pdf, tmp_path):
    with pytest.raises(ValueError, match="No recipients"):
        build_message(pdf, "s", "b", "a@x.com", [])
    with pytest.raises(FileNotFoundError):
        build_message(tmp_path / "nope.pdf", "s", "b", "a@x.com", ["b@x.com"])


def test_settings_from_env():
    s = settings_from_env({"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "pw", "MAIL_TO": "a@x.com, b@x.com"})
    assert s["host"] == "smtp.gmail.com" and s["port"] == 587 and s["sender"] == "me@gmail.com" and s["to"] == ["a@x.com", "b@x.com"]


def test_sending_without_credentials_is_refused(pdf):
    m = build_message(pdf, "s", "b", "a@x.com", ["b@x.com"])
    with pytest.raises(ValueError, match="SMTP_USER"):
        send(m, "localhost", 25, "", "")


def test_dry_run_writes_a_readable_eml(pdf, tmp_path):
    info = {"pdf_path": str(pdf), "subject": "Board report", "body": "Hello board"}
    msg = email_report(info, dry_run=True, out_dir=tmp_path / "outbox", env={"MAIL_TO": "b@x.com"})
    eml = tmp_path / "outbox" / "board_report_2025-12.eml"
    assert "dry run" in msg and eml.exists()
    parsed = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert parsed["Subject"] == "Board report" and parsed["To"] == "b@x.com"
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == "Hello board"
