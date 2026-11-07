"""Authentication serializers (Phase 3 — Authentication and Authorization)."""
from rest_framework import serializers


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    require_admin = serializers.BooleanField(required=False, default=False)


class RegisterRequestSerializer(serializers.Serializer):
    firstName = serializers.CharField(max_length=120, required=False, allow_blank=True)
    lastName = serializers.CharField(max_length=120, required=False, allow_blank=True)
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    organization = serializers.CharField(max_length=255, required=False, allow_blank=True)
    jobTitle = serializers.CharField(max_length=150, required=False, allow_blank=True)
    invitationCode = serializers.CharField(max_length=64, required=False, allow_blank=True)
    role = serializers.CharField(max_length=64, required=False, allow_blank=True)
    accountType = serializers.CharField(max_length=64, required=False, allow_blank=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    token = serializers.CharField()
    newPassword = serializers.CharField(write_only=True, min_length=8, required=False)
    new_password = serializers.CharField(write_only=True, min_length=8, required=False)


class EmailVerificationRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)


class EmailVerificationConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    token = serializers.CharField()


class LogoutRequestSerializer(serializers.Serializer):
    jti = serializers.CharField(required=False, allow_blank=True)
    sessionId = serializers.UUIDField(required=False)
    revokeAll = serializers.BooleanField(required=False, default=False)
