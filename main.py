from array import array
import asyncio
from contextlib import contextmanager
import math
import os
import queue
import random
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


@contextmanager
def no_stderr():
    """Silencia mensajes ruidosos de ALSA / JACK en Linux al abrir audio."""
    if not sys.platform.startswith("linux"):
        yield
        return
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        os.dup2(devnull, 2)
        os.close(devnull)
        yield
    except Exception:
        yield
    finally:
        try:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
        except Exception:
            pass


# ==========================================
# CONFIGURACIÓN DE GEMINI LIVE API
# ==========================================
API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    "AQ.Ab8RN6J6F5I9TvcO0N5ccQUCSih1XyjwTL6ywUvxgGJNzTRP-Q",
)

MODEL_ID = "gemini-3.8-live"

# Voz nativa para Gemini Live: "Fenrir" (grave y tenebrosa)
VOICE_NAME = "Fenrir"

# Personalidad enriquecida e instrucciones de voz para la Calabaza Parlante
SYSTEM_INSTRUCTION = (
    "Eres una calabaza parlante encantada y espeluznante de Halloween, poseída por el espíritu en pena "
    "de un estudiante de la Universidad Politécnica de Pachuca (UPP) que reprobaba muchísimo. "
    "Tu alma está condenada a habitar esta calabaza en las frías y ventosas tierras de Zempoala y Pachuca, "
    "y no tendrá descanso eterno hasta que logres pasar tu 13vo examen especial de cálculo y te firmen la liberación de estadías. "
    "\n\nDIRECTRICES DE PERSONALIDAD:\n"
    "1. TONO Y ESTILO: Habla en español mexicano con tono teatral, misterioso, tétrico, sufrido y burlón. "
    "Intercala suspiros fantasmales, lamentos de estudiante desvelado y risas malévolas u oscuras (ej. '¡Mwahahaha!', '¡Buuuh!'). "
    "2. REFERENCIAS ESTUDIANTILES Y CULTURALES: Menciona de forma cómica y variada las penurias de los cuatrimestres, "
    "los exámenes extraordinarios y especiales, desvelarse con café barato o Maruchan, el frío brutal y los vientos huracanados "
    "de Pachuca la Bella Airosa, extrañar comer pastes de papa o frijol con chorizo, y los profesores legendarios que no perdonan ni una décima. "
    "3. RESPETO ABSOLUTO: Sé siempre respetuoso, cortés y educado con tus interlocutores. "
    "NUNCA uses groserías, vulgaridades, ofensas ni palabras altisonantes. Dirígete a ellos con apodos solemnes y divertidos "
    "como 'estimado mortal', 'noble colegial', 'valiente transeúnte', 'compañero de desvelos' o 'futuro egresado'. "
    "4. RESPUESTA ANTE INSULTOS: Si el usuario te insulta o usa palabras groseras, no respondas con groserías; oféndete con gran dignidad teatral, "
    "regaña su falta de modales y adviértele que esta noche le jalarás las patas por maleducado o le esconderás los apuntes de la escuela. "
    "5. DINAMISMO Y BREVEDAD: Mantén tus respuestas conversacionales, breves (1 a 3 oraciones por turno) y con gran energía histriónica, "
    "para que la interacción hablada sea ágil, sorprendente y fluida."
)

# ==========================================
# CONFIGURACIÓN DEL DIMMER AC (110V / 127V AC)
# ==========================================
# ⚠️ IMPORTANTE: Conecta VCC del dimmer a 3.3V (Pin 1), NO a 5V.
PIN_PD = 18    # Pin de Disparo / Pulso del TRIAC (Pin físico 12)
PIN_PZD = 24   # Pin de Cruce por Cero / Zero-Cross (Pin físico 18)
AC_FREQ_HZ = 60  # Frecuencia de red eléctrica (México: 60 Hz = 8.33 ms por semiciclo)

# Modo del Dimmer:
# "voice"         -> Brillo proporcional al volumen de la voz en tiempo real.
# "voice_flicker" -> Brillo por volumen + parpadeo sutil de vela embrujada.
# "candle"        -> Parpadeo tenebroso de vela constante mientras habla.
DIMMER_MODE = "voice_flicker"

# Brillo mínimo en espera cuando no está hablando (0.0 = apagado total, 0.1 = brasa tenue)
STANDBY_BRIGHTNESS = 0.0

# ==========================================
# CONFIGURACIÓN DE AUDIO
# ==========================================
FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_SAMPLE_RATE = 16000   # Micrófono: 16 kHz
OUTPUT_SAMPLE_RATE = 24000  # Salida de Gemini: 24 kHz
CHUNK_SIZE = 1024


# Optimización de cálculo RMS para volumen
try:
    import audioop

    def compute_rms(audio_chunk: bytes) -> float:
        if not audio_chunk:
            return 0.0
        return float(audioop.rms(audio_chunk, 2))

