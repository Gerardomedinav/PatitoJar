import sys
import os
import time
import math
import re
import html
import requests
import traceback
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QTextEdit, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QThread, pyqtSignal, QRectF
from PyQt6.QtGui import QColor, QFont, QPixmap, QPainter, QBrush, QPen
from PyQt6.QtSvg import QSvgRenderer

# Logger for uncaught crashes to prevent silent application closure
def log_uncaught_exceptions(exctype, value, tb):
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    print("UNCAUGHT EXCEPTION CRASH:\n", err_msg)
    try:
        log_path = os.path.join(os.path.dirname(__file__), "patito_jar_crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- CRASH DETECTADO: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(err_msg)
    except Exception:
        pass

sys.excepthook = log_uncaught_exceptions

from patito_jar_voice import (
    STTWorker, WakeWordWorker, TTSWorker, 
    PipecatVoiceWorker, PipecatVoiceEngine, 
    get_whisper_model, correct_transcription_text
)

INSTANT_PRE_FILLERS = [
    "¡Che Gerar, estoy genial, gracias por preguntar!",
    "¡Ger, qué genial que lo consultes! Te paso todo lo que necesites, Ger, para eso estoy.",
    "¡Espectacular consulta, Gerar! Dejame explicártelo al detalle.",
    "¡De una, crack! Al instante te paso toda la información, para eso estamos.",
    "¡Excelente, Ger! Dejame contarte todo sobre esto, sos un grande."
]


class ChatAPIWorker(QThread):
    """Worker thread for async streaming API requests to Django Backend (<200ms latency)."""
    success_signal = pyqtSignal(dict)
    token_received = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, backend_url, session_id, message, code_context):
        super().__init__()
        self.backend_url = backend_url
        self.session_id = session_id
        self.message = message
        self.code_context = code_context

    def run(self):
        try:
            payload = {
                "message": self.message,
                "code_context": self.code_context
            }
            if self.session_id:
                payload["session_id"] = self.session_id

            headers = {"Content-Type": "application/json"}
            url = f"{self.backend_url}/api/v1/chat/stream/"

            full_text = []
            with requests.post(url, json=payload, headers=headers, stream=True, timeout=(5.0, 60.0)) as response:
                if response.status_code == 200:
                    sess_id_header = response.headers.get("X-Session-ID")
                    if sess_id_header:
                        try:
                            self.session_id = int(sess_id_header)
                        except Exception:
                            pass

                    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                        if chunk:
                            full_text.append(chunk)
                            self.token_received.emit(chunk)

                    accumulated = "".join(full_text)
                    self.success_signal.emit({
                        "session_id": self.session_id,
                        "assistant_message": accumulated
                    })
                else:
                    self.error_signal.emit(f"HTTP {response.status_code}: {response.text}")
        except requests.exceptions.Timeout:
            self.error_signal.emit("⚠️ La respuesta de la red tardó un poco más de lo esperado. ¡Por favor, reintentá tu consulta, genio!")
        except Exception as e:
            try:
                headers = {"Content-Type": "application/json"}
                resp = requests.post(f"{self.backend_url}/api/v1/chat/", json=payload, headers=headers, timeout=12)
                if resp.status_code == 200:
                    self.success_signal.emit(resp.json())
                else:
                    self.error_signal.emit(f"HTTP {resp.status_code}: {resp.text}")
            except Exception as ex:
                self.error_signal.emit(f"Conexión con Backend interrumpida: {str(ex)}")


