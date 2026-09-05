from __future__ import annotations

import json
import logging

from django.db.models import Count
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes, renderer_classes
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from accounts.permissions import IsStaffUser
from rag.ollama_client import OllamaError
from rag.qa import answer_question, stream_answer_events

from .models import ChatMessage, ChatSession, MessageRole
from .serializers import (
    AdminChatSessionDetailSerializer,
    AdminChatSessionSerializer,
    ChatAskSerializer,
    ChatMessageSerializer,
    ChatSessionDetailSerializer,
    ChatSessionSerializer,
)

logger = logging.getLogger("chat")

ANON_COOKIE = "bahria_chat_key"


class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        return


def _client_ip(request) -> str | None:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    ip = forwarded or (request.META.get("REMOTE_ADDR") or "").strip()
    return ip or None


def _touch_session_ip(request, session: ChatSession) -> None:
    ip = _client_ip(request)
    if ip and session.client_ip != ip:
        session.client_ip = ip
        session.save(update_fields=["client_ip", "updated_at"])


class ServerSentEventRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "txt"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data.encode(self.charset or "utf-8")
        return json.dumps(data, ensure_ascii=False).encode(self.charset or "utf-8")


def _anonymous_key(request) -> str:
    return request.COOKIES.get(ANON_COOKIE) or request.session.session_key or ""


def _get_or_create_session(request, session_id=None) -> ChatSession:
    user = request.user if request.user.is_authenticated else None
    anon_key = "" if user else _anonymous_key(request)

    if session_id:
        queryset = ChatSession.objects.filter(id=session_id)
        if user:
            queryset = queryset.filter(user=user)
        elif anon_key:
            queryset = queryset.filter(anonymous_key=anon_key, user__isnull=True)
        else:
            queryset = queryset.none()
        session = queryset.first()
        if session:
            _touch_session_ip(request, session)
            return session

    if not request.session.session_key:
        request.session.create()
    if not user:
        anon_key = request.session.session_key

    return ChatSession.objects.create(
        user=user,
        anonymous_key=anon_key or "",
        client_ip=_client_ip(request),
        title="New conversation",
    )


def _visible_sessions(request):
    user = request.user if request.user.is_authenticated else None
    if user:
        return ChatSession.objects.filter(user=user).annotate(n=Count("messages"))
    anon_key = _anonymous_key(request)
    if not anon_key:
        return ChatSession.objects.none()
    return ChatSession.objects.filter(anonymous_key=anon_key, user__isnull=True)


def _begin_turn(request, payload=None):
    serializer = ChatAskSerializer(data=payload if payload is not None else request.data)
    serializer.is_valid(raise_exception=True)
    question = serializer.validated_data["question"].strip()
    session = _get_or_create_session(request, serializer.validated_data.get("session_id"))

    user_message = ChatMessage.objects.create(
        session=session,
        role=MessageRole.USER,
        content=question,
    )
    if session.title in {"", "New conversation"}:
        session.title = question[:80]
        session.save(update_fields=["title", "updated_at"])

    history = [
        {"role": msg.role, "content": msg.content}
        for msg in session.messages.exclude(id=user_message.id).order_by("created_at")
    ]
    return session, question, history


def _attach_anon_cookie(request, response, session: ChatSession):
    if not request.user.is_authenticated and session.anonymous_key:
        response.set_cookie(
            ANON_COOKIE,
            session.anonymous_key,
            httponly=True,
            samesite="Lax",
            max_age=60 * 60 * 24 * 30,
        )
    return response


def _save_assistant(session: ChatSession, result: dict) -> ChatMessage:
    assistant = ChatMessage.objects.create(
        session=session,
        role=MessageRole.ASSISTANT,
        content=result["answer"],
        sources=result.get("sources") or [],
        found=result.get("found", False),
    )
    session.updated_at = timezone.now()
    session.save(update_fields=["updated_at"])
    return assistant


