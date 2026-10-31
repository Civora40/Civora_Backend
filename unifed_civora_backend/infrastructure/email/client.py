"""Transactional email delivery client."""
from django.core.mail import send_mail
from django.conf import settings


def send_transactional_email(subject: str, message: str, recipient_list: list) -> int:
    return send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@civora.io"),
        recipient_list=recipient_list,
        fail_silently=False,
    )