def markdown_to_html(text: str) -> str:
    """
    Convierte sintaxis Markdown (bloques de código, negritas, viñetas, saltos de línea)
    a HTML estilizado para la ventana de chat QTextEdit con tema IDE Cyberpunk.
    """
    if not text:
        return ""

    code_blocks = []

    def replace_code_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        safe_code = html.escape(code.strip())
        block_html = (
            f'<div style="background-color: #0d1117; border: 1px solid #00f3ff; '
            f'border-radius: 6px; padding: 10px 14px; margin: 10px 0; '
            f'font-family: \'Consolas\', \'Cascadia Code\', \'Courier New\', monospace; '
            f'color: #00ff88; font-size: 12px; line-height: 1.5; white-space: pre-wrap;">'
            f'<div style="color: #00f3ff; font-weight: bold; margin-bottom: 6px; font-size: 11px;">[{lang.upper() if lang else "CÓDIGO"}]</div>'
            f'<pre style="margin:0; padding:0; font-family:inherit; white-space:pre-wrap;">{safe_code}</pre></div>'
        )
        code_blocks.append(block_html)
        return f"___CODE_BLOCK_{len(code_blocks)-1}___"

    # 1. Proteger y formatear bloques de código ```lang ... ```
    processed = re.sub(r'```(\w+)?\n?([\s\S]*?)```', replace_code_block, text)

    # 2. Escapar caracteres HTML en el texto fuera de bloques de código
    processed = html.escape(processed)

    # 3. Formatear markdown básico (código inline, negrita, cursiva, viñetas, títulos)
    processed = re.sub(r'`([^`]+)`', r'<code style="background-color: #1a233a; color: #00f3ff; padding: 2px 6px; border-radius: 4px; font-family: monospace;">\1</code>', processed)
    processed = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', processed)
    processed = re.sub(r'\*(.*?)\*', r'<i>\1</i>', processed)
    processed = re.sub(r'^\s*[\*\-]\s+(.*)$', r'&bull; \1', processed, flags=re.MULTILINE)
    processed = re.sub(r'^###\s+(.*)$', r'<h4 style="color: #00f3ff; margin: 8px 0 4px 0;">\1</h4>', processed, flags=re.MULTILINE)
    processed = re.sub(r'^##\s+(.*)$', r'<h3 style="color: #00f3ff; margin: 10px 0 6px 0;">\1</h3>', processed, flags=re.MULTILINE)

    # 4. Convertir saltos de línea fuera de bloques de código a <br>
    processed = processed.replace('\n', '<br>')

    # 5. Restaurar los bloques de código HTML previamente estilizados
    for idx, block_html in enumerate(code_blocks):
        processed = processed.replace(f"___CODE_BLOCK_{idx}___", block_html)

    return processed


