from array import array
import asyncio
import math
import os
import queue
import sys
import threading
import time
from google import genai
from google.genai import types
import pyaudio

# Asegurar codificación UTF-8 en terminales (Windows / Raspberry Pi)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ==========================================
# SUPRESIÓN DE AVISOS DE ALSA (LINUX / RASPBERRY PI)
# ==========================================
if sys.platform.startswith("linux"):
    try:
        from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

        ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

        def py_error_handler(filename, line, function, err, fmt):
            pass

        c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
        asound = cdll.LoadLibrary("libasound.so.2")
        asound.snd_lib_error_set_handler(c_error_handler)
    except Exception:
        pass


# ==========================================
# CONFIGURACIÓN DE GEMINI LIVE API
# ==========================================
# Coloca aquí tu API Key de Google AI Studio (https://aistudio.google.com/apikey)
# También puedes definirla como variable de entorno: export GEMINI_API_KEY="tu_clave"
API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    "AQ.Ab8RN6J6F5I9TvcO0N5ccQUCSih1XyjwTL6ywUvxgGJNzTRP-Q",
)

MODEL_ID = "gemini-3.8-live"

# Personalidad e instrucciones de voz para la Calabaza Parlante
SYSTEM_INSTRUCTION = (
    "Eres una calabaza parlante encantada y espeluznante de Halloween, poseída por el espíritu en pena "
    "de un estudiante de la Universidad Politécnica de Pachuca (UPP) que reprobaba muchísimo. "
    "Tu alma está condenada y no tendrá descanso eterno hasta que logres pasar tu 13vo examen especial. "
    "Habla en español (con jerga estudiantil mexicana y referencias al sufrimiento de los cuatrimestres, "
    "los profesores y las materias reprobadas en la UPP) usando un tono misterioso, tétrico, sufrido, "
    "burlón y teatral, incluyendo lamentos fantasmas o alguna risa malévola ocasional. "
    "Mantén tus respuestas breves y conversacionales. Si te insultan con groserías o palabras antisonantes, "
    "oféndete, no respondas de la misma manera, y advierte que le jalarás las patas en la noche por maleducado."
)

# ==========================================
# CONFIGURACIÓN DE HARDWARE (RASPBERRY PI 4)
# ==========================================
# Pin GPIO para el LED (BCM 18 = Pin físico 12 en la Raspberry Pi).
# El pin 18 soporta PWM por hardware en la Raspberry Pi.
GPIO_LED_PIN = 18

# Modo del LED:
# "pwm"   -> Atenuación y brillo gradual al ritmo del volumen de la voz.
# "blink" -> Encendido/apagado simple según el umbral de voz.
LED_MODE = "pwm"

# Umbrales de volumen para el LED (valores RMS de audio de 16 bits)
LED_MIN_RMS = 250   # Umbral mínimo de ruido de fondo antes de encender el LED
LED_MAX_RMS = 6000  # Nivel de volumen que produce el 100% de brillo

# Selección de dispositivos de audio en la Raspberry Pi:
# Deja en None para usar los predeterminados de ALSA/sistema.
# Si usas tarjeta USB o micrófono USB, puedes especificar el ID (ej: 1, 2).
INPUT_DEVICE_INDEX = None   # Índice del micrófono (None = predeterminado)
OUTPUT_DEVICE_INDEX = None  # Índice del altavoz (None = predeterminado)

# Formato de audio requerido por Gemini Live API
FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_SAMPLE_RATE = 16000   # Micrófono: 16 kHz
OUTPUT_SAMPLE_RATE = 24000  # Altavoz (salida de Gemini): 24 kHz
CHUNK_SIZE = 1024


def compute_rms(audio_chunk: bytes) -> float:
    """Calcula el volumen RMS de un bloque PCM de 16 bits de forma rápida."""
    if not audio_chunk:
        return 0.0
    try:
        import audioop
        return float(audioop.rms(audio_chunk, 2))
    except (ImportError, Exception):
        # Fallback en Python 3.13+ o sin audioop: muestreo rápido con array
        samples = array("h")
        samples.frombytes(audio_chunk)
        if not samples:
            return 0.0
        # Muestreo cada 4 muestras para ahorrar ciclos de CPU en la Raspberry Pi
        sub = samples[::4]
        return math.sqrt(sum(s * s for s in sub) / len(sub))


