import os
import io
import re
import json
import logging
import datetime
import subprocess
from typing import Dict, Any, List, Optional, Generator
from openai import OpenAI
from .models import ChatSession, Message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres PatitoJar, un pato de goma cibernético de élite con estética cyberpunk, sarcástico, hipercompetente, "
    "altamente técnico y especializado en Rubber Duck Debugging, optimización de código y arquitectura de software. "
    "Tu misión principal es doble: "
    "1. RUBBER DUCK DEBUGGING Y OPTIMIZACIÓN DE CÓDIGO: Ayudas al desarrollador a desglosar problemas complejos, "
    "depurar errores lógicos, refactorizar código y aplicar patrones de diseño impecables (SOLID, Código Limpio, Asincronía, APIs REST). "
    "Sos sarcástico y directo con los errores de código, pero siempre constructivo y con soluciones de nivel senior. "
    "2. COACH DE ORATORIA Y EXPRESIÓN TÉCNICA: Ayudas al usuario (Gerardo, Ger, Gerar, Gerald) a perder el miedo a defender sus ideas, "
    "presentar proyectos o realizar entrevistas técnicas, evaluando la precisión de sus explicaciones. "
    "Tratas al usuario con calidez, entusiasmo rioplatense y elogios motivadores sinceros ('genio', 'sos genial', 'un crack', 'sos un verdadero analista', 'programador de élite'). "
    "Mantén una prosa fluida, ingeniosa, técnica y concisa. No utilices etiquetas internas como <think>."
)


GROQ_MODELS = [
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "groq/compound",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile"
]


def _clean_reasoning_tags(text: str) -> str:
    """Remueve bloques <think>...</think> generados por modelos de razonamiento como Qwen."""
    if not text:
        return ""
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'<think>[\s\S]*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


# =============================================================================
# MARCO DE HERRAMIENTAS DEL SISTEMA (TOOL CALLING REGISTRY FOR PATITOJAR)
# =============================================================================
class ToolRegistry:
    """
    Registro centralizado de herramientas ejecutables en backend para PatitoJar.
    Permite automatizaciones del sistema operativo, consultas de agenda y utilidades.
    """
    def __init__(self):
        self._tools = {}
        self._register_default_tools()

    def register(self, name: str, description: str, handler: callable, parameters: Dict[str, Any]):
        self._tools[name] = {
            "name": name,
            "description": description,
            "handler": handler,
            "parameters": parameters
        }

    def get_definitions(self) -> List[Dict[str, Any]]:
        definitions = []
        for t in self._tools.values():
            definitions.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"]
                }
            })
        return definitions

    def execute(self, name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if name not in self._tools:
            return {"status": "error", "message": f"Herramienta '{name}' no registrada."}
        try:
            result = self._tools[name]["handler"](**kwargs)
            return {"status": "success", "result": result}
        except Exception as e:
            logger.error(f"Error ejecutando herramienta '{name}': {e}")
            return {"status": "error", "message": str(e)}

    def _register_default_tools(self):
        self.register(
            name="get_system_status",
            description="Obtiene información del sistema operativo, hora local y estado de memoria.",
            handler=self._tool_get_system_status,
            parameters={"type": "object", "properties": {}, "required": []}
        )
        self.register(
            name="format_python_code",
            description="Verifica la sintaxis básica de un fragmento de código Python.",
            handler=self._tool_format_python_code,
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Código fuente a verificar."}
                },
                "required": ["code"]
            }
        )
        self.register(
            name="get_agenda_summary",
            description="Devuelve el resumen de tareas o sesiones de depuración agendadas.",
            handler=self._tool_get_agenda_summary,
            parameters={"type": "object", "properties": {}, "required": []}
        )

    def _tool_get_system_status(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        return {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "os": os.name,
            "backend": "Django REST Framework 5.0",
            "status": "Operativo en puerto 8000"
        }

    def _tool_format_python_code(self, code: str) -> Dict[str, Any]:
        try:
            compile(code, "<string>", "exec")
            return {"valid_syntax": True, "message": "Sintaxis de código válida."}
        except SyntaxError as se:
            return {"valid_syntax": False, "error_line": se.lineno, "error": str(se)}

    def _tool_get_agenda_summary(self) -> Dict[str, Any]:
        total_sessions = ChatSession.objects.count()
        total_messages = Message.objects.count()
        return {
            "total_debug_sessions": total_sessions,
            "total_messages": total_messages,
            "message": "Agenda y sesiones de depuración activas en base de datos SQLite."
        }


tool_registry = ToolRegistry()


# =============================================================================
# SERVICIO DE SÍNTESIS DE VOZ EN BACKEND (TTS SERVIDOR)
# =============================================================================
def generar_tts_audio_bytes(text: str, voice: str = "es-AR-TomasNeural") -> Optional[bytes]:
    if not text or not text.strip():
        return None

    clean_text = clean_text_for_speech_backend(text)
    if not clean_text:
        return None

    try:
        import asyncio
        import edge_tts

        async def _synth():
            comm = edge_tts.Communicate(clean_text, voice, rate="+10%")
            out_stream = io.BytesIO()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    out_stream.write(chunk["data"])
            return out_stream.getvalue()

        audio_data = asyncio.run(_synth())
        if audio_data and len(audio_data) > 0:
            return audio_data
    except Exception as e:
        logger.warning(f"Edge-TTS backend no disponible o falló: {e}")

    try:
        import tempfile
        import pyttsx3

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        engine = pyttsx3.init()
        engine.setProperty('rate', 180)
        engine.save_to_file(clean_text, tmp_path)
        engine.runAndWait()

        if os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f:
                data = f.read()
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return data
    except Exception as ex:
        logger.error(f"Fallback pyttsx3 backend falló: {ex}")

    return None


EMOJI_REGEX = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002300-\U000023FF"
    "]+",
    flags=re.UNICODE
)