class FloatingDuckWidget(QWidget):
    """Pure Floating Cyber Duck Widget with Native Animated SVG & PatitoJar HUD FX."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.state = "IDLE"  # IDLE, THINKING, SPEAKING, LISTENING
        self.rot_clockwise = 0.0
        self.rot_counter = 360.0
        self.pulse_val = 0.0
        self.scan_y = 0
        self.wave_size = 48
        self.float_offset = 0.0

        # Load Avatar Images (PNG / SVG)
        png_path = os.path.join(os.path.dirname(__file__), "patito_cool.png")
        svg_path = os.path.join(os.path.dirname(__file__), "patito_jarvis.svg")

        if os.path.exists(png_path):
            self.duck_pixmap = QPixmap(png_path)
        else:
            self.duck_pixmap = None

        if os.path.exists(svg_path):
            self.svg_renderer = QSvgRenderer(svg_path)
        else:
            self.svg_renderer = None

        # 60 FPS animation loop
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_hud_animation)
        self.anim_timer.start(25)

    def set_state(self, new_state):
        self.state = new_state
        self.update()

    def update_hud_animation(self):
        # 1. Concentric HUD Ring rotation & float wave
        self.rot_clockwise = (self.rot_clockwise + 2.5) % 360.0
        self.rot_counter = (self.rot_counter - 4.0) % 360.0
        self.pulse_val = (self.pulse_val + 0.12) % 6.28
        self.float_offset = 4.0 * math.sin(self.pulse_val)

        # 2. Scanner laser line (Thinking mode)
        if self.state == "THINKING":
            self.scan_y = (self.scan_y + 4) % self.height()

        # 3. Voice soundwaves (Speaking mode)
        if self.state == "SPEAKING":
            self.wave_size += 2.5
            if self.wave_size > 68:
                self.wave_size = 46

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        cx, cy = self.width() // 2, self.height() // 2

        # Color schemes based on state
        if self.state == "LISTENING":
            hud_color = QColor(255, 204, 0)
        elif self.state == "THINKING":
            hud_color = QColor(255, 0, 128)
        elif self.state == "SPEAKING":
            hud_color = QColor(0, 255, 136)
        else:
            hud_color = QColor(0, 240, 255)

        # --- ANIMATION 1: Concentric PatitoJar HUD Rings ---
        # Outer Ring
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self.rot_clockwise)
        pen_outer = QPen(hud_color, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen_outer)
        painter.drawEllipse(QPoint(0, 0), 62, 62)
        painter.restore()

        # Middle Ring
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self.rot_counter)
        pen_mid = QPen(hud_color, 2, Qt.PenStyle.DotLine)
        painter.setPen(pen_mid)
        painter.drawEllipse(QPoint(0, 0), 52, 52)
        painter.restore()

        # Inner Pulsing Ring
        pulse_scale = 42 + int(3.0 * math.sin(self.pulse_val))
        pen_inner = QPen(QColor(hud_color.red(), hud_color.green(), hud_color.blue(), 180), 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen_inner)
        painter.drawEllipse(QPoint(cx, cy), pulse_scale, pulse_scale)

        # --- ANIMATION 3: Voice Soundwaves (Speaking mode) ---
        if self.state == "SPEAKING":
            alpha = int(255 * (1.0 - (self.wave_size - 46) / 22.0))
            alpha = max(0, min(255, alpha))
            wave_pen = QPen(QColor(0, 255, 136, alpha), 2.5)
            painter.setPen(wave_pen)
            painter.drawEllipse(QPoint(cx, cy), int(self.wave_size), int(self.wave_size))

        # --- RENDER COOL DUCK AVATAR WITH FLOATING EFFECT ---
        duck_rect = QRectF(cx - 42, cy - 42 + self.float_offset, 84, 84)

        if hasattr(self, 'duck_pixmap') and self.duck_pixmap and not self.duck_pixmap.isNull():
            painter.drawPixmap(duck_rect.toRect(), self.duck_pixmap)
        elif self.svg_renderer and self.svg_renderer.isValid():
            self.svg_renderer.render(painter, duck_rect)
        else:
            painter.setFont(QFont("Consolas", 26))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "🐥")

        # --- ANIMATION 2: HUD Laser Scanner Line (Thinking mode) ---
        if self.state == "THINKING":
            scan_pen = QPen(QColor(255, 0, 128, 230), 3)
            painter.setPen(scan_pen)
            painter.drawLine(15, self.scan_y, self.width() - 15, self.scan_y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PatitoJarOverlay(QWidget):
    """Pure Floating Cyber Duck Overlay Widget with Native Animated SVG & Expandable Dialog for PatitoJar."""
    def __init__(self, backend_url="http://127.0.0.1:8000"):
        super().__init__()
        self.backend_url = backend_url
        self.session_id = None
        self.dialog_expanded = False
        self.old_pos = QPoint()
        self.active_threads = []
        self.api_in_progress = False
        self.current_tts_worker = None
        self.current_stt_worker = None
        self.is_recording_voice = False
        self.auto_dialog_loop = False
        self.wake_worker = None
        self.passive_listen_enabled = False
        self.is_speaking = False
        self.pending_answer = None
        self.speech_queue = []
        self.pre_synthesized_audio = {}
        self.stream_buffer = ""

        # Pre-inicializar pygame mixer para eliminar latencia de audio en TTS
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception:
            pass

        self.init_ui()

    def register_thread(self, thread):
        """Prevents QThread garbage collection while background execution is active."""
        self.active_threads.append(thread)
        thread.finished.connect(lambda: self.unregister_thread(thread))

    def unregister_thread(self, thread):
        if thread in self.active_threads:
            self.active_threads.remove(thread)

    def init_ui(self):
        # Frameless, 100% translucent background, always on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("PatitoJar HUD - Asistente de Depuración")

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = max(50, geo.width() - 520)
            y = max(50, (geo.height() - 650) // 2)
            self.setGeometry(x, y, 480, 620)
        else:
            self.setGeometry(250, 150, 480, 620)

        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(4)

        # 1. PURE FLOATING DUCK WIDGET (With SVG & HUD FX)
        duck_bar = QHBoxLayout()
        duck_bar.setContentsMargins(0, 0, 0, 0)
        
        self.duck_avatar = FloatingDuckWidget(self)
        self.duck_avatar.clicked.connect(self.toggle_dialog)
        duck_bar.addWidget(self.duck_avatar, alignment=Qt.AlignmentFlag.AlignLeft)

        self.main_layout.addLayout(duck_bar)

        # 2. EXPANDABLE CHAT DIALOG PANEL
        self.dialog_frame = QFrame(self)
        self.dialog_frame.setObjectName("dialogFrame")
        self.dialog_frame.setStyleSheet("""
            QFrame#dialogFrame {
                background-color: rgba(10, 17, 40, 248);
                border: 2px solid #00f3ff;
                border-radius: 14px;
            }
            QLabel {
                color: #00f3ff;
                font-family: Consolas, sans-serif;
            }
            QPushButton {
                background-color: #003366;
                color: #00f3ff;
                border: 1px solid #00f3ff;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-family: Consolas, sans-serif;
            }
            QPushButton:hover {
                background-color: #00f3ff;
                color: #0a1128;
            }
            QTextEdit {
                background-color: #050a18;
                color: #00f3ff;
                border: 1px solid #005588;
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                font-family: Consolas, sans-serif;
            }
            QLineEdit {
                background-color: #050a18;
                color: #ffffff;
                border: 1px solid #00f3ff;
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
                font-family: Consolas, sans-serif;
            }
        """)

        dialog_layout = QVBoxLayout(self.dialog_frame)
        dialog_layout.setContentsMargins(10, 10, 10, 10)

        # Header Row inside Dialog (Title, Status, Stop Audio)
        dialog_header = QHBoxLayout()
        
        header_text_box = QVBoxLayout()
        self.title_label = QLabel("PATITOJAR v1.0", self)
        self.title_label.setStyleSheet("color: #00f3ff; font-weight: bold; font-size: 13px;")

        self.status_label = QLabel("Rubber Duck Debugger Listo", self)
        self.status_label.setStyleSheet("color: #ffcc00; font-size: 10px;")

        header_text_box.addWidget(self.title_label)
        header_text_box.addWidget(self.status_label)
        dialog_header.addLayout(header_text_box)

        # Stop & Listen Audio Button inside Dialog Header
        self.btn_stop_audio = QPushButton("⏹️ Cortar y Hablar", self)
        self.btn_stop_audio.setToolTip("Detiene la voz actual e inicia el micrófono inmediatamente para reanudar la conversación.")
        self.btn_stop_audio.setStyleSheet("""
            QPushButton {
                background-color: #440011;
                color: #ff3366;
                border: 1px solid #ff3366;
                border-radius: 5px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #ff3366;
                color: #ffffff;
            }
        """)
        self.btn_stop_audio.clicked.connect(self.stop_audio_and_listen)
        dialog_header.addWidget(self.btn_stop_audio)

        # Botón para activar/desactivar Escucha Atenta en segundo plano (Wake Word "PatitoJar" / "Patito")
        self.btn_passive_listen = QPushButton("📡 Modo Atento", self)
        self.btn_passive_listen.setToolTip("Escucha continua en segundo plano. Decí 'PatitoJar dime...', 'Patito responde...' o 'PatitoJar cállate'.")
        self.btn_passive_listen.setCheckable(True)
        self.btn_passive_listen.setStyleSheet("""
            QPushButton {
                background-color: #0d223a;
                color: #00f3ff;
                border: 1px solid #00f3ff;
                border-radius: 5px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:checked {
                background-color: #00aa66;
                color: #ffffff;
                border: 1px solid #00ff88;
            }
        """)
        self.btn_passive_listen.toggled.connect(self.toggle_passive_listening)
        dialog_header.addWidget(self.btn_passive_listen)

        # Botón para Calibrar Huella de Voz de Gerardo
        self.btn_calibrate_voice = QPushButton("🎯 Calibrar Voz", self)
        self.btn_calibrate_voice.setToolTip("Graba 3.5 segundos de tu voz para calibrar tu firma vocal única (Gerardo).")
        self.btn_calibrate_voice.setStyleSheet("""
            QPushButton {
                background-color: #220044;
                color: #d000ff;
                border: 1px solid #d000ff;
                border-radius: 5px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #d000ff;
                color: #ffffff;
            }
        """)
        self.btn_calibrate_voice.clicked.connect(self.start_voice_calibration)
        dialog_header.addWidget(self.btn_calibrate_voice)

        dialog_layout.addLayout(dialog_header)

        # Chat Log
        self.chat_log = QTextEdit(self)
        self.chat_log.setReadOnly(True)
        self.chat_log.append("<b>PATITOJAR:</b> ¡Hola, genio! Soy PatitoJar, tu pato de goma cibernético y asistente de depuración. ¿Cómo te llamás? Presioná '🎤 Voz' o escribí tu consulta para empezar.\n----------------------------------------")
        dialog_layout.addWidget(self.chat_log)

        # Code Context Input
        self.code_input = QLineEdit(self)
        self.code_input.setPlaceholderText("Contexto de código IDE (opcional: def foo(): ...)")
        self.code_input.setStyleSheet("color: #ffcc00; border-color: #005588;")
        dialog_layout.addWidget(self.code_input)

        # Controls Row (Mic + Prompt Input + Send)
        controls_layout = QHBoxLayout()

        self.btn_mic = QPushButton("🎤 Voz", self)
        self.btn_mic.setStyleSheet("""
            QPushButton {
                background-color: #443300;
                color: #ffcc00;
                border: 1px solid #ffcc00;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ffcc00;
                color: #0a1128;
            }
        """)
        self.btn_mic.clicked.connect(self.toggle_voice_recording)
        controls_layout.addWidget(self.btn_mic)

        self.user_input = QLineEdit(self)
        self.user_input.setPlaceholderText("Pregúntale a PatitoJar...")
        self.user_input.returnPressed.connect(self.send_message)
        controls_layout.addWidget(self.user_input)

        self.btn_send = QPushButton("Enviar", self)
        self.btn_send.clicked.connect(self.send_message)
        controls_layout.addWidget(self.btn_send)

        dialog_layout.addLayout(controls_layout)
        self.main_layout.addWidget(self.dialog_frame)

        # Dialog is expanded by default on launch for immediate visibility
        self.dialog_expanded = True
        self.dialog_frame.setVisible(True)

        # Activar Modo Atento por defecto al iniciar
        self.btn_passive_listen.setChecked(True)

    def toggle_dialog(self):
        if self.is_speaking:
            self.stop_audio_and_listen()
            return
        self.dialog_expanded = not self.dialog_expanded
        self.dialog_frame.setVisible(self.dialog_expanded)
        self.adjustSize()


    # Mouse Drag and Drop Events anywhere on duck widget
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self.old_pos.isNull():
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = QPoint()

    def stop_audio(self):
        """Stops speech synthesis playback immediately."""
        self.speech_queue.clear()
        self.is_speaking = False
        self.last_tts_stop_time = time.time()
        if self.current_tts_worker:
            self.current_tts_worker.stop_audio()
            self.current_tts_worker = None
        self.duck_avatar.set_state("IDLE")
        self.status_label.setText("🔴 Voz detenida por comando.")
        self.status_label.setStyleSheet("color: #ff3366;")

    def start_voice_calibration(self):
        """Graba 3.5 segundos de audio de Gerardo para calibrar la huella de voz."""
        if self.is_speaking:
            self.stop_audio()

        self.btn_calibrate_voice.setText("🔴 Calibrando...")
        self.btn_calibrate_voice.setEnabled(False)
        self.status_label.setText("🔴 Calibrando voz... Decí en voz alta: 'Hola Patito, soy Gerardo'")
        self.status_label.setStyleSheet("color: #d000ff; font-weight: bold;")
        self.duck_avatar.set_state("LISTENING")

        class CalibrationWorker(QThread):
            finished_signal = pyqtSignal(bool, str)

            def run(self):
                try:
                    import sounddevice as sd
                    import wave, io, numpy as np
                    from patito_jar_voice import get_speaker_verifier, SAMPLE_RATE, CHANNELS, DTYPE

                    frames = []
                    def _cb(indata, f, t, s):
                        frames.append(indata.copy())

                    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=1024, dtype=DTYPE, callback=_cb):
                        time.sleep(3.5)

                    if not frames:
                        self.finished_signal.emit(False, "No se capturó audio en el micrófono.")
                        return

                    full_audio = np.concatenate(frames, axis=0)
                    buf = io.BytesIO()
                    with wave.open(buf, 'wb') as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(full_audio.tobytes())

                    verifier = get_speaker_verifier()
                    success = verifier.calibrate_profile(buf.getvalue())
                    if success:
                        self.finished_signal.emit(True, "¡Huella de voz de Gerardo calibrada con éxito!")
                    else:
                        self.finished_signal.emit(False, "No se detectó voz clara para calibrar. Reintentá hablar más fuerte.")
                except Exception as e:
                    self.finished_signal.emit(False, f"Error en calibración: {e}")

        self.calib_worker = CalibrationWorker()
        def _on_calib_done(success, msg):
            self.btn_calibrate_voice.setText("🎯 Calibrar Voz")
            self.btn_calibrate_voice.setEnabled(True)
            self.duck_avatar.set_state("IDLE")
            if success:
                self.status_label.setText(f"✅ {msg}")
                self.status_label.setStyleSheet("color: #00ff88; font-weight: bold;")
                self.chat_log.append(f"<div style='margin: 8px 0; color: #00ff88;'><b>PATITOJAR:</b> ¡Excelente Gerardo! Tu huella de voz ha sido registrada. De ahora en más, solo responderé a tu voz cuando digas 'Patito'.</div>\n")
            else:
                self.status_label.setText(f"⚠️ {msg}")
                self.status_label.setStyleSheet("color: #ff3366;")

        self.calib_worker.finished_signal.connect(_on_calib_done)
        self.register_thread(self.calib_worker)
        self.calib_worker.start()

    def play_speech_queue(self, text_list):
        """Añade oraciones completas a la cola de voz secuencial."""
        if not text_list:
            return
        if isinstance(text_list, str):
            text_list = [text_list]

        for item in text_list:
            clean_item = item.strip()
            if clean_item and clean_item not in self.speech_queue:
                self.speech_queue.append(clean_item)

        if not self.is_speaking:
            self._process_next_speech_item()

    def _process_next_speech_item(self):
        if not self.speech_queue:
            self.is_speaking = False
            self.duck_avatar.set_state("IDLE")
            if self.passive_listen_enabled:
                self.status_label.setText("📡 Escucha atenta activa. Te escucho...")
                self.status_label.setStyleSheet("color: #00ff88; font-weight: bold; font-size: 11px;")
            else:
                self.status_label.setText("Rubber Duck Listo")
                self.status_label.setStyleSheet("color: #ffcc00; font-size: 11px;")
            return

        next_text = self.speech_queue.pop(0)
        self.is_speaking = True
        self.duck_avatar.set_state("SPEAKING")
        self.status_label.setText("Hablando...")
        self.status_label.setStyleSheet("color: #00ff88; font-weight: bold;")

        if self.current_tts_worker:
            self.current_tts_worker.stop_audio()
            self.current_tts_worker = None

        self.current_tts_worker = TTSWorker(next_text)
        self.current_tts_worker.finished_speaking.connect(self._on_speech_chunk_finished)
        self.register_thread(self.current_tts_worker)
        self.current_tts_worker.start()

    def _on_speech_chunk_finished(self):
        self.current_tts_worker = None
        self._process_next_speech_item()

    def stop_audio_and_listen(self):
        """Corta la reproducción actual e inicia la escucha por micrófono para reanudar la conversación."""
        self.speech_queue = []
        self.is_speaking = False
        self.last_tts_stop_time = 0.0
        self.current_tts_worker = None
        if self.current_tts_worker:
            self.current_tts_worker.stop_audio()
            self.current_tts_worker = None
        self.duck_avatar.set_state("IDLE")
        self.status_label.setText("Voz cortada. Escuchando tu respuesta...")
        self.status_label.setStyleSheet("color: #ff3366;")

        if self.is_recording_voice:
            if self.current_stt_worker:
                self.current_stt_worker.stop_recording()
            self.reset_mic_button()

        self.toggle_voice_recording()

    def toggle_voice_recording(self):
        """Alterna reactivamente entre Grabar Voz y Detener/Enviar Voz."""
        if not self.is_recording_voice:
            # --- INICIAR GRABACIÓN ---
            if self.current_tts_worker:
                self.current_tts_worker.stop_audio()
                self.current_tts_worker = None

            if not self.dialog_expanded:
                self.toggle_dialog()

            self.is_recording_voice = True
            self.duck_avatar.set_state("LISTENING")

            # Cambiar botón reactivamente a "📤 Enviar Voz"
            self.btn_mic.setText("📤 Enviar Voz")
            self.btn_mic.setStyleSheet("""
                QPushButton {
                    background-color: #ff0055;
                    color: #ffffff;
                    border: 2px solid #ff3366;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #ff3366;
                    color: #ffffff;
                }
            """)
            self.status_label.setText("🔴 Grabando voz... Presiona 'Enviar Voz' para finalizar.")
            self.status_label.setStyleSheet("color: #ff0055; font-weight: bold;")

            self.current_stt_worker = STTWorker()
            self.current_stt_worker.text_transcribed.connect(self.on_voice_transcribed)
            self.current_stt_worker.error_signal.connect(self.on_voice_error)
            self.register_thread(self.current_stt_worker)
            self.current_stt_worker.start()
        else:
            # --- DETENER Y ENVIAR VOZ ---
            self.btn_mic.setText("⏳ Procesando...")
            self.btn_mic.setEnabled(False)
            self.status_label.setText("Procesando y transcribiendo tu voz...")
            self.status_label.setStyleSheet("color: #ffcc00;")

            if self.current_stt_worker:
                self.current_stt_worker.stop_recording()

    def reset_mic_button(self):
        self.is_recording_voice = False
        self.btn_mic.setEnabled(True)
        self.btn_mic.setText("🎤 Voz")
        self.btn_mic.setStyleSheet("""
            QPushButton {
                background-color: #443300;
                color: #ffcc00;
                border: 1px solid #ffcc00;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ffcc00;
                color: #0a1128;
            }
        """)

    def on_voice_transcribed(self, text):
        self.auto_dialog_loop = False
        self.reset_mic_button()

        # Corregir errores fonéticos o sintácticos comunes de la transcripción
        corrected = correct_transcription_text(text)

        self.status_label.setText("Voz capturada con éxito.")
        self.status_label.setStyleSheet("color: #00ff88;")

        # Expandir automáticamente la ventana de chat si está colapsada para mostrar la pregunta
        if not self.dialog_expanded:
            self.toggle_dialog()

        self.user_input.setText(corrected)
        self.send_message()

    def on_voice_error(self, err_msg):
        self.auto_dialog_loop = False
        self.reset_mic_button()
        self.duck_avatar.set_state("IDLE")
        self.status_label.setText("Error de voz.")
        self.status_label.setStyleSheet("color: #ff3366;")
        self.chat_log.append(f"<span style='color: #ffcc00;'><b>MIC:</b> {err_msg}</span>\n")

    def send_message(self):
        if self.api_in_progress:
            return

        msg = self.user_input.text().strip()
        if not msg:
            return

        code_ctx = self.code_input.text().strip()
        self.user_input.clear()

        if not self.dialog_expanded:
            self.toggle_dialog()

        self.chat_log.append(f"<div style='margin-top: 8px;'><b style='color: #ffcc00;'>TÚ:</b> {html.escape(msg)}</div>")
        if code_ctx:
            code_html = markdown_to_html(f"```python\n{code_ctx}\n```")
            self.chat_log.append(f"<div style='margin-left: 10px; margin-top: 4px;'><i>[Código del IDE]:</i>{code_html}</div>")

        self.api_in_progress = True
        self.stream_buffer = ""

        # ⚡ CERO LATENCIA PERCIBIDA (0ms): Reproducir frase de intro inmediata mientras la IA genera el texto
        import random
        intro_phrase = random.choice(INSTANT_PRE_FILLERS)
        self.play_speech_queue(intro_phrase)

        # ⚡ Consultar Backend en streaming en vivo (<200ms latencia)
        api_worker = ChatAPIWorker(self.backend_url, self.session_id, msg, code_ctx)
        api_worker.token_received.connect(self.on_token_received)
        api_worker.success_signal.connect(self.on_api_success)
        api_worker.error_signal.connect(self.on_api_error)
        self.register_thread(api_worker)
        api_worker.start()

    def on_token_received(self, token):
        """Procesa tokens en vivo de Groq y encola oraciones completas al motor TTS."""
        self.stream_buffer += token
        parts = re.split(r'(?<=[.!?])\s+|\n+', self.stream_buffer)
        if len(parts) > 1:
            completed_sentence = parts[0].strip()
            self.stream_buffer = " ".join(parts[1:])
            if completed_sentence:
                self.play_speech_queue(completed_sentence)

    def on_api_success(self, data):
        self.api_in_progress = False
        self.session_id = data.get("session_id")
        answer = data.get("assistant_message", "")

        formatted_answer = markdown_to_html(answer)
        self.chat_log.append(
            f"<div style='margin: 10px 0;'><b style='color: #00f3ff;'>PATITOJAR:</b><br>{formatted_answer}</div>"
            f"<hr style='border: 0; border-top: 1px solid #1a233a; margin: 10px 0;'>"
        )

        # Enviar cualquier remanente del búfer de texto a la cola de voz
        if self.stream_buffer and self.stream_buffer.strip():
            rem = self.stream_buffer.strip()
            self.stream_buffer = ""
            self.play_speech_queue(rem)

    def on_tts_finished(self):
        self.is_speaking = False
        self.last_tts_stop_time = time.time()
        self.pending_answer = None
        self.duck_avatar.set_state("IDLE")
        if self.passive_listen_enabled:
            self.status_label.setText("📡 Escuchando... Di 'Patito' + consulta [Voz: Gerardo]")
            self.status_label.setStyleSheet("color: #00ff88; font-weight: bold; font-size: 11px;")
        else:
            self.status_label.setText("Rubber Duck Listo")
            self.status_label.setStyleSheet("color: #ffcc00; font-size: 11px;")

    def toggle_passive_listening(self, checked):
        """Activa o desactiva la escucha atenta en segundo plano (wake-word: 'Patito' + consulta)."""
        if checked:
            self.passive_listen_enabled = True
            self.status_label.setText("📡 Escuchando... Di 'Patito' + consulta [Voz: Gerardo]")
            self.status_label.setStyleSheet("color: #00ff88; font-weight: bold;")
            self.start_wake_word_worker()
        else:
            self.passive_listen_enabled = False
            self.status_label.setText("Escucha atenta desactivada.")
            self.status_label.setStyleSheet("color: #ffcc00; font-size: 11px;")
            self.stop_wake_word_worker()

    def start_wake_word_worker(self):
        if self.wake_worker and self.wake_worker.isRunning():
            return
        self.wake_worker = WakeWordWorker(parent_overlay=self)
        self.wake_worker.wake_command_detected.connect(self.on_wake_command)
        self.wake_worker.stop_command_detected.connect(self.on_stop_command)
        self.register_thread(self.wake_worker)
        self.wake_worker.start()

    def stop_wake_word_worker(self):
        if self.wake_worker:
            self.wake_worker.stop_listening()
            self.wake_worker = None

    def on_stop_command(self):
        """Responde a comandos de voz como 'PatitoJar cállate' o 'Patito suficiente'."""
        self.stop_audio()
        self.status_label.setText("🔴 Silenciado por comando de voz.")
        self.status_label.setStyleSheet("color: #ff3366; font-weight: bold;")

    def on_wake_command(self, phrase):
        """Responde a activaciones e interrupciones prioritarias por voz ('PatitoJar dime...', 'tengo otra pregunta...', 'corrijo...')."""
        self.stop_audio()
        self.api_in_progress = False

        if self.is_recording_voice and self.current_stt_worker:
            self.current_stt_worker.stop_recording()
            self.reset_mic_button()

        self.user_input.setText(phrase)
        self.send_message()


    def on_api_error(self, err_msg):
        self.api_in_progress = False
        self.duck_avatar.set_state("IDLE")
        self.chat_log.append(f"<span style='color: #ff3366;'><b>ERROR:</b> {err_msg}</span>\n")
        self.status_label.setText("Fallo de comunicación.")
        self.status_label.setStyleSheet("color: #ff3366;")

    def closeEvent(self, event):
        """Cleanup active threads before window closes to prevent Qt crash."""
        if self.wake_worker:
            self.wake_worker.stop_listening()
        for thread in list(self.active_threads):
            if thread.isRunning():
                if isinstance(thread, TTSWorker):
                    thread.stop_audio()
                thread.quit()
                thread.wait(1000)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = PatitoJarOverlay(backend_url="http://127.0.0.1:8000")
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())
