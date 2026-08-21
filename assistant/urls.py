from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ChatSessionViewSet, 
    MessageViewSet, 
    ChatAPIView, 
    ChatStreamAPIView,
    TTSAPIView,
    ToolsAPIView,
    ToolExecutionAPIView
)

router = DefaultRouter()
router.register(r'sessions', ChatSessionViewSet, basename='chatsession')
router.register(r'messages', MessageViewSet, basename='message')

urlpatterns = [
    path('chat/', ChatAPIView.as_view(), name='chat-api'),
    path('chat/stream/', ChatStreamAPIView.as_view(), name='chat-stream-api'),
    path('tts/', TTSAPIView.as_view(), name='tts-api'),
    path('tools/', ToolsAPIView.as_view(), name='tools-list-api'),
    path('tools/execute/', ToolExecutionAPIView.as_view(), name='tools-exec-api'),
    path('', include(router.urls)),
]
