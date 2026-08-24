from rest_framework import serializers

from .models import ChatMessage, ChatSession


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ["id", "role", "content", "sources", "found", "created_at"]
        read_only_fields = fields


class ChatSessionSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(source="messages.count", read_only=True)

    class Meta:
        model = ChatSession
        fields = ["id", "title", "created_at", "updated_at", "message_count"]
        read_only_fields = fields


class AdminChatSessionSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)
    username = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    guest = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "message_count",
            "client_ip",
            "username",
            "email",
            "guest",
        ]
        read_only_fields = fields

    def get_username(self, obj):
        return obj.user.username if obj.user else "Guest"

    def get_email(self, obj):
        return obj.user.email if obj.user else ""

    def get_guest(self, obj):
        return obj.user_id is None


class ChatSessionDetailSerializer(ChatSessionSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = ChatSessionSerializer.Meta.fields + ["messages"]


class AdminChatSessionDetailSerializer(AdminChatSessionSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(AdminChatSessionSerializer.Meta):
        fields = AdminChatSessionSerializer.Meta.fields + ["messages"]


class ChatAskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=4000)
    session_id = serializers.UUIDField(required=False, allow_null=True)
