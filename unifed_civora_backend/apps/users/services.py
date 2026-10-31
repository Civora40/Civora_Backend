"""Write services for Users domain."""
from django.db import transaction
from .models import User


@transaction.atomic
def create_user_account(
    *,
    email: str,
    full_name: str,
    password: str = None,
    job_title: str = "",
    professional_registration: str = "",
    phone_number: str = None,
    is_email_verified: bool = False,
    is_platform_admin: bool = False,
) -> User:
    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name,
        job_title=job_title,
        professional_registration=professional_registration,
        phone_number=phone_number,
        is_email_verified=is_email_verified,
        is_platform_admin=is_platform_admin,
    )