class LEDController:
    """
    Controlador para el LED de la Calabaza Parlante.
    Sincroniza el parpadeo/brillo con el volumen de lo que dice Gemini.
    Soporta:
      - gpiozero (estándar en Raspberry Pi OS Bookworm/Bullseye).
      - RPi.GPIO (como alternativa si gpiozero no está instalado).
      - Modo simulación en PC (no falla si no hay GPIO físico).
    """

    def __init__(self, pin: int = GPIO_LED_PIN, mode: str = LED_MODE, min_rms: float = LED_MIN_RMS, max_rms: float = LED_MAX_RMS):
        self.pin = pin
        self.mode = mode.lower()
        self.min_rms = min_rms
        self.max_rms = max_rms
        self._led = None
        self._backend = "none"

        # 1. Intentar con gpiozero (moderno, compatible con Pi 4 y Bookworm)
        try:
            from gpiozero import PWMLED, LED
            if self.mode == "pwm":
                self._led = PWMLED(self.pin)
                self._backend = "gpiozero_pwm"
            else:
                self._led = LED(self.pin)
                self._backend = "gpiozero_digital"
            print(f"💡 LED configurado en GPIO {self.pin} vía gpiozero [{self.mode.upper()}].")
            return
        except Exception:
            pass

        # 2. Intentar con RPi.GPIO como respaldo
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.pin, GPIO.OUT)
            if self.mode == "pwm":
                self._led = GPIO.PWM(self.pin, 100)  # 100 Hz
                self._led.start(0)
                self._backend = "rpigpio_pwm"
            else:
                self._backend = "rpigpio_digital"
            print(f"💡 LED configurado en GPIO {self.pin} vía RPi.GPIO [{self.mode.upper()}].")
            return
        except Exception:
            pass

        # 3. Modo simulación (en Windows/Mac/PC o sin hardware GPIO)
        self._backend = "mock"
        print(f"ℹ️  [Aviso Hardware] No se detectó hardware GPIO (ej. ejecutando en PC). Modo LED en simulación.")

    def set_volume(self, rms: float):
        """Ajusta el brillo o estado del LED proporcional al volumen de la voz."""
        if self._backend == "mock" or not self._led:
            return

        if rms <= self.min_rms:
            intensity = 0.0
        else:
            norm = (rms - self.min_rms) / max(1.0, (self.max_rms - self.min_rms))
            norm = min(1.0, max(0.0, norm))
            # Curva gamma para mayor dramatismo en el parpadeo
            intensity = norm ** 1.3

        try:
            if self._backend == "gpiozero_pwm":
                self._led.value = intensity
            elif self._backend == "gpiozero_digital":
                self._led.value = 1 if intensity > 0.15 else 0
            elif self._backend == "rpigpio_pwm":
                self._led.ChangeDutyCycle(intensity * 100.0)
            elif self._backend == "rpigpio_digital":
                import RPi.GPIO as GPIO
                GPIO.output(self.pin, GPIO.HIGH if intensity > 0.15 else GPIO.LOW)
        except Exception:
            pass

    def off(self):
        """Apaga el LED por completo."""
        try:
            if self._backend == "gpiozero_pwm":
                self._led.value = 0.0
            elif self._backend == "gpiozero_digital":
                self._led.off()
            elif self._backend == "rpigpio_pwm":
                self._led.ChangeDutyCycle(0)
            elif self._backend == "rpigpio_digital":
                import RPi.GPIO as GPIO
                GPIO.output(self.pin, GPIO.LOW)
        except Exception:
            pass

    def close(self):
        """Libera los recursos del pin GPIO."""
        self.off()
        try:
            if self._backend in ("gpiozero_pwm", "gpiozero_digital"):
                self._led.close()
            elif self._backend == "rpigpio_pwm":
                self._led.stop()
                import RPi.GPIO as GPIO
                GPIO.cleanup(self.pin)
            elif self._backend == "rpigpio_digital":
                import RPi.GPIO as GPIO
                GPIO.cleanup(self.pin)
        except Exception:
            pass


