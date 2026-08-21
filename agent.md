# 🤖 AGENTS.md / AGENT.MD - Contexto del Sistema PATITOJAR

Este documento proporciona una guía exhaustiva sobre el sistema **PATITOJAR**, detallando qué es, para qué sirve, las tecnologías que utiliza, la arquitectura de 3 capas, el flujo de ejecución, el sistema de Tool Calling y todas sus funcionalidades.

---

## 📌 1. ¿Qué es y Para qué sirve?

### ¿Qué es?
**PATITOJAR v1.0** es un **Asistente Virtual de Depuración de Código (Rubber Duck Debugger)** interactivo para escritorio. Implementa una arquitectura segura de 3 capas que combina una interfaz gráfica flotante Cyberpunk HUD en **PyQt6**, un backend seguro en **Django REST Framework** y acceso a Modelos de Lenguaje de Última Generación (Groq Llama 3.3 70B y Google Gemini 2.5 Flash).

### ¿Para qué sirve?
El sistema aplica y potencia el concepto clásico de programación de **Rubber Duck Debugging** ("Depuración con Patito de Goma"):
- **Asistencia en tiempo real:** Permite a los desarrolladores explicar sus problemas lógicos o pegar fragmentos de código para obtener soluciones directas.
- **Personalidad Integrada:** PatitoJar opera con una personalidad amigable, entusiasta, hipercompetente y elegante. Saluda al usuario por su nombre (Gerardo, Ger, Gerar, Gerald) y le dedica elogios motivadores ("genio", "sos genial", "un crack", "sos un verdadero analista", "programador de élite").
- **Interacción Multimodal:** Soporta entrada por teclado, voz por micrófono (STT) y respuesta por voz (TTS).
- **Memoria Contextual:** Conserva la memoria histórica a corto y largo plazo de las conversaciones para dar seguimiento continuo a sesiones de refactorización o depuración compleja.
- **Tool Calling & Automatización:** Permite ejecutar herramientas de inspección del sistema operativo, formateo de código Python y consulta de agenda mediante backend.

---

## 🏗️ 2. Arquitectura de 3 Capas y Seguridad

```
┌─────────────────────────────────────────────────────────────┐
│ 1. CAPA CLIENTE / INTERFAZ (PyQt6 Cyberpunk HUD)            │
│  - Captura Micrófono (STT Local Faster-Whisper / VAD)       │
│  - Renderizado Animado HUD 60 FPS (Estados, Anillos, Láser) │
│  - Reproducción de Voz (TTS Local / Remote Fetch Backend)   │
│  - CERO API Keys en el Cliente                              │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP REST / Streaming (CORS)
┌──────────────────────────────▼──────────────────────────────┐
│ 2. CAPA BACKEND SEGURO (Django REST Framework)              │
│  - Aislamiento de Credenciales (API Keys en .env)          │
│  - Endpoint /api/v1/chat/stream/ (Streaming <200ms)        │
│  - Endpoint /api/v1/tts/ (Síntesis TTS Backend)             │
│  - Motor de Herramientas (Tool Registry OS / Agenda)       │
│  - Persistencia de Memoria Corto/Largo Plazo (SQLite)       │
└──────────────────────────────┬──────────────────────────────┘
                               │ API Requests Aisladas
┌──────────────────────────────▼──────────────────────────────┐
│ 3. CAPA CEREBRO / SERVICIOS IA EXTERNOS                     │
│  - Groq API (Llama 3.3 70B Versatile)                       │
│  - Google Gemini API (2.5 Flash Fallback)                   │
│  - Edge-TTS / PyTTSx3 (Síntesis de Voz Neuronal)             │
└─────────────────────────────────────────────────────────────┘
```

### Reglas de Seguridad
1. **Cero exposición de credenciales:** Ninguna API Key se expose en los scripts cliente (`patito_jar_overlay.py`). El backend Django gestiona todas las llamadas externas de forma aislada.
2. **Resiliencia & Fallback:** Si un proveedor externo falla, el sistema conmuta automáticamente entre Groq, Gemini o el Modo Offline simulado sin colapsar la interfaz.

---

## 🛠️ 3. Tecnologías Utilizadas

| Componente | Tecnología | Descripción |
| :--- | :--- | :--- |
| **Lenguaje Base** | Python 3.10+ / 3.13 | Entorno principal de ejecución |
| **Backend Web** | Django 5.0 | Framework web robusto para el motor API |
| **API REST** | Django REST Framework (DRF) | Creación de Endpoints RESTful para la comunicación UI-Backend |
| **Documentación API** | `drf-spectacular` | Generación automática de especificación OpenAPI 3.0, Swagger UI y ReDoc |
| **Cross-Origin** | `django-cors-headers` | Habilita CORS para conexiones de overlays o extensiones de IDE |
| **Base de Datos** | SQLite 3 (`db.sqlite3`) | Persistencia de sesiones (`ChatSession`) y mensajes (`Message`) |
| **Modelos de IA** | Groq API (`llama-3.3-70b-versatile`) | Motor principal de Inteligencia Artificial de alta velocidad |
| **IA Fallback** | Google GenAI (`gemini-2.5-flash`) | Proveedor secundario de respaldo para inferencia |
| **Tool Calling System**| `ToolRegistry` (Django) | Registro ejecutable de automatizaciones OS, agenda y análisis de código |
| **Interfaz HUD (GUI)** | PyQt6 | Ventana flotante translúcida, frameless y Always-On-Top |
| **Gráficos & Animación**| `QSvgRenderer`, `QPainter`, `QTimer` | Renderizado gráfico a 60 FPS con anillos HUD concéntricos y láser |
| **Reconocimiento Voz (STT)**| `SpeechRecognition` & `Faster-Whisper` | Captura y transcripción local de audio desde micrófono |
| **Síntesis de Voz (TTS)** | `edge-tts` & `pyttsx3` (Cliente & Server) | Voz Neuronal ultra realista (`es-AR-TomasNeural`) con soporte remoto en backend |

---

## 🚀 4. ¿Cómo Corre el Sistema?

### Opción A: Inicio Automático (Recomendado)
```bat
INICIAR_PATITO.bat
```
Este script realiza automáticamente:
1. Inicia el servidor backend Django en segundo plano en `http://127.0.0.1:8000`.
2. Espera 3 segundos a que el servidor esté activo.
3. Lanza la interfaz gráfica flotante PyQt6 `patito_jar_overlay.py`.

---

## 💡 5. Resumen de Endpoints API REST

- `POST /api/v1/chat/` -> Envía mensaje y contexto de código; devuelve respuesta de PatitoJar.
- `POST /api/v1/chat/stream/` -> Respuestas conversacionales en streaming HTTP (<200ms).
- `POST /api/v1/tts/` -> Genera y descarga el flujo binario de audio sintetizado en servidor.
- `GET /api/v1/tools/` -> Lista las herramientas registradas para Tool Calling.
- `POST /api/v1/tools/execute/` -> Ejecuta de forma segura una herramienta del backend.
- `GET /api/v1/sessions/` -> Lista todas las sesiones de depuración guardadas.
- `GET /api/v1/messages/` -> Lista el historial completo de mensajes.
- `GET /api/docs/` -> Interfaz de Swagger UI para probar los endpoints interactivos.