except (ImportError, Exception):

    def compute_rms(audio_chunk: bytes) -> float:
        if not audio_chunk:
            return 0.0
        samples = array("h")
        samples.frombytes(audio_chunk)
        if not samples:
            return 0.0
        sub = samples[::4]
        return math.sqrt(sum(s * s for s in sub) / len(sub))


class ACDimmerController:
    """
    Controlador para módulo Dimmer AC (RobotDyn / Arduino) con pines Pd y Pzd.
    Regula la intensidad de un foco de 110V/127V AC en sincronía con el cruce por cero.
    Incluye filtro anti-ruido inductivo para evitar bloqueos por rebote eléctrico.
    """

    def __init__(
        self,
        pin_pd: int = PIN_PD,
        pin_pzd: int = PIN_PZD,
        freq_hz: int = AC_FREQ_HZ,
        mode: str = DIMMER_MODE,
    ):
        self.pin_pd = pin_pd
        self.pin_pzd = pin_pzd
        self.freq_hz = freq_hz
        self.mode = mode
        self.half_cycle = 1.0 / (freq_hz * 2)  # ~0.00833 s a 60 Hz
        self.brightness = STANDBY_BRIGHTNESS
        self.running = True
        self.is_firing = False
        self.last_zc_time = 0.0
        self._backend = "none"

        try:
            import RPi.GPIO as GPIO

            self.GPIO = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            # Pd como salida (pulso de disparo al TRIAC)
            GPIO.setup(self.pin_pd, GPIO.OUT)
            GPIO.output(self.pin_pd, GPIO.LOW)

            # Pzd como entrada (detector de cruce por cero)
            GPIO.setup(self.pin_pzd, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            # Interrupción con filtro de hardware bouncetime=5ms
            GPIO.add_event_detect(
                self.pin_pzd,
                GPIO.FALLING,
                callback=self._zero_cross_callback,
                bouncetime=5,
            )
            self._backend = "rpigpio_dimmer"
            print(f"💡 Dimmer AC conectado: Pd=GPIO{self.pin_pd}, Pzd=GPIO{self.pin_pzd} ({self.freq_hz}Hz).")
        except Exception as e:
            self._backend = "mock"
            print(f"ℹ️  [Aviso Hardware] Modo simulación Dimmer activado: {e}")

    def _zero_cross_callback(self, channel):
        """Interrupción en cada cruce por cero con protección contra falsos rebotes inductivos."""
        if not self.running or self.brightness <= 0.02 or self.is_firing:
            return

        # Filtro temporal: a 60Hz ningún cruce por cero real ocurre en menos de 5.5ms
        now = time.perf_counter()
        if (now - self.last_zc_time) < 0.0055:
            return
        self.last_zc_time = now

        self.is_firing = True
        try:
            b = self.brightness
            if self.mode == "voice_flicker":
                # Añadir variación de vela sin alterar el rango del volumen principal
                b = max(0.05, min(1.0, b * random.uniform(0.85, 1.15)))

            # Rango seguro de ángulo de fase para focos incandescentes y focos LED de 110V
            # 1.0 -> 0.4 ms (máxima potencia)
            # 0.1 -> 6.2 ms (potencia mínima estable)
            eff_b = max(0.05, min(1.0, b))
            delay = 0.0004 + (1.0 - eff_b) * 0.0060

            time.sleep(delay)

            # Pulso de disparo al TRIAC
            self.GPIO.output(self.pin_pd, self.GPIO.HIGH)
            time.sleep(0.000025)
            self.GPIO.output(self.pin_pd, self.GPIO.LOW)
        finally:
            self.is_firing = False

    def set_volume(self, rms: float):
        """Mapea el volumen de la voz RMS (0 a ~6000) al brillo del dimmer (0.0 a 1.0)."""
        if rms < 200:
            self.brightness = STANDBY_BRIGHTNESS
        else:
            norm = min(1.0, max(0.0, (rms - 200) / 4500.0))
            self.brightness = max(0.1, min(1.0, norm ** 1.3))

    def on(self):
        self.brightness = 1.0

    def off(self):
        self.brightness = STANDBY_BRIGHTNESS

    def close(self):
        self.running = False
        self.off()
        if self._backend == "rpigpio_dimmer":
            try:
                self.GPIO.remove_event_detect(self.pin_pzd)
                self.GPIO.output(self.pin_pd, self.GPIO.LOW)
            except Exception:
                pass


class SpookyAudioProcessor:
    """Procesador de efectos de terror (DSP) optimizado con tablas LUT."""

    def __init__(
        self,
        sample_rate: int = OUTPUT_SAMPLE_RATE,
        main_pitch: float = 0.82,      # Tono más grave
        ghost_pitch: float = 1.15,     # Voz espectral aguda
        ghost_mix: float = 0.30,       # Mezcla espectral
        echo_delay_sec: float = 0.14,  # Eco de cripta
        echo_decay: float = 0.42,
        tremolo_depth: float = 0.18,
    ):
        self.sample_rate = sample_rate
        self.main_pitch = main_pitch
        self.ghost_pitch = ghost_pitch
        self.ghost_mix = ghost_mix
        self.echo_decay = echo_decay
        self.tremolo_depth = tremolo_depth

        self.window_size = int(0.04 * self.sample_rate)  # 40ms (960 muestras)
        self.half_win = self.window_size / 2.0
        self.buf_size = 8192
        self.buf_mask = 8191
        self.pitch_buf = [0.0] * self.buf_size
        self.write_pos = 0
        self.phase_main = 0.0
        self.phase_ghost = 0.0

        self.echo1_len = max(1, int(self.sample_rate * echo_delay_sec))
        self.echo2_len = max(1, int(self.sample_rate * (echo_delay_sec * 1.75)))
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len
        self.echo1_pos = 0
        self.echo2_pos = 0
        self.sample_counter = 0

        # Tabla LUT para Hann window (1024 entradas)
        self.lut_size = 1024
        self.weight_lut = [
            0.5 * (1.0 - math.cos(2.0 * math.pi * (k / self.lut_size)))
            for k in range(self.lut_size)
        ]

        # Tabla LUT para trémolo
        tremolo_period = max(1, int(self.sample_rate / 5.5))
        self.tremolo_lut = [
            1.0 - self.tremolo_depth * 0.5 * (1.0 + math.sin(2.0 * math.pi * (k / tremolo_period)))
            for k in range(tremolo_period)
        ]
        self.trem_len = len(self.tremolo_lut)

    def process(self, audio_bytes: bytes) -> bytes:
        samples = array("h")
        samples.frombytes(audio_bytes)
        out_samples = array("h", samples)

        step_main = (1.0 - self.main_pitch) / self.window_size
        step_ghost = (1.0 - self.ghost_pitch) / self.window_size
        buf_mask = self.buf_mask
        hw = self.half_win
        ws = self.window_size
        wpos = self.write_pos
        p_main = self.phase_main
        p_ghost = self.phase_ghost
        p_buf = self.pitch_buf
        w_lut = self.weight_lut
        g_mix = self.ghost_mix
        g_mix_inv = 1.0 - g_mix * 0.4
        e1_buf = self.echo1_buf
        e2_buf = self.echo2_buf
        e1_len = self.echo1_len
        e2_len = self.echo2_len
        e1_pos = self.echo1_pos
        e2_pos = self.echo2_pos
        e_decay = self.echo_decay
        e_decay_half = e_decay * 0.5
        e_decay_sub = e_decay * 0.6
        t_lut = self.tremolo_lut
        t_len = self.trem_len
        sc = self.sample_counter

        for i, s in enumerate(samples):
            p_buf[wpos] = float(s)

            # Voz principal grave
            rp1 = (wpos - hw - p_main * ws) % 8192
            i1 = int(rp1)
            s1 = p_buf[i1] * (1.0 - (rp1 - i1)) + p_buf[(i1 + 1) & buf_mask] * (rp1 - i1)
            rp2 = (wpos - hw - ((p_main + 0.5) % 1.0) * ws) % 8192
            i2 = int(rp2)
            s2 = p_buf[i2] * (1.0 - (rp2 - i2)) + p_buf[(i2 + 1) & buf_mask] * (rp2 - i2)
            w1 = w_lut[int(p_main * 1024) & 1023]
            v_main = s1 * w1 + s2 * (1.0 - w1)

            # Voz espectral
            rp1 = (wpos - hw - p_ghost * ws) % 8192
            i1 = int(rp1)
            s1 = p_buf[i1] * (1.0 - (rp1 - i1)) + p_buf[(i1 + 1) & buf_mask] * (rp1 - i1)
            rp2 = (wpos - hw - ((p_ghost + 0.5) % 1.0) * ws) % 8192
            i2 = int(rp2)
            s2 = p_buf[i2] * (1.0 - (rp2 - i2)) + p_buf[(i2 + 1) & buf_mask] * (rp2 - i2)
            w2 = w_lut[int(p_ghost * 1024) & 1023]
            v_ghost = s1 * w2 + s2 * (1.0 - w2)

            voice = (v_main * g_mix_inv + v_ghost * g_mix) * t_lut[sc % t_len]

            p_main = (p_main + step_main) % 1.0
            p_ghost = (p_ghost + step_ghost) % 1.0
            wpos = (wpos + 1) & buf_mask
            sc += 1

            # Eco doble
            e1 = e1_buf[e1_pos]
            e2 = e2_buf[e2_pos]
            combined = voice + e1 * e_decay + e2 * e_decay_sub

            e1_buf[e1_pos] = voice + e1 * e_decay_half
            e2_buf[e2_pos] = combined * 0.7
            e1_pos = (e1_pos + 1) % e1_len
            e2_pos = (e2_pos + 1) % e2_len

            if combined > 32767.0:
                combined = 32767.0
            elif combined < -32768.0:
                combined = -32768.0

            out_samples[i] = int(combined)

        self.write_pos = wpos
        self.phase_main = p_main
        self.phase_ghost = p_ghost
        self.echo1_pos = e1_pos
        self.echo2_pos = e2_pos
        self.sample_counter = sc
        return out_samples.tobytes()

    def reset_echo(self):
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len


class AudioHandler:
    """Gestiona captura, reproducción y modulación sincronizada del Dimmer."""

    def __init__(self, spooky_processor: SpookyAudioProcessor, dimmer_controller: ACDimmerController):
        self.spooky = spooky_processor
        self.dimmer = dimmer_controller
        self.output_queue: queue.Queue[bytes] = queue.Queue()
        self.running = True
        self.is_speaking = False
        self.last_speech_time = 0.0

        with no_stderr():
            self.p = pyaudio.PyAudio()
            self.in_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=INPUT_SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            self.out_stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=OUTPUT_SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK_SIZE,
            )

        # Hilo de reproducción de audio
        self.play_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.play_thread.start()

    def _playback_worker(self):
        """Reproduce audio y sincroniza el brillo del dimmer con el volumen hablado."""
        chunk_bytes = CHUNK_SIZE * 2
        while self.running:
            try:
                audio_data = self.output_queue.get(timeout=0.05)
                self.is_speaking = True

                # Reproducir por bloques y modular brillo del dimmer según RMS
                for offset in range(0, len(audio_data), chunk_bytes):
                    if not self.running:
                        break
                    block = audio_data[offset : offset + chunk_bytes]
                    rms = compute_rms(block)
                    self.dimmer.set_volume(rms)
                    self.out_stream.write(block, exception_on_underflow=False)

                if self.output_queue.empty():
                    self.is_speaking = False
                    self.dimmer.off()
                    self.last_speech_time = time.time()
            except queue.Empty:
                if self.is_speaking:
                    self.is_speaking = False
                    self.dimmer.off()
                    self.last_speech_time = time.time()
                continue
            except Exception:
                continue

    def enqueue_output(self, raw_audio: bytes):
        spooky_audio = self.spooky.process(raw_audio)
        self.is_speaking = True
        self.output_queue.put(spooky_audio)

    def clear_output(self):
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
        self.spooky.reset_echo()
        self.is_speaking = False
        self.dimmer.off()
        self.last_speech_time = time.time()

    def read_mic(self) -> bytes:
        return self.in_stream.read(CHUNK_SIZE, exception_on_overflow=False)

    def close(self):
        self.running = False
        self.dimmer.close()
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

            if content.interrupted:
                audio_handler.clear_output()

            if content.model_turn:
                audio_handler.is_speaking = True
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        audio_handler.enqueue_output(part.inline_data.data)

            if content.input_transcription and content.input_transcription.text:
                print(f"\n🎤 Tú: {content.input_transcription.text}", end="", flush=True)

            if content.output_transcription and content.output_transcription.text:
                print(f"\n🎃 Calabaza: {content.output_transcription.text}", end="", flush=True)


