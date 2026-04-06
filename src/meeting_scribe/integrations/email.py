from __future__ import annotations

"""
Email integration — sends the MOM to all attendees via SMTP.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("uvicorn.error")


def send_mom(
    *,
    to_addresses: list[str],
    meeting_title: str,
    meeting_date: str,
    mom_markdown: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_address: str,
) -> list[str]:
    """
    Send the MOM to all attendees.

    Args:
        to_addresses:  List of recipient email addresses.
        meeting_title: Used in the email subject.
        meeting_date:  Used in the email subject.
        mom_markdown:  Full MOM text (Markdown).
        smtp_*:        SMTP server credentials.
        from_address:  Sender address.

    Returns:
        List of addresses the email was successfully sent to.

    Raises:
        RuntimeError: If SMTP connection or authentication fails.
    """
    subject = f"MOM: {meeting_title} — {meeting_date}"
    html_body = _markdown_to_html(mom_markdown)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses)
    msg.attach(MIMEText(mom_markdown, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_address, to_addresses, msg.as_string())
            log.info("[email] MOM sent to %s", to_addresses)
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"SMTP error: {exc}") from exc

    return to_addresses


def _markdown_to_html(md: str) -> str:
    """Minimal Markdown → HTML conversion (no extra dependencies needed)."""
    lines = md.splitlines()
    html_lines: list[str] = [
        "<html><body style='font-family:sans-serif;max-width:720px;margin:auto;padding:24px'>"
    ]
    for line in lines:
        if line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- ") or line.startswith("* "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**") and line.endswith("**"):
            html_lines.append(f"<strong>{line[2:-2]}</strong>")
        elif line.strip() == "":
            html_lines.append("<br>")
        else:
            html_lines.append(f"<p>{line}</p>")
    html_lines.append("</body></html>")
    return "\n".join(html_lines)