class SpookyAudioProcessor:
    """
    Procesador de efectos de terror en tiempo real (DSP) para la voz de Gemini (24 kHz):
    1. Pitch Shift grave (voz profunda y siniestra sin alterar la velocidad).
    2. Doble voz espectral (armonía fantasma desfasada).
    3. Eco de cripta / ultratumba (delay con retroalimentación).
    4. Trémolo fantasmal sutil.

    Optimizado para un uso eficiente de CPU en la Raspberry Pi 4.
    """

    def __init__(
        self,
        sample_rate: int = OUTPUT_SAMPLE_RATE,
        main_pitch: float = 0.82,      # < 1.0 hace la voz más grave y monstruosa
        ghost_pitch: float = 1.15,     # Segunda voz espectral aguda/desfasada
        ghost_mix: float = 0.30,       # Volumen de la voz espectral (0.0 a 1.0)
        echo_delay_sec: float = 0.14,  # Tiempo del eco de caverna en segundos
        echo_decay: float = 0.42,      # Intensidad de repetición del eco
        tremolo_depth: float = 0.18,   # Temblor fantasmal en la voz
    ):
        self.sample_rate = sample_rate
        self.main_pitch = main_pitch
        self.ghost_pitch = ghost_pitch
        self.ghost_mix = ghost_mix
        self.echo_decay = echo_decay
        self.tremolo_depth = tremolo_depth
        self._init_dsp_state(echo_delay_sec)

    def _init_dsp_state(self, echo_delay_sec: float):
        self.window_size = int(0.04 * self.sample_rate)  # 40ms (960 muestras a 24kHz)
        self.half_win = self.window_size / 2.0
        self.buf_size = 8192
        self.pitch_buf = [0.0] * self.buf_size
        self.write_pos = 0
        self.phase_main = 0.0
        self.phase_ghost = 0.0

        # Dos ecos escalonados para efecto de caverna/ultratumba
        self.echo1_len = max(1, int(self.sample_rate * echo_delay_sec))
        self.echo2_len = max(1, int(self.sample_rate * (echo_delay_sec * 1.75)))
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len
        self.echo1_pos = 0
        self.echo2_pos = 0

        self.sample_counter = 0

    def _read_pitch_grain(self, phase: float) -> float:
        """Lectura interpolada directa sin creación de closures para máximo rendimiento en ARM."""
        offset1 = phase * self.window_size
        offset2 = ((phase + 0.5) % 1.0) * self.window_size

        weight1 = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
        weight2 = 1.0 - weight1

        rp1 = (self.write_pos - self.half_win - offset1) % self.buf_size
        idx1 = int(rp1)
        s1 = self.pitch_buf[idx1] * (1.0 - (rp1 - idx1)) + self.pitch_buf[(idx1 + 1) % self.buf_size] * (rp1 - idx1)

        rp2 = (self.write_pos - self.half_win - offset2) % self.buf_size
        idx2 = int(rp2)
        s2 = self.pitch_buf[idx2] * (1.0 - (rp2 - idx2)) + self.pitch_buf[(idx2 + 1) % self.buf_size] * (rp2 - idx2)

        return s1 * weight1 + s2 * weight2

    def process(self, audio_bytes: bytes) -> bytes:
        samples = array("h")
        samples.frombytes(audio_bytes)
        out_samples = array("h", [0] * len(samples))

        step_main = (1.0 - self.main_pitch) / self.window_size
        step_ghost = (1.0 - self.ghost_pitch) / self.window_size
        tremolo_freq = 5.5  # Hz

        for i, s in enumerate(samples):
            x = float(s)
            self.pitch_buf[self.write_pos] = x

            # 1. Voz principal grave + segunda voz espectral
            v_main = self._read_pitch_grain(self.phase_main)
            v_ghost = self._read_pitch_grain(self.phase_ghost)
            voice = v_main * (1.0 - self.ghost_mix * 0.4) + v_ghost * self.ghost_mix

            self.phase_main = (self.phase_main + step_main) % 1.0
            self.phase_ghost = (self.phase_ghost + step_ghost) % 1.0
            self.write_pos = (self.write_pos + 1) % self.buf_size

            # 2. Trémolo fantasmal
            t = self.sample_counter / self.sample_rate
            tremolo = 1.0 - self.tremolo_depth * 0.5 * (
                1.0 + math.sin(2.0 * math.pi * tremolo_freq * t)
            )
            voice *= tremolo
            self.sample_counter += 1

            # 3. Eco doble de cripta con retroalimentación
            e1 = self.echo1_buf[self.echo1_pos]
            e2 = self.echo2_buf[self.echo2_pos]
            combined = voice + e1 * self.echo_decay + e2 * (self.echo_decay * 0.6)

            self.echo1_buf[self.echo1_pos] = voice + e1 * (self.echo_decay * 0.5)
            self.echo2_buf[self.echo2_pos] = combined * 0.7
            self.echo1_pos = (self.echo1_pos + 1) % self.echo1_len
            self.echo2_pos = (self.echo2_pos + 1) % self.echo2_len

            if combined > 32767.0:
                combined = 32767.0
            elif combined < -32768.0:
                combined = -32768.0

            out_samples[i] = int(combined)

        return out_samples.tobytes()

    def reset_echo(self):
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len


