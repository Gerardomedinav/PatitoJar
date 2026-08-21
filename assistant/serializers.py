from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from .models import ChatSession, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['id', 'session', 'role', 'content', 'code_context', 'timestamp']
        read_only_fields = ['id', 'timestamp']


class ChatSessionSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    messages_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'created_at', 'messages_count', 'messages']
        read_only_fields = ['id', 'created_at', 'messages_count']

    @extend_schema_field(serializers.IntegerField())
    def get_messages_count(self, obj) -> int:
        return obj.messages.count()


class ChatRequestSerializer(serializers.Serializer):
    session_id = serializers.IntegerField(
        required=False, 
        allow_null=True, 
        help_text="ID de la sesión de chat existente. Si se omite, se creará una nueva sesión."
    )
    message = serializers.CharField(
        required=True, 
        help_text="Consulta o mensaje del usuario para PatitoJar."
    )
    code_context = serializers.CharField(
        required=False, 
        allow_blank=True, 
        default="", 
        help_text="Fragmento de código fuente actual capturado del IDE."
    )
    session_title = serializers.CharField(
        required=False,
        default="Sesión de Depuración",
        help_text="Título opcional si se crea una nueva sesión."
    )


class ChatResponseSerializer(serializers.Serializer):
    session_id = serializers.IntegerField(help_text="ID de la sesión procesada.")
    user_message = serializers.CharField(help_text="El mensaje enviado por el usuario.")
    assistant_message = serializers.CharField(help_text="Respuesta analítica y sarcástica de PatitoJar.")
    code_context = serializers.CharField(allow_blank=True, allow_null=True, help_text="El contexto de código analizado.")
    timestamp = serializers.DateTimeField(help_text="Marca de tiempo de la respuesta.")


class TTSRequestSerializer(serializers.Serializer):
    text = serializers.CharField(required=True, help_text="Texto a sintetizar en audio en el backend.")
    voice = serializers.CharField(required=False, default="es-AR-TomasNeural", help_text="Identificador de voz neuronal.")


class ToolExecutionSerializer(serializers.Serializer):
    name = serializers.CharField(required=True, help_text="Nombre de la herramienta del backend a ejecutar.")
    arguments = serializers.DictField(required=False, default=dict, help_text="Argumentos clave-valor para la herramienta.")


class ToolResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Estado de ejecución: 'success' o 'error'.")
    result = serializers.JSONField(required=False, help_text="Resultado estructurado de la ejecución.")
    message = serializers.CharField(required=False, help_text="Mensaje explicativo en caso de error.")
