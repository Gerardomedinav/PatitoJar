from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from django.utils import timezone
from django.http import StreamingHttpResponse, HttpResponse

from .models import ChatSession, Message
from .serializers import (
    ChatSessionSerializer,
    MessageSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    TTSRequestSerializer,
    ToolExecutionSerializer,
    ToolResponseSerializer
)
from .services import (
    consultar_patito_jar, 
    consultar_patito_jar_stream, 
    generar_tts_audio_bytes,
    tool_registry
)


class ChatStreamAPIView(APIView):
    """
    Endpoint de transmisión HTTP en tiempo real (<200ms latencia) para respuestas conversacionales token por token.
    """
    @extend_schema(
        request=ChatRequestSerializer,
        summary="Transmisión HTTP en streaming (token por token)",
        description="Recibe la consulta del usuario y emite tokens en tiempo real con latencia inferior a 200ms."
    )
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        session_id = data.get('session_id')
        user_message = data.get('message')
        code_context = data.get('code_context', '')
        session_title = data.get('session_title', 'Sesión de Depuración')

        if not session_id:
            session = ChatSession.objects.create(title=session_title)
            session_id = session.id
        else:
            session, _ = ChatSession.objects.get_or_create(id=session_id, defaults={'title': session_title})

        stream_gen = consultar_patito_jar_stream(session_id, user_message, code_context)
        response = StreamingHttpResponse(stream_gen, content_type='text/plain; charset=utf-8')
        response['X-Session-ID'] = str(session_id)
        return response


class TTSAPIView(APIView):
    """
    Endpoint backend para síntesis de voz (TTS). Permite al cliente solicitar audio binario centralizado.
    """
    @extend_schema(
        request=TTSRequestSerializer,
        responses={
            200: OpenApiResponse(description="Archivo binario de audio sintetizado (MP3/WAV)"),
            400: OpenApiResponse(description="Error en el texto proporcionado")
        },
        summary="Sintetizar audio de respuesta (TTS Servidor)",
        description="Sintetiza un texto en el backend usando Edge-TTS o pyttsx3 y retorna el flujo binario de audio."
    )
    def post(self, request):
        serializer = TTSRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        text = serializer.validated_data.get('text')
        voice = serializer.validated_data.get('voice', 'es-AR-TomasNeural')

        audio_bytes = generar_tts_audio_bytes(text, voice)
        if not audio_bytes:
            return Response({"error": "No se pudo sintetizar el audio."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = HttpResponse(audio_bytes, content_type='audio/mpeg')
        response['Content-Disposition'] = 'inline; filename="speech.mp3"'
        return response


class ToolsAPIView(APIView):
    """
    Endpoint para listar todas las herramientas registradas ejecutables en backend por PatitoJar.
    """
    @extend_schema(
        summary="Listar herramientas registradas en backend",
        description="Retorna las definiciones JSON Schema de todas las herramientas registradas en el backend para Tool Calling."
    )
    def get(self, request):
        definitions = tool_registry.get_definitions()
        return Response({"tools": definitions}, status=status.HTTP_200_OK)


class ToolExecutionAPIView(APIView):
    """
    Endpoint seguro para ejecutar una herramienta del sistema operativo o backend mediante PatitoJar.
    """
    @extend_schema(
        request=ToolExecutionSerializer,
        responses={
            200: ToolResponseSerializer,
            400: OpenApiResponse(description="Parámetros inválidos")
        },
        summary="Ejecutar herramienta en el backend",
        description="Ejecuta de forma segura una función registrada en el registro de herramientas del backend."
    )
    def post(self, request):
        serializer = ToolExecutionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name = serializer.validated_data.get('name')
        args = serializer.validated_data.get('arguments', {})

        result = tool_registry.execute(name, args)
        response_serializer = ToolResponseSerializer(data=result)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.data, status=status.HTTP_200_OK)


class ChatSessionViewSet(viewsets.ModelViewSet):
    """
    ViewSet para listar, crear, consultar y eliminar sesiones de chat (memoria a largo plazo).
    """
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet de solo lectura para revisar el historial completo de mensajes.
    """
    queryset = Message.objects.all()
    serializer_class = MessageSerializer
    filterset_fields = ['session', 'role']


class ChatAPIView(APIView):
    """
    Endpoint principal de interacción con PatitoJar.
    Recibe la consulta del usuario, el código del IDE, interactúa con la IA y guarda el contexto.
    """
    @extend_schema(
        request=ChatRequestSerializer,
        responses={
            200: ChatResponseSerializer,
            400: OpenApiResponse(description="Parámetros de entrada inválidos")
        },
        summary="Enviar consulta a PatitoJar con contexto de código",
        description="Recibe el mensaje del usuario, contexto opcional de código del IDE, procesa la respuesta a través del cerebro de IA con memoria de corto y largo plazo, y devuelve la respuesta analítica de PatitoJar."
    )
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        session_id = data.get('session_id')
        user_message = data.get('message')
        code_context = data.get('code_context', '')
        session_title = data.get('session_title', 'Sesión de Depuración')

        if not session_id:
            session = ChatSession.objects.create(title=session_title)
            session_id = session.id
        else:
            session, _ = ChatSession.objects.get_or_create(id=session_id, defaults={'title': session_title})

        respuesta_patito_jar = consultar_patito_jar(
            session_id=session_id,
            user_input=user_message,
            code_context=code_context
        )

        response_data = {
            'session_id': session_id,
            'user_message': user_message,
            'assistant_message': respuesta_patito_jar,
            'code_context': code_context,
            'timestamp': timezone.now()
        }

        response_serializer = ChatResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.data, status=status.HTTP_200_OK)
