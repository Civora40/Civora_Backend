"""
Standardized API response envelope helpers for Civora Backend.
"""
from typing import Any, Dict, Optional
from rest_framework import status
from rest_framework.response import Response


def api_success(
    data: Any = None,
    message: str = "Success",
    status_code: int = status.HTTP_200_OK,
    meta: Optional[Dict[str, Any]] = None,
) -> Response:
    payload: Dict[str, Any] = {
        "success": True,
        "message": message,
        "data": data,
    }
    if meta is not None:
        payload["meta"] = meta
    return Response(payload, status=status_code)


def api_error(
    message: str,
    code: str = "error",
    details: Any = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    payload: Dict[str, Any] = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }
    return Response(payload, status=status_code)

