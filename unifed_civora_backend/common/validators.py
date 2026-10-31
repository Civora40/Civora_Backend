"""Reusable validators for Civora Backend."""
import re
from django.core.exceptions import ValidationError


def validate_e164_phone(value: str) -> None:
    """Validate E.164 phone number format for WhatsApp and SMS identity mapping."""
    if value and not re.match(r"^\+[1-9]\d{6,14}$", value):
        raise ValidationError(
            "Phone number must be in E.164 format (e.g. +2348012345678)."
        )

