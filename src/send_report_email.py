"""Email the board report PDF with Python (SMTP). Use it to test the email step, or as a fallback to Power Automate.

Settings come from environment variables, never from the repo:
  SMTP_HOST (default smtp.gmail.com)   SMTP_PORT (default 587)   SMTP_USER   SMTP_PASSWORD (a Gmail App Password)
  MAIL_FROM (default SMTP_USER)        MAIL_TO (comma separated)

Run:  python src/send_report_email.py --dry-run          writes reports/outbox/<name>.eml, sends nothing
      python src/send_report_email.py                    sends the report named in reports/latest_report.json
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_message(pdf: Path, subject: str, body: str, sender: str, to: list[str], cc: list[str] | None = None) -> EmailMessage:
    if not to:
        raise ValueError("No recipients. Set MAIL_TO or pass --to.")
    if not Path(pdf).exists():
        raise FileNotFoundError(f"Report not found: {pdf}")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)
    ctype, _ = mimetypes.guess_type(str(pdf))
    main, sub = (ctype or "application/octet-stream").split("/", 1)
    msg.add_attachment(Path(pdf).read_bytes(), maintype=main, subtype=sub, filename=Path(pdf).name)
    return msg


def settings_from_env(env=os.environ) -> dict:
    user = env.get("SMTP_USER", "")
    return {"host": env.get("SMTP_HOST", "smtp.gmail.com"), "port": int(env.get("SMTP_PORT", "587")), "user": user,
            "password": env.get("SMTP_PASSWORD", ""), "sender": env.get("MAIL_FROM", user),
            "to": [a.strip() for a in env.get("MAIL_TO", "").split(",") if a.strip()]}


def send(msg: EmailMessage, host: str, port: int, user: str, password: str) -> None:
    if not user or not password:
        raise ValueError("SMTP_USER and SMTP_PASSWORD are not set.")
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)


def write_eml(msg: EmailMessage, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(msg.get_payload()[-1].get_filename() or "report").stem
    path = out_dir / f"{name}.eml"
    path.write_bytes(bytes(msg))
    return path


def email_report(info: dict, dry_run: bool = False, out_dir: Path = ROOT / "reports" / "outbox", env=os.environ,
                 to: list[str] | None = None) -> str:
    """Send (or write) the email described by reports/latest_report.json. Returns a short description of what happened."""
    st = settings_from_env(env)
    recipients = to or st["to"] or (["board@example.com"] if dry_run else [])
    msg = build_message(Path(info["pdf_path"]), info["subject"], info["body"], st["sender"] or "board-report@example.com", recipients)
    if dry_run:
        return f"dry run: wrote {write_eml(msg, out_dir)}"
    send(msg, st["host"], st["port"], st["user"], st["password"])
    return f"sent to {', '.join(recipients)}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report-json", type=Path, default=ROOT / "reports" / "latest_report.json")
    ap.add_argument("--to", help="comma separated recipients (default: MAIL_TO)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    info = json.loads(a.report_json.read_text(encoding="utf-8"))
    if info.get("status") == "FAILED":
        raise SystemExit(f"The last refresh failed ({info.get('error')}). Nothing to send.")
    print(email_report(info, a.dry_run, to=[x.strip() for x in a.to.split(",")] if a.to else None))


if __name__ == "__main__":
    main()
