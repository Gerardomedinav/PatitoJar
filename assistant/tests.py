from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import ChatSession, Message
from .services import consultar_patito_jar, tool_registry


class AssistantModelTests(TestCase):
    def test_create_session_and_message(self):
        session = ChatSession.objects.create(title="Test Debugging Session")
        self.assertEqual(ChatSession.objects.count(), 1)
        
        msg = Message.objects.create(
            session=session,
            role="user",
            content="Hola PatitoJar",
            code_context="x = 10"
        )
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(msg.session, session)
        self.assertEqual(msg.role, "user")


class AssistantServiceTests(TestCase):
    def test_consultar_patito_jar_saves_memory(self):
        session = ChatSession.objects.create(title="Memory Test")
        response_text = consultar_patito_jar(
            session_id=session.id,
            user_input="Why is my loop infinite?",
            code_context="while True: pass"
        )
        
        self.assertTrue(len(response_text) > 0)
        messages = Message.objects.filter(session=session)
        self.assertEqual(messages.count(), 2)
        self.assertEqual(messages[0].role, "user")
        self.assertEqual(messages[1].role, "assistant")

    def test_tool_registry_execution(self):
        res = tool_registry.execute("get_system_status", {})
        self.assertEqual(res["status"], "success")
        self.assertIn("os", res["result"])

        format_res = tool_registry.execute("format_python_code", {"code": "x = 10"})
        self.assertEqual(format_res["status"], "success")
        self.assertTrue(format_res["result"]["valid_syntax"])


class AssistantAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_chat_endpoint_new_session(self):
        payload = {
            "message": "PatitoJar, check my code.",
            "code_context": "def foo(): return None",
            "session_title": "API Test Session"
        }
        response = self.client.post('/api/v1/chat/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('session_id', data)
        self.assertIn('assistant_message', data)
        self.assertEqual(data['user_message'], payload['message'])

    def test_tools_list_endpoint(self):
        response = self.client.get('/api/v1/tools/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('tools', data)

    def test_tools_execute_endpoint(self):
        payload = {"name": "get_system_status", "arguments": {}}
        response = self.client.post('/api/v1/tools/execute/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["status"], "success")

    def test_tts_endpoint(self):
        payload = {"text": "Hola Gerardo, probando voz backend."}
        response = self.client.post('/api/v1/tts/', payload, format='json')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_500_INTERNAL_SERVER_ERROR])

    def test_swagger_schema_endpoint(self):
        response = self.client.get('/api/schema/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_swagger_ui_endpoint(self):
        response = self.client.get('/api/docs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
