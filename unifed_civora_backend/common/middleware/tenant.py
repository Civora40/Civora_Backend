"""
Tenant context and correlation ID middleware.
Never trusts client-supplied X-Organization-ID alone for authorization:
verifies membership against the authenticated user on the server side.
"""
import uuid


def resolve_request_organization(request):
    """
    Resolves and attaches `request.organization` and `request.organization_membership`
    from the authenticated user and optional `X-Organization-ID` header.
    Safe to call both in Django middleware and after DRF view-level authentication.
    """
    if getattr(request, "organization", None) is not None:
        return request.organization

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        requested_org_id = request.headers.get("X-Organization-ID")
        memberships = user.organization_memberships.select_related("organization", "role").filter(
            is_active=True,
            organization__is_active=True,
        )
        if requested_org_id:
            membership = memberships.filter(organization_id=requested_org_id).first()
        else:
            membership = memberships.first()

        if membership:
            request.organization = membership.organization
            request.organization_membership = membership
            # Also sync onto underlying WSGIRequest if `request` is a DRF Request wrapper
            raw_request = getattr(request, "_request", None)
            if raw_request is not None:
                raw_request.organization = membership.organization
                raw_request.organization_membership = membership

    return getattr(request, "organization", None)


class TenantContextMiddleware:
    """Attaches request_id and resolves active organization context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.organization = None
        request.organization_membership = None

        resolve_request_organization(request)

        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response