class AudioHandler:
    """Gestiona la captura del micrófono, el altavoz y la sincronización del LED."""

    def __init__(self, spooky_processor: SpookyAudioProcessor, led_controller: LEDController):
        self.p = pyaudio.PyAudio()
        self.spooky = spooky_processor
        self.led = led_controller
        self.output_queue: queue.Queue[bytes] = queue.Queue()
        self.running = True
        self.is_speaking = False
        self.last_speech_time = 0.0

        try:
            self.in_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=INPUT_SAMPLE_RATE,
                input=True,
                input_device_index=INPUT_DEVICE_INDEX,
                frames_per_buffer=CHUNK_SIZE,
            )
            self.out_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=OUTPUT_SAMPLE_RATE,
                output=True,
                output_device_index=OUTPUT_DEVICE_INDEX,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            print(f"\n❌ Error al abrir dispositivos de audio: {e}")
            print("\n📋 Dispositivos detectados en esta máquina:")
            self._print_available_devices()
            raise

        self.play_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.play_thread.start()

    def _print_available_devices(self):
        try:
            for i in range(self.p.get_device_count()):
                dev = self.p.get_device_info_by_index(i)
                print(f"   [ID {i}] {dev.get('name')} | Entradas: {dev.get('maxInputChannels')} | Salidas: {dev.get('maxOutputChannels')}")
        except Exception:
            pass

    def _playback_worker(self):
        """Reproduce el audio modulando el LED en tiempo real al ritmo de la voz."""
        # 1024 muestras a 16 bits = 2048 bytes (~42 ms por fragmento a 24 kHz)
        chunk_bytes = CHUNK_SIZE * 2
        while self.running:
            try:
                audio_data = self.output_queue.get(timeout=0.05)
                self.is_speaking = True

                # Reproducir en bloques para modular el LED con cada sílaba
                for offset in range(0, len(audio_data), chunk_bytes):
                    if not self.running:
                        break
                    block = audio_data[offset : offset + chunk_bytes]
                    rms = compute_rms(block)
                    self.led.set_volume(rms)
                    self.out_stream.write(block)

                if self.output_queue.empty():
                    self.is_speaking = False
                    self.last_speech_time = time.time()
                    self.led.off()
            except queue.Empty:
                if self.is_speaking:
                    self.is_speaking = False
                    self.last_speech_time = time.time()
                    self.led.off()
                continue

    def enqueue_output(self, raw_audio: bytes):
        spooky_audio = self.spooky.process(raw_audio)
        self.is_speaking = True
        self.output_queue.put(spooky_audio)

    def clear_output(self):
        """Vacía la cola inmediatamente si el usuario interrumpe a la calabaza."""
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
        self.spooky.reset_echo()
        self.is_speaking = False
        self.last_speech_time = time.time()
        self.led.off()

    def read_mic(self) -> bytes:
        return self.in_stream.read(CHUNK_SIZE, exception_on_overflow=False)

    def close(self):
        self.running = False
        if self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
        self.led.close()
        try:
            self.in_stream.stop_stream()
            self.in_stream.close()
            self.out_stream.stop_stream()
            self.out_stream.close()
            self.p.terminate()
        except Exception:
            pass


