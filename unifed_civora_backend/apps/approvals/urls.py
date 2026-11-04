from django.urls import path
from .views import ApprovalListCreateView

urlpatterns = [
    path("", ApprovalListCreateView.as_view(), name="approval-list"),
]