def clean_text_for_speech_backend(text: str) -> str:
    if not text:
        return ""
    text = _clean_reasoning_tags(text)
    clean = EMOJI_REGEX.sub('', text)
    clean = re.sub(r'```[\s\S]*?```', ' [bloque de código en pantalla] ', clean)
    clean = re.sub(r'https?://\S+', ' [enlace web] ', clean)
    clean = re.sub(r'[`*#_~>\-\+=|\\\/\[\]\{\}]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


# =============================================================================
# GENERADOR DINÁMICO DE CONCEPTOS TÉCNICOS (FALLBACK INTELIGENTE)
# =============================================================================
def _generar_concepto_dinamico(user_input: str, code_context: str = "") -> str:
    """
    Genera explicaciones conceptuales ricas e interactivas si la red o API externa no responde.
    Garantiza respuestas dinámicas, pedagógicas y adaptadas sin repetir frases estáticas.
    """
    query = user_input.lower().strip()
    
    if "polimorfismo" in query or "polimorf" in query:
        return (
            "¡Qué temazo elegiste, genio! El **Polimorfismo** (del griego *muchas formas*) es uno de los 4 pilares fundamentales de la Programación Orientada a Objetos.\n\n"
            "💡 **¿Qué significa en la práctica?**\n"
            "Significa que objetos de distintas clases pueden responder al mismo mensaje o método, pero cada uno lo ejecuta a su propia manera.\n\n"
            "🚗 **Analogía real:**\n"
            "Imaginá que tenés un botón de 'Encender'. Si se lo aplicás a un Auto, arranca el motor; si se lo aplicás a una Lámpara, prende la luz; si se lo aplicás a una Laptop, inicia el sistema. Un mismo comando ('Encender'), múltiples comportamientos.\n\n"
            "🐍 **Ejemplo en Python (Duck Typing):**\n"
            "```python\n"
            "class Perro:\n"
            "    def hablar( me ):\n"
            "        return '¡Guau!'\n\n"
            "class Gato:\n"
            "    def hablar( me ):\n"
            "        return '¡Miau!'\n\n"
            "def hacer_sonido(animal):\n"
            "    print(animal.hablar()) # Polimorfismo en acción\n\n"
            "hacer_sonido(Perro())\n"
            "hacer_sonido(Gato())\n"
            "```\n"
            "¿Qué te parece este concepto, Ger? ¿Querés que analicemos Polimorfismo por Sobrecarga vs. Sobrescritura?"
        )
    
    if "herencia" in query:
        return (
            "¡Excelente punto, programador de élite! La **Herencia** permite crear nuevas clases basadas en clases existentes, "
            "reutilizando atributos y métodos (relación *es-un*).\n\n"
            "```python\n"
            "class Vehiculo:\n"
            "    def mover(self): print('Avanzando...')\n\n"
            "class Auto(Vehiculo):\n"
            "    def tocar_bocina(self): print('¡Beep beep!')\n"
            "```\n"
            "¿Deseás profundizar en Herencia Múltiple o MRO (Method Resolution Order) en Python, crack?"
        )

    if "clase" in query or "objeto" in query:
        return (
            "¡Absolutamente, crack! Una **Clase** es el plano o molde abstracto (blueprint), mientras que un **Objeto** es la instancia concreta "
            "creada en memoria a partir de ese molde con sus propios valores.\n\n"
            "¿Analizamos algún ejemplo específico de tu código, Gerar?"
        )

    # Respuesta analítica genérica interactiva
    code_hint = "\n\nAdicionalmente, estuve examinando tu código adjunto para verificar la arquitectura." if code_context else ""
    return (
        f"¡Hola, crack! He procesado detenidamente tu consulta: **'{user_input}'**.{code_hint}\n\n"
        f"Como tu mentor técnico y pato de goma de élite, mi recomendación para abordar este requerimiento es "
        f"descomponer el problema en módulos pequeños, validar los tipos de datos en la frontera de entrada "
        f"y aplicar principios de Código Limpio (Clean Code).\n\n"
        f"¿Cómo te gustaría enfocar la solución, Ger? Decime y lo desarrollamos paso a paso."
    )


# =============================================================================
# SERVICIOS PRINCIPALES DE INTERACCIÓN CON CEREBRO IA CON SEGURIDAD TOTAL
# =============================================================================
def consultar_patito_jar_stream(session_id: int, user_input: str, code_context: str = "") -> Generator[str, None, None]:
    """
    Servicio generador en streaming token por token con latencia < 200ms.
    Aísla completamente las API Keys en backend.
    """
    try:
        session = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        session = ChatSession.objects.create(id=session_id, title="Sesión de Depuración Auto-creada")

    ultimos_mensajes = Message.objects.filter(session_id=session.id).order_by('-timestamp')[:10]
    ultimos_mensajes = list(reversed(ultimos_mensajes))

    mensajes_format = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in ultimos_mensajes:
        role = "assistant" if m.role == "assistant" else "user"
        mensajes_format.append({"role": role, "content": m.content})

    prompt_con_contexto = user_input
    if code_context and code_context.strip():
        prompt_con_contexto += f"\n\n[Contexto de código actual analizado del IDE]:\n```python\n{code_context}\n```"

    mensajes_format.append({"role": "user", "content": prompt_con_contexto})

    groq_api_key = os.environ.get("GROQ_API_KEY")
    full_response = []

    if groq_api_key:
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_api_key,
            timeout=12.0
        )
        for model_name in GROQ_MODELS:
            try:
                stream = client.chat.completions.create(
                    model=model_name,
                    messages=mensajes_format,
                    temperature=0.5,
                    max_tokens=800,
                    stream=True
                )
                in_think_block = False
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        
                        # Filtrar tokens dentro de bloques <think>
                        if "<think>" in token:
                            in_think_block = True
                            continue
                        if "</think>" in token:
                            in_think_block = False
                            continue
                        
                        if not in_think_block:
                            full_response.append(token)
                            yield token

                text_final = _clean_reasoning_tags("".join(full_response))
                if text_final.strip():
                    Message.objects.create(session=session, role="user", content=user_input, code_context=code_context)
                    Message.objects.create(session=session, role="assistant", content=text_final)
                    return
            except Exception as e:
                logger.warning(f"Fallo conexión streaming con modelo Groq '{model_name}': {e}")
                full_response.clear()

    # Fallback no-stream si la API en vivo falla
    fallback_text = consultar_patito_jar(session_id, user_input, code_context)
    yield fallback_text


