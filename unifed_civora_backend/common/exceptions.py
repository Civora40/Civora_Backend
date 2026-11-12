"""
Centralized domain exceptions and custom DRF exception handler.
Prevents leaking internal stack traces while providing consistent error envelopes.
"""
import logging
from typing import Optional
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class CivoraDomainError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A business rule validation error occurred."
    default_code = "domain_error"


class TenantIsolationError(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Access denied: cross-organization or unauthorized project boundary."
    default_code = "tenant_isolation_violation"


class ProjectApprovalRequiredError(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Project must be reviewed and approved by a Platform Administrator before field submissions can begin."
    default_code = "project_approval_required"


def civora_exception_handler(exc, context) -> Optional[Response]:
    """Custom DRF exception handler producing standardized error envelopes."""
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        return Response(
            {
                "success": False,
                "error": {
                    "code": "validation_error",
                    "message": "Data validation failed.",
                    "details": detail,
                },
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            {
                "success": False,
                "error": {
                    "code": "permission_denied",
                    "message": str(exc) or "You do not have permission to perform this action.",
                    "details": None,
                },
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    response = exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", "api_error")
        msg = "Request could not be processed."
        if isinstance(response.data, dict) and isinstance(response.data.get("detail"), str):
            msg = response.data["detail"]
        elif isinstance(response.data, str):
            msg = response.data
        return Response(
            {
                "success": False,
                "error": {
                    "code": code,
                    "message": msg,
                    "details": response.data,
                },
            },
            status=response.status_code,
        )

    logger.exception("Unhandled server exception in Civora API: %s", exc)
    return Response(
        {
            "success": False,
            "error": {
                "code": "internal_server_error",
                "message": "An unexpected server error occurred.",
                "details": None,
            },
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
