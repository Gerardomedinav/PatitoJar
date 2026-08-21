"""
===============================================================================
MÓDULO DE VOZ EN TIEMPO REAL - PATITOJAR (Arquitectura Pipecat v3.0)
===============================================================================
Arquitectura de Tubería Continua (Continuous Pipeline) basada en el patrón Pipecat:
1. AudioInputTransport: Captura continua de micrófono (sounddevice / PCM int16).
2. VADProcessor: Detección de Actividad de Voz con Ring Buffer pre-speech y muting digital (Barge-in / Exclusión mutua).
3. STTProcessor: Transcripción rápida asíncrona (Faster-Whisper int8 / Google SR) + Corrector fonético técnico.
4. LLMContext & LLMContextAggregator: Agregador de contexto conversacional de sesión y código IDE.
5. BackendStreamingClient: Transmisión HTTP token por token con Django REST Framework (/api/v1/chat/stream/).
6. TTSProcessor & AudioOutputTransport: Parseo de oraciones en tiempo real y reproducción fluida (Edge-TTS / pyttsx3 / DRF TTS).
7. PyQt6 Integration: PipecatVoiceWorker con señales desacopladas (LISTEN, THINK, SPEAK, IDLE) para 60 FPS sin freezes.
===============================================================================
"""

import os
import re
import io
import time
import uuid
import wave
import queue
import logging
import threading
import collections
import json
from typing import Callable, Optional, Dict, Any, List, Generator, Tuple

# Configuración de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] (PipecatVoice) %(message)s")
logger = logging.getLogger("PipecatVoice")

# -----------------------------------------------------------------------------
# VERIFICACIÓN DE LIBRERÍAS DE AUDIO Y TRANSCRIPCIÓN
# -----------------------------------------------------------------------------
SD_AVAILABLE = False
try:
    import sounddevice as sd
    import numpy as np
    SD_AVAILABLE = True
except Exception as e:
    sd = None
    np = None
    logger.warning(f"sounddevice o numpy no disponibles: {e}")

WHISPER_AVAILABLE = False
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except Exception as e:
    WhisperModel = None
    logger.warning(f"faster-whisper no disponible: {e}")

PYTTSX3_AVAILABLE = False
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except Exception as e:
    pyttsx3 = None
    logger.warning(f"pyttsx3 no disponible: {e}")

_GLOBAL_PYTTSX3_ENGINE = None

def get_pyttsx3_engine():
    global _GLOBAL_PYTTSX3_ENGINE
    if _GLOBAL_PYTTSX3_ENGINE is None and PYTTSX3_AVAILABLE and pyttsx3:
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            engine = pyttsx3.init()
            engine.setProperty('rate', 185)
            engine.setProperty('volume', 1.0)
            voices = engine.getProperty('voices')
            for v in voices:
                v_name = v.name.lower()
                if any(m in v_name for m in ['pablo', 'mateo', 'tomas', 'raul', 'julio', 'male', 'hombre', 'es', 'spanish']):
                    engine.setProperty('voice', v.id)
                    break
            _GLOBAL_PYTTSX3_ENGINE = engine
        except Exception as e:
            logger.debug(f"Fallo inicialización singleton pyttsx3: {e}")
    return _GLOBAL_PYTTSX3_ENGINE

SR_AVAILABLE = False
try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except Exception:
    sr = None

PYQT_AVAILABLE = False
try:
    from PyQt6.QtCore import QThread, pyqtSignal
    PYQT_AVAILABLE = True
except Exception:
    QThread = object
    pyqtSignal = None

# -----------------------------------------------------------------------------
# PARÁMETROS GLOBALES DE AUDIO Y CONFIGURACIÓN VAD PIPECAT
# -----------------------------------------------------------------------------
SAMPLE_RATE = 16000          # Tasa de muestreo estándar para Whisper (16 kHz)
CHANNELS = 1                 # Audio monofónico
BLOCK_SIZE = 512             # Chunks ultra-rápidos de ~32ms para respuesta inmediata
DTYPE = 'int16'              # Formato entero de 16-bits para audio raw

VAD_MIN_ENERGY_THRESHOLD = 150   # Amplitud RMS mínima para considerar voz humana (Modo IDLE)
VAD_SPEAKING_ENERGY_THRESHOLD = 220 # Umbral de locución calibrado para capturar 'basta' y 'alto' al instante
VAD_SILENCE_TIMEOUT_SEC = 0.6    # Segundos de silencio (600ms) para captura completa de oraciones sin fragmentar
VAD_RING_BUFFER_SEC = 0.2        # Búfer previo para latencia cero

GLOBAL_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()

def get_whisper_model() -> Optional[object]:
    """Retorna la instancia singleton en caché del modelo Whisper 'tiny' (int8)."""
    global GLOBAL_WHISPER_MODEL
    if WHISPER_AVAILABLE and WhisperModel and GLOBAL_WHISPER_MODEL is None:
        with _WHISPER_LOCK:
            if GLOBAL_WHISPER_MODEL is None:
                try:
                    logger.info("Cargando modelo Faster-Whisper 'tiny' (compute_type=int8)...")
                    GLOBAL_WHISPER_MODEL = WhisperModel(
                        "tiny",
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=4
                    )
                    logger.info("Modelo Faster-Whisper cargado correctamente.")
                except Exception as e:
                    logger.error(f"Error al inicializar Whisper Model: {e}")
                    GLOBAL_WHISPER_MODEL = None
    return GLOBAL_WHISPER_MODEL