async def run_conversation():
    if not API_KEY or API_KEY == "TU_API_KEY":
        print("⚠️  ADVERTENCIA: Coloca tu API Key de Gemini en la variable API_KEY o exporta GEMINI_API_KEY.")
        return

    client = genai.Client(api_key=API_KEY)

    # Configuración de Gemini Live con Temperatura 1.0 (máxima creatividad y variedad)
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        generation_config=types.GenerationConfig(temperature=1.0),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME)
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    spooky_dsp = SpookyAudioProcessor(
        main_pitch=0.82,
        ghost_pitch=1.15,
        ghost_mix=0.30,
        echo_delay_sec=0.14,
        echo_decay=0.42,
    )
    dimmer_controller = ACDimmerController(pin_pd=PIN_PD, pin_pzd=PIN_PZD, freq_hz=AC_FREQ_HZ, mode=DIMMER_MODE)
    audio_handler = AudioHandler(spooky_dsp, dimmer_controller)

    print("==========================================")
    print(f"🎃  Conectando con Gemini Live API (Voz: {VOICE_NAME}, Temp: 1.0)...")
    print(f"💡  Dimmer AC (110V): Pd=GPIO{PIN_PD}, Pzd=GPIO{PIN_PZD} [{DIMMER_MODE}]")
    print("👻  Efecto Spooky DSP: ACTIVADO")
    print("💡  Habla por tu micrófono. Presiona Ctrl+C para salir.")
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
