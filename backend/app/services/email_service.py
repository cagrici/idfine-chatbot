"""Standalone SMTP email service — independent of Odoo."""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """Send emails via SMTP."""

    def __init__(self):
        s = get_settings()
        self.host = s.smtp_host
        self.port = s.smtp_port
        self.user = s.smtp_user
        self.password = s.smtp_password
        self.from_addr = s.smtp_from or s.smtp_user
        self.use_tls = s.smtp_use_tls

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, to: str, subject: str, body_html: str) -> bool:
        if not self.is_configured:
            logger.warning("SMTP not configured, skipping email to %s", to)
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            ctx = ssl.create_default_context()
            if self.use_tls and self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=15) as srv:
                    srv.login(self.user, self.password)
                    srv.sendmail(self.from_addr, [to], msg.as_string())
            else:
                with smtplib.SMTP(self.host, self.port, timeout=15) as srv:
                    if self.use_tls:
                        srv.starttls(context=ctx)
                    srv.login(self.user, self.password)
                    srv.sendmail(self.from_addr, [to], msg.as_string())

            logger.info("Email sent to %s: %s", to, subject)
            return True
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to, e)
            return False