EMOJI_REGEX = re.compile(
    r"["
    r"\U0001F600-\U0001F64F"
    r"\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0"
    r"\U000024C2-\U0001F251"
    r"\U0001F900-\U0001F9FF"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF"
    r"\U00002300-\U000023FF"
    r"]+",
    flags=re.UNICODE
)

def clean_text_for_speech(text: str) -> str:
    """Limpia el texto formateado en Markdown para locución fluida."""
    if not text:
        return ""
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<think>[\s\S]*$', '', text, flags=re.IGNORECASE)
    clean = EMOJI_REGEX.sub('', text)
    clean = re.sub(r'```[\s\S]*?```', ' [bloque de código en pantalla] ', clean)
    clean = re.sub(r'https?://\S+', ' [enlace web] ', clean)
    clean = re.sub(r'[`*#_~>\-\+=|\\\/\[\]\{\}]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

TECHNICAL_CORRECTIONS = {
    r'\b(patitojar|patito jar|patito-jar|patito_jar)\b': 'PatitoJar',
    r'\b(yarbiz|yarvis|jervis|yarbis|jarbis|yervis)\b': 'Jarvis',
    r'\b(pato|patita|pateito)\b': 'Patito',
    r'\b(piton|paiton|pito)\b': 'Python',
    r'\b(llango|djan go|yango|jango)\b': 'Django',
    r'\b(yavascrip|javascrip|ya bascript|yavaship)\b': 'JavaScript',
    r'\b(vaquend|bacend|baquend)\b': 'backend',
    r'\b(frondend|fronent|frondend)\b': 'frontend',
    r'\b(res api|restapi|api rest)\b': 'API REST',
    r'\b(depurarion|depuración|depurar)\b': 'depurar',
    r'\b(clace|clasesita)\b': 'clase',
    r'\b(escop|scop)\b': 'scope',
    r'\b(herensia|erencia)\b': 'herencia',
    r'\b(polimorfesmo|polimorfismo)\b': 'polimorfismo',
    r'\b(encapsulacion|encapsulamieto)\b': 'encapsulamiento',
}

def correct_transcription_text(text: str) -> str:
    if not text:
        return ""
    corrected = text
    for pattern, replacement in TECHNICAL_CORRECTIONS.items():
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
    return corrected.strip()


# -----------------------------------------------------------------------------
# RECONOCIMIENTO Y VERIFICACIÓN DE FIRMA VOCAL DE GERARDO (SPEAKER VERIFIER)
# -----------------------------------------------------------------------------
class SpeakerVerifier:
    """Análisis de espectro de frecuencia F0, centroide espectral y bandas para verificación de voz."""
    def __init__(self, profile_path: Optional[str] = None):
        if profile_path is None:
            profile_path = os.path.join(os.path.dirname(__file__), "gerardo_voice_profile.json")
        self.profile_path = profile_path
        self.profile_data = self.load_profile()

    def load_profile(self) -> Optional[dict]:
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"No se pudo cargar el perfil de voz de Gerardo: {e}")
        return None

    def extract_features(self, wav_bytes: bytes) -> Optional[np.ndarray]:
        if np is None or not wav_bytes:
            return None
        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw_data = wf.readframes(n_frames)

            signal = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32)
            if len(signal) == 0 or np.max(np.abs(signal)) < 10.0:
                return None

            signal = signal / (np.max(np.abs(signal)) + 1e-6)

            frame_size = 512
            hop_size = 256
            num_frames = max(1, (len(signal) - frame_size) // hop_size)

            f0_list = []
            centroids = []
            bands = np.zeros(5, dtype=np.float32)
            zcr_list = []

            freqs = np.fft.rfftfreq(frame_size, d=1.0/sample_rate)

            for i in range(num_frames):
                frame = signal[i * hop_size : i * hop_size + frame_size]
                if len(frame) < frame_size:
                    continue

                rms = np.sqrt(np.mean(frame**2))
                if rms < 0.015:
                    continue

                zcr = np.sum(np.abs(np.diff(np.sign(frame)))) / (2.0 * frame_size)
                zcr_list.append(zcr)

                fft_mag = np.abs(np.fft.rfft(frame * np.hanning(frame_size)))
                total_mag = np.sum(fft_mag) + 1e-6

                centroid = np.sum(freqs * fft_mag) / total_mag
                centroids.append(centroid)

                b1 = np.sum(fft_mag[(freqs >= 50) & (freqs < 300)])
                b2 = np.sum(fft_mag[(freqs >= 300) & (freqs < 800)])
                b3 = np.sum(fft_mag[(freqs >= 800) & (freqs < 2000)])
                b4 = np.sum(fft_mag[(freqs >= 2000) & (freqs < 4000)])
                b5 = np.sum(fft_mag[(freqs >= 4000) & (freqs <= 8000)])
                bands += np.array([b1, b2, b3, b4, b5], dtype=np.float32) / total_mag

                corr = np.correlate(frame, frame, mode='full')
                corr = corr[len(corr)//2:]
                d = np.diff(corr)
                start = np.where(d > 0)[0]
                if len(start) > 0:
                    peak = np.argmax(corr[start[0]:]) + start[0]
                    if peak > 0:
                        f0 = sample_rate / peak
                        if 60 <= f0 <= 400:
                            f0_list.append(f0)

            if len(centroids) == 0:
                return None

            f0_mean = float(np.mean(f0_list)) if len(f0_list) > 0 else 130.0
            f0_std = float(np.std(f0_list)) if len(f0_list) > 0 else 20.0
            centroid_mean = float(np.mean(centroids))
            bands_norm = (bands / (num_frames + 1e-6)).tolist()
            zcr_mean = float(np.mean(zcr_list)) if len(zcr_list) > 0 else 0.05

            return np.array([f0_mean, f0_std, centroid_mean, zcr_mean] + bands_norm, dtype=np.float32)

        except Exception as e:
            logger.error(f"Error extrayendo características de voz: {e}")
            return None

    def calibrate_profile(self, wav_bytes: bytes) -> bool:
        vec = self.extract_features(wav_bytes)
        if vec is None:
            logger.warning("No se pudo extraer huella de voz para calibración.")
            return False

        self.profile_data = {
            "speaker_name": "Gerardo",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "feature_vector": vec.tolist()
        }
        try:
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(self.profile_data, f, indent=2)
            logger.info(f"Perfil de voz de Gerardo guardado en {self.profile_path}")
            return True
        except Exception as e:
            logger.error(f"Error guardando perfil de voz: {e}")
            return False

    def verify_speaker(self, wav_bytes: bytes, threshold: float = 0.48) -> Tuple[bool, float]:
        if not self.profile_data or "feature_vector" not in self.profile_data:
            return True, 1.0

        vec_cand = self.extract_features(wav_bytes)
        if vec_cand is None:
            return False, 0.0

        vec_target = np.array(self.profile_data["feature_vector"], dtype=np.float32)

        scale = np.array([150.0, 30.0, 2000.0, 0.1, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        norm_cand = vec_cand / scale
        norm_target = vec_target / scale

        cos_sim = np.dot(norm_cand, norm_target) / (np.linalg.norm(norm_cand) * np.linalg.norm(norm_target) + 1e-6)
        score = float(cos_sim)

        is_match = score >= threshold
        logger.info(f"[SpeakerVerifier] Puntuación similitud con Gerardo: {score:.3f} (Match: {is_match})")
        return is_match, score

_GLOBAL_SPEAKER_VERIFIER = None
def get_speaker_verifier() -> SpeakerVerifier:
    global _GLOBAL_SPEAKER_VERIFIER
    if _GLOBAL_SPEAKER_VERIFIER is None:
        _GLOBAL_SPEAKER_VERIFIER = SpeakerVerifier()
    return _GLOBAL_SPEAKER_VERIFIER

WAKE_WORDS = ["patitojar", "patito jar", "patito", "jarvis", "jarbis", "jervis", "yarvis", "patita", "pato"]
STOP_WORDS = [
    "basta", "baasta", "bastante", "calla", "cállate", "callate", "silencio", "silecio", "silensio",
    "alto", "pausa", "stop", "listo", "suficiente", "parar", "pará", "parate",
    "corta", "cortá", "silenciar", "silenciate", "shh", "shhh",
    "patito basta", "patito cállate", "patito callate", "patito stop", "patito silencio"
]
CORRECTION_WORDS = [
    "tengo otra pregunta", "otra pregunta", "corrijo mi pregunta", "corrijo",
    "reformulo mi pregunta", "reformulo", "cambio mi pregunta", "error en tu respuesta",
    "te equivocaste", "no es así", "espera", "escuchame", "escuchá", "escucha"
]

def parse_voice_command(text: str) -> Dict[str, Any]:
    cleaned = text.lower().strip()
    if not cleaned:
        return {"has_wake_word": False, "is_stop": False, "is_correction": False, "query": ""}

    is_stop = any(sw in cleaned for sw in STOP_WORDS) or cleaned in ["alto", "pausa", "silencio", "stop", "basta", "shh", "callate", "cállate"]
    is_correction = any(cw in cleaned for cw in CORRECTION_WORDS)

    wake_found = None
    for ww in WAKE_WORDS:
        if ww in cleaned:
            wake_found = ww
            break

    has_wake_word = wake_found is not None
    query = ""
    if has_wake_word:
        idx = cleaned.find(wake_found)
        if idx != -1:
            raw_q = text[idx + len(wake_found):].strip()
            raw_q = re.sub(r'^[,\.\s\:\-]+', '', raw_q).strip()
            query = raw_q if raw_q else text.strip()
        else:
            query = text.strip()

    return {
        "has_wake_word": has_wake_word,
        "is_stop": is_stop,
        "is_correction": is_correction,
        "query": query
    }


# =============================================================================
# ABSTRACCIÓN DE FRAMES Y PIPELINE PIPECAT
# =============================================================================
class Frame:
    """Clase base de Frame en el pipeline de Pipecat."""
    pass

class AudioInputFrame(Frame):
    def __init__(self, data):
        self.data = data

class TextFrame(Frame):
    def __init__(self, text: str, wav_bytes: Optional[bytes] = None, is_final: bool = True):
        self.text = text
        self.wav_bytes = wav_bytes
        self.is_final = is_final

class TTSAudioFrame(Frame):
    def __init__(self, text: str):
        self.text = text

class ControlFrame(Frame):
    def __init__(self, command: str, payload: Any = None):
        self.command = command
        self.payload = payload


# =============================================================================
# COMPONENTES MODULARES PIPECAT (TRANSPORT, VAD, STT, AGGREGATOR, TTS)
# =============================================================================

class AudioInputTransport:
    """Captura continua de micrófono utilizando sounddevice."""
    def __init__(self, output_queue: queue.Queue):
        self.output_queue = output_queue
        self.stream = None
        self.is_running = False

    def _audio_callback(self, indata, frames, time_info, status):
        if self.is_running:
            self.output_queue.put(AudioInputFrame(indata.copy()))

    def start(self):
        if self.is_running or not SD_AVAILABLE or not sd:
            return
        self.is_running = True
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=BLOCK_SIZE,
                dtype=DTYPE,
                callback=self._audio_callback
            )
            self.stream.start()
            logger.info("AudioInputTransport: Micrófono iniciado.")
        except Exception as e:
            logger.error(f"AudioInputTransport error: {e}")

    def stop(self):
        self.is_running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        logger.info("AudioInputTransport: Micrófono detenido.")


class VADProcessor:
    """Procesador VAD con Ring Buffer y soporte de exlusión mutua (Digital Muting)."""
    def __init__(self, input_queue: queue.Queue, output_queue: queue.Queue, is_speaking_func: Callable[[], bool]):
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.is_speaking_func = is_speaking_func
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PipecatVAD")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        chunks_per_sec = SAMPLE_RATE / BLOCK_SIZE
        ring_buffer_chunks = max(1, int(VAD_RING_BUFFER_SEC * chunks_per_sec))
        silence_limit_chunks = max(1, int(VAD_SILENCE_TIMEOUT_SEC * chunks_per_sec))
        max_speaking_chunks = max(1, int(0.8 * chunks_per_sec))

        ring_buffer = collections.deque(maxlen=ring_buffer_chunks)
        speech_frames = []
        is_speech_active = False
        silence_counter = 0

        while not self._stop_event.is_set():
            try:
                frame: AudioInputFrame = self.input_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            chunk = frame.data
            if np is not None:
                audio_data = chunk.flatten().astype(np.float32)
                rms = float(np.sqrt(np.mean(audio_data ** 2))) if len(audio_data) > 0 else 0.0
            else:
                rms = 0.0

            is_speaking = self.is_speaking_func()
            threshold = VAD_SPEAKING_ENERGY_THRESHOLD if is_speaking else VAD_MIN_ENERGY_THRESHOLD
            is_chunk_speech = rms > threshold



            if not is_speech_active:
                ring_buffer.append(chunk)
                if is_chunk_speech:
                    is_speech_active = True
                    speech_frames.extend(list(ring_buffer))
                    ring_buffer.clear()
                    silence_counter = 0
            else:
                speech_frames.append(chunk)
                if is_chunk_speech:
                    silence_counter = 0
                else:
                    silence_counter += 1

                force_slice = is_speaking and len(speech_frames) >= max_speaking_chunks

                if silence_counter >= silence_limit_chunks or force_slice:
                    is_speech_active = False
                    silence_counter = 0

                    if len(speech_frames) > ring_buffer_chunks:
                        wav_bytes = self._audio_frames_to_wav(speech_frames)
                        if wav_bytes:
                            self.output_queue.put(wav_bytes)

                    speech_frames.clear()
                    ring_buffer.clear()

    def _audio_frames_to_wav(self, frames) -> Optional[bytes]:
        try:
            if np is None:
                return None
            full_audio = np.concatenate(frames, axis=0)
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(full_audio.tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Error convirtiendo frames a WAV: {e}")
            return None


class STTProcessor:
    """Procesador STT asíncrono que consume WAV y produce TextFrame."""
    def __init__(self, input_queue: queue.Queue, on_text_callback: Callable[[TextFrame], None]):
        self.input_queue = input_queue
        self.on_text_callback = on_text_callback
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PipecatSTT")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                wav_bytes = self.input_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            temp_path = os.path.join(os.path.expanduser("~"), f"temp_pipecat_{uuid.uuid4().hex[:8]}.wav")
            try:
                with open(temp_path, "wb") as f:
                    f.write(wav_bytes)

                text = ""
                # 1. Google SR ultra-rápido
                if SR_AVAILABLE and sr:
                    try:
                        r = sr.Recognizer()
                        with sr.AudioFile(temp_path) as source:
                            audio_data = r.record(source)
                            text = r.recognize_google(audio_data, language="es-AR")
                    except Exception:
                        pass

                # 2. Faster-Whisper local fallback
                if not text or not text.strip():
                    model = get_whisper_model()
                    if model:
                        segments, _ = model.transcribe(
                            temp_path,
                            language="es",
                            beam_size=1,
                            best_of=1,
                            vad_filter=True
                        )
                        text = " ".join([seg.text for seg in segments]).strip()

                text = correct_transcription_text(text)
                if text and text.strip():
                    logger.debug(f"STTProcessor: Transcrito -> '{text}'")
                    self.on_text_callback(TextFrame(text=text.strip(), wav_bytes=wav_bytes, is_final=True))

            except Exception as e:
                logger.error(f"STTProcessor error: {e}")
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass


class LLMContext:
    """Gestor de contexto conversacional de la sesión."""
    def __init__(self, session_id: Optional[int] = None):
        self.session_id = session_id
        self.messages: List[Dict[str, str]] = []

    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str):
        self.messages.append({"role": "assistant", "content": text})


class LLMContextAggregator:
    """Agregador de mensajes e integración con Backend seguro de Django."""
    def __init__(self, backend_url: str = "http://127.0.0.1:8000"):
        self.backend_url = backend_url
        self.context = LLMContext()

    def process_speech_input(self, user_text: str, code_context: str = "") -> Generator[str, None, None]:
        """
        Envía la consulta del usuario al backend seguro mediante streaming HTTP.
        Retorna generador de tokens emitidos en tiempo real sin exponer API Keys.
        """
        import requests
        self.context.add_user_message(user_text)

        payload = {
            "message": user_text,
            "code_context": code_context
        }
        if self.context.session_id:
            payload["session_id"] = self.context.session_id

        headers = {"Content-Type": "application/json"}
        url = f"{self.backend_url}/api/v1/chat/stream/"

        try:
            with requests.post(url, json=payload, headers=headers, stream=True, timeout=(5.0, 60.0)) as response:
                if response.status_code == 200:
                    sess_header = response.headers.get("X-Session-ID")
                    if sess_header:
                        try:
                            self.context.session_id = int(sess_header)
                        except Exception:
                            pass

                    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                        if chunk:
                            yield chunk
                else:
                    yield f"Error backend ({response.status_code}): {response.text}"
        except Exception as e:
            logger.error(f"Fallo conexión backend stream: {e}")
            yield "¡Che Gerar, tuve un pequeño problema de conexión con el backend! Reintentá en un instante, crack."


class TTSProcessor:
    """Sintetizador TTS por oraciones y salida de audio fluida sin cortes."""
    def __init__(self, backend_url: str = "http://127.0.0.1:8000"):
        self.backend_url = backend_url
        self._lock = threading.Lock()
        self.is_speaking = False

    def speak_text(self, text: str, voice: str = "es-AR-TomasNeural", stop_event: Optional[threading.Event] = None) -> bool:
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return False

        with self._lock:
            self.is_speaking = True

        played = False

        # 1. Edge-TTS local
        if not stop_event or not stop_event.is_set():
            try:
                import asyncio
                import edge_tts
                import pygame

                mp3_path = os.path.join(os.path.expanduser("~"), f"temp_tts_pipe_{uuid.uuid4().hex[:8]}.mp3")
                async def _gen():
                    comm = edge_tts.Communicate(clean_text, voice, rate="+10%")
                    await comm.save(mp3_path)
                asyncio.run(_gen())

                if os.path.exists(mp3_path) and (not stop_event or not stop_event.is_set()):
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.play()

                    while pygame.mixer.music.get_busy() and (not stop_event or not stop_event.is_set()):
                        time.sleep(0.03)

                    pygame.mixer.music.unload()
                    try:
                        os.remove(mp3_path)
                    except Exception:
                        pass
                    played = True
            except Exception as e:
                logger.debug(f"Edge-TTS falló: {e}")

        # 2. pyttsx3 local
        if not played and (not stop_event or not stop_event.is_set()) and PYTTSX3_AVAILABLE and pyttsx3:
            try:
                engine = get_pyttsx3_engine()
                if engine:
                    engine.say(clean_text)
                    engine.runAndWait()
                    played = True
            except Exception as e:
                logger.error(f"pyttsx3 falló: {e}")

        # 3. Fallback Backend DRF /api/v1/tts/
        if not played and (not stop_event or not stop_event.is_set()):
            try:
                import requests
                import pygame
                url = f"{self.backend_url}/api/v1/tts/"
                resp = requests.post(url, json={"text": clean_text, "voice": voice}, timeout=8)
                if resp.status_code == 200 and resp.content:
                    mp3_path = os.path.join(os.path.expanduser("~"), f"temp_tts_back_{uuid.uuid4().hex[:8]}.mp3")
                    with open(mp3_path, "wb") as f:
                        f.write(resp.content)
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy() and (not stop_event or not stop_event.is_set()):
                        time.sleep(0.03)
                    pygame.mixer.music.unload()
                    try:
                        os.remove(mp3_path)
                    except Exception:
                        pass
                    played = True
            except Exception as e_back:
                logger.warning(f"DRF TTS endpoint falló: {e_back}")

        with self._lock:
            self.is_speaking = False

        return played


# =============================================================================
# MOTOR DE VOZ PRINCIPAL DESACOPLADO (PIPECAT VOICE ENGINE)
# =============================================================================
class PipecatVoiceEngine:
    """
    Motor orquestador de voz Pipecat.
    Coordina AudioInputTransport -> VADProcessor -> STTProcessor -> LLMContextAggregator -> TTSProcessor.
    """
    def __init__(self, backend_url: str = "http://127.0.0.1:8000",
                 on_state_change: Optional[Callable[[str], None]] = None,
                 on_transcription: Optional[Callable[[str], None]] = None,
                 on_token: Optional[Callable[[str], None]] = None,
                 enable_llm_trigger: bool = True):

        self.backend_url = backend_url
        self.on_state_change = on_state_change
        self.on_transcription = on_transcription
        self.on_token = on_token
        self.enable_llm_trigger = enable_llm_trigger

        self.audio_raw_queue = queue.Queue()
        self.audio_speech_queue = queue.Queue()

        self._is_speaking_flag = False
        self._lock = threading.Lock()

        # Componentes
        self.input_transport = AudioInputTransport(self.audio_raw_queue)
        self.vad_processor = VADProcessor(
            input_queue=self.audio_raw_queue,
            output_queue=self.audio_speech_queue,
            is_speaking_func=self.get_is_speaking
        )
        self.stt_processor = STTProcessor(
            input_queue=self.audio_speech_queue,
            on_text_callback=self._handle_stt_result
        )
        self.aggregator = LLMContextAggregator(backend_url=self.backend_url)
        self.tts_processor = TTSProcessor(backend_url=self.backend_url)

        self._stop_event = threading.Event()
        self._current_state = "IDLE"

    def get_is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking_flag or self.tts_processor.is_speaking

    def set_is_speaking(self, val: bool):
        with self._lock:
            self._is_speaking_flag = val

    def set_state(self, new_state: str):
        self._current_state = new_state
        if self.on_state_change:
            self.on_state_change(new_state)

    def start(self):
        logger.info("Iniciando PipecatVoiceEngine...")
        self._stop_event.clear()
        self.vad_processor.start()
        self.stt_processor.start()
        self.input_transport.start()
        self.set_state("LISTEN")

    def stop(self):
        logger.info("Deteniendo PipecatVoiceEngine...")
        self._stop_event.set()
        self.input_transport.stop()
        self.vad_processor.stop()
        self.stt_processor.stop()
        self.set_state("IDLE")

    def _handle_stt_result(self, text_frame: TextFrame):
        user_text = text_frame.text
        if not user_text:
            return

        if self.on_transcription:
            self.on_transcription(user_text)

        # Procesar con el Agregador LLM únicamente si no hay callback externo o si enable_llm_trigger está activo explícitamente sin callback
        if self.enable_llm_trigger and not self.on_transcription:
            threading.Thread(target=self._process_llm_and_tts, args=(user_text,), daemon=True).start()


    def _process_llm_and_tts(self, user_text: str):
        self.set_state("THINK")
        sentence_buffer = ""
        full_response = ""

        # Delimitadores de oraciones para sintetizar audio en streaming
        delimiters = re.compile(r'([.?!;\n]+)')

        for token in self.aggregator.process_speech_input(user_text):
            if self._stop_event.is_set():
                break

            if self.on_token:
                self.on_token(token)

            full_response += token
            sentence_buffer += token

            parts = delimiters.split(sentence_buffer)
            if len(parts) > 1:
                # Sintetizar oraciones completadas
                for i in range(0, len(parts) - 1, 2):
                    sentence = parts[i] + parts[i+1]
                    if sentence.strip():
                        self.set_state("SPEAK")
                        self.set_is_speaking(True)
                        self.tts_processor.speak_text(sentence, stop_event=self._stop_event)
                        self.set_is_speaking(False)

                sentence_buffer = parts[-1]

        # Sintetizar remanente de respuesta
        if sentence_buffer.strip() and not self._stop_event.is_set():
            self.set_state("SPEAK")
            self.set_is_speaking(True)
            self.tts_processor.speak_text(sentence_buffer, stop_event=self._stop_event)
            self.set_is_speaking(False)

        self.aggregator.context.add_assistant_message(full_response)
        self.set_state("LISTEN")

    def speak_direct(self, text: str):
        """Método directo para reproducir un texto en TTS."""
        def _job():
            self.set_state("SPEAK")
            self.set_is_speaking(True)
            self.tts_processor.speak_text(text, stop_event=self._stop_event)
            self.set_is_speaking(False)
            self.set_state("LISTEN")
        threading.Thread(target=_job, daemon=True).start()


# Mantener VoicePipeline para retrocompatibilidad directa
VoicePipeline = PipecatVoiceEngine


# =============================================================================
# ADAPTADORES DE COMPATIBILIDAD CON PYQT6 (WORKERS DE INTERFAZ GRÁFICA)
# =============================================================================
if PYQT_AVAILABLE and QThread is not object:

    class PipecatVoiceWorker(QThread):
        """
        Hilo PyQt6 para orquestación total del pipeline Pipecat con emisión de señales desacopladas.
        """
        state_changed = pyqtSignal(str)              # 'LISTEN', 'THINK', 'SPEAK', 'IDLE'
        user_speech_detected = pyqtSignal(str)       # Texto transcrito del usuario
        llm_token_received = pyqtSignal(str)         # Token del modelo LLM
        assistant_response_complete = pyqtSignal(str) # Respuesta final completa
        error_occurred = pyqtSignal(str)

        def __init__(self, backend_url="http://127.0.0.1:8000"):
            super().__init__()
            self.backend_url = backend_url
            self.engine = None

        def run(self):
            def _on_state(s):
                self.state_changed.emit(s)

            def _on_trans(t):
                self.user_speech_detected.emit(t)

            def _on_tok(tok):
                self.llm_token_received.emit(tok)

            self.engine = PipecatVoiceEngine(
                backend_url=self.backend_url,
                on_state_change=_on_state,
                on_transcription=_on_trans,
                on_token=_on_tok
            )
            self.engine.start()

            while not self.isInterruptionRequested():
                self.msleep(100)

            if self.engine:
                self.engine.stop()

        def stop(self):
            self.requestInterruption()
            if self.engine:
                self.engine.stop()


    class STTWorker(QThread):
        """Hilo PyQt6 para grabación manual interactiva por botón ('Voz' -> 'Enviar Voz')."""
        text_transcribed = pyqtSignal(str)
        error_signal = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self._is_recording = True
            self.recorded_frames = []

        def stop_recording(self):
            self._is_recording = False

        def run(self):
            wav_path = os.path.join(os.path.expanduser("~"), f"temp_stt_manual_{uuid.uuid4().hex[:8]}.wav")
            try:
                if SD_AVAILABLE and sd and np:
                    logger.info("Iniciando grabación de voz manual...")
                    def _record_callback(indata, frames, time_info, status):
                        if self._is_recording:
                            self.recorded_frames.append(indata.copy())

                    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE, dtype=DTYPE, callback=_record_callback):
                        while self._is_recording:
                            self.msleep(50)

                    if not self.recorded_frames:
                        self.error_signal.emit("No se capturó audio en el micrófono.")
                        return

                    full_audio = np.concatenate(self.recorded_frames, axis=0)
                    with wave.open(wav_path, 'wb') as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(full_audio.tobytes())

                elif SR_AVAILABLE and sr:
                    r = sr.Recognizer()
                    with sr.Microphone(sample_rate=SAMPLE_RATE) as source:
                        audio_data = r.listen(source, timeout=10, phrase_time_limit=15)
                        with open(wav_path, "wb") as f:
                            f.write(audio_data.get_wav_data())

                else:
                    self.error_signal.emit("No hay ningún backend de captura de audio disponible.")
                    return

                text = ""
                model = get_whisper_model()
                if model and os.path.exists(wav_path):
                    segments, _ = model.transcribe(wav_path, language="es", beam_size=1, best_of=1, vad_filter=True)
                    text = " ".join([segment.text for segment in segments]).strip()
                elif SR_AVAILABLE and sr and os.path.exists(wav_path):
                    r = sr.Recognizer()
                    with sr.AudioFile(wav_path) as source:
                        audio_data = r.record(source)
                        text = r.recognize_google(audio_data, language="es-ES")

                if os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass

                text = correct_transcription_text(text)

                if text and text.strip():
                    logger.info(f"Transcripción manual completada: '{text}'")
                    self.text_transcribed.emit(text.strip())
                else:
                    self.error_signal.emit("No se escuchó voz clara. Intenta hablar más cerca del micrófono.")

            except Exception as e:
                logger.error(f"Error en STTWorker manual: {e}")
                if os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass
                self.error_signal.emit(f"Error en grabación: {str(e)}")


    class WakeWordWorker(QThread):
        """Hilo PyQt6 en segundo plano para Modo Atento con verificación de voz de Gerardo y 'Patito' + consulta."""
        wake_command_detected = pyqtSignal(str)
        stop_command_detected = pyqtSignal()

        def __init__(self, parent_overlay=None):
            super().__init__()
            self.parent_overlay = parent_overlay
            self.engine = None
            self.last_wake_time = 0.0
            self.pending_wake = False

        def stop_listening(self):
            if self.engine:
                self.engine.stop()
            self.requestInterruption()

        def run(self):
            def _handle_transcription(text_frame: Any):
                if isinstance(text_frame, TextFrame):
                    text = text_frame.text
                    wav_bytes = text_frame.wav_bytes
                else:
                    text = str(text_frame)
                    wav_bytes = None

                cleaned = text.lower().strip()
                if not cleaned:
                    return

                cmd = parse_voice_command(text)
                is_speaking = self.parent_overlay and getattr(self.parent_overlay, 'is_speaking', False)
                now = time.time()
                last_stop = getattr(self.parent_overlay, 'last_tts_stop_time', 0.0) if self.parent_overlay else 0.0

                # 1. Comando explícito de detención ('basta', 'alto', 'cállate', 'stop', 'shh', 'pará') -> CERO LATENCIA
                # ¡SE EJECUTA ANTES QUE EL SPEAKER VERIFIER Y SIN BLOQUEOS DE LOCUCIÓN!
                if cmd["is_stop"]:
                    logger.info(f"⚡ [STOP INSTANTÁNEO] Comando '{text}' recibido. Cortando voz al instante.")
                    if self.parent_overlay:
                        self.parent_overlay.stop_audio()
                    self.stop_command_detected.emit()
                    self.pending_wake = False
                    return

                # 2. Cooldown post-locución para evitar eco de rebote en habitación (0.2s)
                if not is_speaking and (now - last_stop) < 0.2:
                    logger.debug(f"Audio ignorado (Buffer de enfriamiento post-locución 0.2s): '{text}'")
                    return

                # 3. Durante locución de Patito, descartar cualquier frase que no sea un comando de detención
                if is_speaking:
                    logger.debug(f"Audio ignorado durante locución (no es comando de detención): '{text}'")
                    return

                # 4. Verificación de identidad de voz con Gerardo para consultas LLM (umbral 0.48)
                verifier = get_speaker_verifier()
                if wav_bytes and verifier.profile_data:
                    is_gerardo, score = verifier.verify_speaker(wav_bytes, threshold=0.48)
                    if not is_gerardo and not cmd["has_wake_word"]:
                        logger.info(f"Audio rechazado por SpeakerVerifier: No coincide con Gerardo (similitud {score:.2f}).")
                        return

                # 5. Consulta válida con activación 'Patito' o continuación de wake word reciente (< 3.0s)
                has_pending = self.pending_wake and (now - self.last_wake_time < 3.0)

                if cmd["has_wake_word"]:
                    query_text = cmd["query"] if cmd["query"] else text
                    clean_q = re.sub(r'^(patitojar|patito jar|patito|jarvis|jarbis|jervis|yarvis|patita|pato)\b', '', query_text.lower()).strip()
                    clean_q = re.sub(r'^[,\.\s\:\-]+', '', clean_q).strip()

                    if clean_q:
                        self.pending_wake = False
                        logger.info(f"Activación 'Patito' + consulta detectada de Gerardo: '{query_text}'")
                        self.wake_command_detected.emit(query_text)
                    else:
                        self.pending_wake = True
                        self.last_wake_time = now
                        logger.info("Activación 'Patito' aislada recibida. Aguardando consulta inmediata en los próximos 3s...")
                elif has_pending:
                    self.pending_wake = False
                    logger.info(f"Consulta de seguimiento vinculada a 'Patito': '{text}'")
                    self.wake_command_detected.emit(text)
                else:
                    logger.debug(f"Frase ignorada silenciosamente (no contiene 'Patito' + consulta): '{text}'")

            backend_url = getattr(self.parent_overlay, "backend_url", "http://127.0.0.1:8000") if self.parent_overlay else "http://127.0.0.1:8000"
            self.engine = PipecatVoiceEngine(
                backend_url=backend_url,
                on_transcription=_handle_transcription,
                enable_llm_trigger=False
            )
            self.engine.start()

            while not self.isInterruptionRequested():
                if self.parent_overlay and hasattr(self.parent_overlay, 'is_speaking'):
                    self.engine.set_is_speaking(bool(self.parent_overlay.is_speaking))
                self.msleep(100)

            self.engine.stop()



    class TTSWorker(QThread):
        """Hilo PyQt6 para síntesis TTS síncrona y fluida."""
        finished_speaking = pyqtSignal()

        def __init__(self, text, voice="es-AR-TomasNeural"):
            super().__init__()
            self.text = text
            self.voice = voice
            self._is_stopped = False

        def stop_audio(self):
            self._is_stopped = True
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except Exception:
                pass

        def run(self):
            clean_text = clean_text_for_speech(self.text)
            if not clean_text or self._is_stopped:
                self.finished_speaking.emit()
                return

            logger.info(f"TTSWorker reproduciendo: '{clean_text[:60]}...'")
            tts_processor = TTSProcessor()
            stop_evt = threading.Event()

            def _check_stop():
                while not self.isFinished():
                    if self._is_stopped:
                        stop_evt.set()
                        break
                    self.msleep(30)

            threading.Thread(target=_check_stop, daemon=True).start()
            tts_processor.speak_text(clean_text, voice=self.voice, stop_event=stop_evt)

            self.msleep(50)
            self.finished_speaking.emit()

else:
    class PipecatVoiceWorker:
        pass
    class STTWorker:
        pass
    class WakeWordWorker:
        pass
    class TTSWorker:
        pass


# -----------------------------------------------------------------------------
# EJECUCIÓN DIRECTA PARA PRUEBA INDEPENDIENTE DEL MÓDULO PIPECAT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== MÓDULO DE VOZ PATITOJAR PIPECAT (PRUEBA INDEPENDIENTE) ===")

    def on_state(s):
        print(f"[ESTADO PIPECAT]: {s}")

    def on_trans(t):
        print(f"\n[TRANSCRIPCIÓN PIPECAT]: {t}\n")

    def on_tok(tok):
        print(tok, end="", flush=True)

    engine = PipecatVoiceEngine(
        backend_url="http://127.0.0.1:8000",
        on_state_change=on_state,
        on_transcription=on_trans,
        on_token=on_tok
    )
    engine.start()

    print("Escuchando micrófono continuamente... Di 'PatitoJar' o habla libremente.")
    print("Presiona Ctrl+C para salir.")

    try:
        engine.speak_direct("Hola Gerardo. El motor de voz Pipecat continuo para PatitoJar está activo.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo engine de voz Pipecat...")
        engine.stop()
        print("Listo.")
