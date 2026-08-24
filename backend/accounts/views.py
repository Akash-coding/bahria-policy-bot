from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_staff", "is_superuser"]
        read_only_fields = fields


@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf_token(request):
    token = get_token(request)
    return Response({"detail": "CSRF cookie set", "csrfToken": token, "csrfToken": token})


@api_view(["POST"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    username = serializer.validated_data["username"].strip()
    password = serializer.validated_data["password"]
    user = authenticate(request, username=username, password=password)
    if user is None and "@" in username:
        match = User.objects.filter(email__iexact=username).first()
        if match:
            user = authenticate(request, username=match.username, password=password)
    if user is None:
        return Response(
            {"detail": "Invalid username or password."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_active:
        return Response(
            {"detail": "This account is disabled."},
            status=status.HTTP_403_FORBIDDEN,
        )
    login(request, user)
    return Response(UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request):
    logout(request)
    return Response({"detail": "Logged out."})


@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def me_view(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False, "user": None})
    return Response(
        {
            "authenticated": True,
            "user": UserSerializer(request.user).data,
        }
    )