@api_view(["GET", "POST"])
@authentication_classes([CsrfExemptSessionAuthentication])
@permission_classes([AllowAny])
def chat_ask(request):
    payload = request.data
    if request.method == "GET":
        payload = {
            "question": request.query_params.get("question", ""),
            "session_id": request.query_params.get("session_id") or None,
        }
    logger.info("Chat reply started (%s)", request.method)
    session, question, history = _begin_turn(request, payload)

    try:
        result = answer_question(question, history)
    except OllamaError as exc:
        logger.exception("Chat generation failed")
        return Response(
            {
                "detail": str(exc),
                "session_id": str(session.id),
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except Exception as exc:
        logger.exception("Unexpected chat error")
        return Response(
            {"detail": f"The policy assistant could not complete this request: {exc}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    assistant = _save_assistant(session, result)
    response = Response(
        {
            "session_id": str(session.id),
            "message": ChatMessageSerializer(assistant).data,
            "answer": result["answer"],
            "sources": result["sources"],
            "found": result["found"],
        }
    )
    return _attach_anon_cookie(request, response, session)


@api_view(["GET", "POST"])
@authentication_classes([CsrfExemptSessionAuthentication])
@permission_classes([AllowAny])
@renderer_classes([ServerSentEventRenderer, JSONRenderer])
def chat_ask_stream(request):
    payload = request.data
    if request.method == "GET":
        payload = {
            "question": request.query_params.get("question", ""),
            "session_id": request.query_params.get("session_id") or None,
        }
    logger.info("Chat stream started (%s)", request.method)
    session, question, history = _begin_turn(request, payload)

    def events():
        yield _sse({"type": "meta", "session_id": str(session.id), "status": "retrieving"})
        try:
            for event in stream_answer_events(question, history):
                if event.get("type") == "done":
                    assistant = _save_assistant(session, event)
                    event = {
                        **event,
                        "session_id": str(session.id),
                        "message": ChatMessageSerializer(assistant).data,
                    }
                yield _sse(event)
            yield _sse({"type": "close"})
        except Exception as exc:
            logger.exception("Streaming chat failed")
            yield _sse(
                {
                    "type": "error",
                    "detail": "The policy assistant could not complete this request. Please try again.",
                }
            )

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return _attach_anon_cookie(request, response, session)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def session_list_create(request):
    if request.method == "POST":
        if not request.session.session_key:
            request.session.create()
        user = request.user if request.user.is_authenticated else None
        session = ChatSession.objects.create(
            user=user,
            anonymous_key="" if user else request.session.session_key,
            client_ip=_client_ip(request),
        )
        return Response(ChatSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    sessions = _visible_sessions(request)
    return Response(ChatSessionSerializer(sessions, many=True).data)


@api_view(["GET", "DELETE"])
@permission_classes([AllowAny])
def session_detail(request, pk):
    session = _visible_sessions(request).filter(id=pk).first()
    if not session:
        return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(ChatSessionDetailSerializer(session).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def chat_history(request):
    session_id = request.query_params.get("session_id")
    if session_id:
        session = _visible_sessions(request).filter(id=session_id).first()
        if not session:
            return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ChatSessionDetailSerializer(session).data)
    sessions = _visible_sessions(request)[:20]
    return Response(ChatSessionSerializer(sessions, many=True).data)


@api_view(["GET"])
@permission_classes([IsStaffUser])
def admin_session_list(request):
    sessions = (
        ChatSession.objects.select_related("user")
        .annotate(message_count=Count("messages"))
        .order_by("-updated_at")
    )
    return Response(AdminChatSessionSerializer(sessions, many=True).data)


@api_view(["GET"])
@permission_classes([IsStaffUser])
def admin_session_detail(request, pk):
    session = (
        ChatSession.objects.select_related("user")
        .annotate(message_count=Count("messages"))
        .filter(id=pk)
        .first()
    )
    if not session:
        return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(AdminChatSessionDetailSerializer(session).data)

