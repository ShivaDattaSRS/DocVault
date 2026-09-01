from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config import settings

log = logging.getLogger("docvault.mailer")

OTP_TEMPLATE = """\
Hi {name},

Your {app} verification code is:

    {code}

It expires in {minutes} minute(s). If you did not request this, ignore this email.

— {app}
"""

OTP_HTML = """\
<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6fb;padding:32px">
  <div style="max-width:460px;margin:auto;background:#fff;border-radius:14px;padding:32px;
              box-shadow:0 6px 24px rgba(18,25,45,.08)">
    <h2 style="margin:0 0 6px;color:#1c2540;font-size:20px">{app} verification</h2>
    <p style="color:#5b647d;font-size:14px;margin:0 0 22px">Hi {name}, use this code to finish signing in.</p>
    <div style="font-size:34px;letter-spacing:10px;font-weight:700;color:#2f5bea;
                background:#f0f4ff;border-radius:10px;padding:16px;text-align:center">{code}</div>
    <p style="color:#8a92a6;font-size:12px;margin:22px 0 0">
      This code expires in {minutes} minute(s). Didn't request it? You can safely ignore this email.
    </p>
  </div>
</div>
"""


def _send(msg: EmailMessage) -> None:
    if settings.smtp_tls:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)


def send_otp_email(to_email: str, name: str, code: str) -> bool:
    """Deliver an OTP over SMTP. Returns True if it was actually emailed."""
    minutes = max(1, settings.otp_ttl_seconds // 60)
    msg = EmailMessage()
    msg["Subject"] = f"{settings.app_name} verification code: {code}"
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(
        OTP_TEMPLATE.format(name=name or "there", app=settings.app_name, code=code, minutes=minutes)
    )
    msg.add_alternative(
        OTP_HTML.format(name=name or "there", app=settings.app_name, code=code, minutes=minutes),
        subtype="html",
    )

    try:
        _send(msg)
        log.info("OTP email sent to %s", to_email)
        return True
    except Exception as exc:  # noqa: BLE001 - SMTP failures must not break login
        if settings.smtp_console_fallback:
            log.warning("SMTP send failed (%s). DEV OTP for %s -> %s", exc, to_email, code)
            print(f"\n[DEV OTP] {to_email} -> {code}  (expires in {minutes} min)\n", flush=True)
            return False
        raise
