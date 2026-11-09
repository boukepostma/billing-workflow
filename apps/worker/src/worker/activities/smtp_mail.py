import asyncio
import os
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from email.utils import make_msgid

import mistralai.workflows as workflows

from worker.workflows.config_models import BillingConfig
from worker.workflows.models import MailMessage

MAX_ATTACHMENT_BYTES = 1_500_000


def _smtp_settings(cfg: BillingConfig) -> tuple[str, int, str, str, str]:
    host = os.environ[cfg.mail.host_env]
    port = int(os.environ.get(cfg.mail.port_env, "587"))
    user = os.environ[cfg.mail.user_env]
    password = os.environ[cfg.mail.password_env]
    from_addr = os.environ[cfg.mail.from_env]
    return host, port, user, password, from_addr


def _build_message(message: MailMessage, from_addr: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(message.to)
    if message.cc:
        msg["Cc"] = ", ".join(message.cc)
    msg["Subject"] = message.subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(message.body)
    if message.attachment_pdf and message.attachment_name:
        if len(message.attachment_pdf) <= MAX_ATTACHMENT_BYTES:
            msg.add_attachment(
                message.attachment_pdf,
                maintype="application",
                subtype="pdf",
                filename=message.attachment_name,
            )
    return msg


def send_mail_message(message: MailMessage, cfg: BillingConfig) -> str:
    return _send_sync(message, cfg)


def _send_sync(message: MailMessage, cfg: BillingConfig) -> str:
    host, port, user, password, from_addr = _smtp_settings(cfg)
    msg = _build_message(message, from_addr)
    with smtplib.SMTP(host, port, timeout=60) as smtp:
        if cfg.mail.starttls:
            smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    return msg["Message-ID"]


@workflows.activity(
    name="send-mail",
    start_to_close_timeout=timedelta(minutes=2),
    retry_policy_max_attempts=3,
    retry_policy_backoff_coefficient=2.0,
)
async def send_mail(message: MailMessage, cfg: BillingConfig) -> str:
    return await asyncio.to_thread(_send_sync, message, cfg)