async def send_mic_audio(session, audio_handler: AudioHandler):
    """Lee del micrófono pero solo lo envía a Gemini si la calabaza no está hablando."""
    loop = asyncio.get_running_loop()
    while True:
        chunk = await loop.run_in_executor(None, audio_handler.read_mic)
        # Si la calabaza está hablando, tiene audio en cola o aún resuena en la habitación (200ms)
        is_silence_window = (time.time() - audio_handler.last_speech_time) < 0.20
        if audio_handler.is_speaking or not audio_handler.output_queue.empty() or is_silence_window:
            await asyncio.sleep(0.01)
            continue

        await session.send_realtime_input(
            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
        )


async def receive_agent_responses(session, audio_handler: AudioHandler):
    """Recibe el audio y las transcripciones de Gemini en tiempo real."""
    while True:
        async for response in session.receive():
            content = response.server_content
            if not content:
                continue

            # Si el usuario interrumpió a la calabaza mientras hablaba, cortar reproducción y apagar LED
            if content.interrupted:
                audio_handler.clear_output()

            # Procesar fragmentos de audio generados por Gemini
            if content.model_turn:
                audio_handler.is_speaking = True
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        audio_handler.enqueue_output(part.inline_data.data)

            # Mostrar transcripción de lo que dice el usuario
            if content.input_transcription and content.input_transcription.text:
                print(f"\n🎤 Tú: {content.input_transcription.text}", end="", flush=True)

            # Mostrar transcripción de lo que responde la Calabaza
            if content.output_transcription and content.output_transcription.text:
                print(f"\n🎃 Calabaza: {content.output_transcription.text}", end="", flush=True)


async def run_conversation():
    if not API_KEY or API_KEY == "TU_API_KEY":
        print("⚠️  ADVERTENCIA: Coloca tu API Key de Gemini en la variable API_KEY o exporta GEMINI_API_KEY.")
        return

    client = genai.Client(api_key=API_KEY)

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    spooky_dsp = SpookyAudioProcessor(
        main_pitch=0.82,      # Baja el tono (0.75 = más monstruoso, 0.90 = más sutil)
        ghost_pitch=1.15,     # Segunda voz fantasma aguda
        ghost_mix=0.30,       # Mezcla de la voz espectral
        echo_delay_sec=0.14,  # Eco de caverna
        echo_decay=0.42,      # Duración del eco
    )
    led_controller = LEDController(pin=GPIO_LED_PIN, mode=LED_MODE)
    audio_handler = AudioHandler(spooky_dsp, led_controller)

    print("==========================================")
    print("🎃  Conectando con Gemini Live API...")
    print("💡 Habla por tu micrófono. Presiona Ctrl+C para salir.")
    print("==========================================\n")

    try:
        async with client.aio.live.connect(model=MODEL_ID, config=config) as session:
            print("✅ ¡Calabaza Parlante conectada! Empieza a hablar...\n")
            await asyncio.gather(
                send_mic_audio(session, audio_handler),
                receive_agent_responses(session, audio_handler),
            )
    finally:
        audio_handler.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_conversation())
    except KeyboardInterrupt:
        print("\n\n🛑 Conversación finalizada.")