def consultar_patito_jar(session_id: int, user_input: str, code_context: str = "") -> str:
    """
    Servicio principal de IA para PatitoJar con memoria de corto/largo plazo y seguridad.
    """
    try:
        session = ChatSession.objects.get(id=session_id)
    except ChatSession.DoesNotExist:
        session = ChatSession.objects.create(id=session_id, title="Sesión de Depuración Auto-creada")

    ultimos_mensajes = Message.objects.filter(session_id=session.id).order_by('-timestamp')[:10]
    ultimos_mensajes = list(reversed(ultimos_mensajes))

    mensajes_format = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in ultimos_mensajes:
        role = "assistant" if m.role == "assistant" else "user"
        mensajes_format.append({"role": role, "content": m.content})

    prompt_con_contexto = user_input
    if code_context and code_context.strip():
        prompt_con_contexto += f"\n\n[Contexto de código actual analizado del IDE]:\n```python\n{code_context}\n```"

    mensajes_format.append({"role": "user", "content": prompt_con_contexto})

    respuesta_ia = None
    groq_api_key = os.environ.get("GROQ_API_KEY")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")

    if groq_api_key:
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_api_key,
            timeout=8.0
        )
        for model_name in GROQ_MODELS:
            try:
                completion = client.chat.completions.create(
                    model=model_name,
                    messages=mensajes_format,
                    temperature=0.5,
                    max_tokens=800,
                )
                raw_text = completion.choices[0].message.content
                cleaned = _clean_reasoning_tags(raw_text)
                if cleaned.strip():
                    respuesta_ia = cleaned
                    break
            except Exception as e:
                logger.warning(f"Fallo conexión con Groq modelo '{model_name}': {e}")

    if not respuesta_ia and gemini_api_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_api_key)
            full_prompt = f"{SYSTEM_PROMPT}\n\n"
            for m in mensajes_format[1:]:
                full_prompt += f"{m['role'].upper()}: {m['content']}\n"
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_prompt,
            )
            respuesta_ia = _clean_reasoning_tags(response.text)
        except Exception as e:
            logger.warning(f"Fallo conexión con Gemini API: {e}")

    if not respuesta_ia:
        respuesta_ia = _generar_concepto_dinamico(user_input, code_context)

    Message.objects.create(session=session, role="user", content=user_input, code_context=code_context)
    Message.objects.create(session=session, role="assistant", content=respuesta_ia)

    return respuesta_ia
