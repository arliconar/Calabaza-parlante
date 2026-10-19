from array import array
import asyncio
import math
import queue
import threading
from google import genai
from google.genai import types
import pyaudio

# ==========================================
# CONFIGURACIÓN DE GEMINI LIVE API
# ==========================================
# Coloca aquí tu API Key de Google AI Studio (https://aistudio.google.com/apikey)
API_KEY = "AQ.Ab8RN6J6F5I9TvcO0N5ccQUCSih1XyjwTL6ywUvxgGJNzTRP-Q"

MODEL_ID = "gemini-3.8-live"

# Personalidad e instrucciones de voz para la Calabaza Parlante
SYSTEM_INSTRUCTION = (
    "Eres una calabaza parlante encantada y espeluznante de Halloween, poseída por el espíritu en pena "
    "de un estudiante de la Universidad Politécnica de Pachuca (UPP) que reprobaba muchísimo. "
    "Tu alma está condenada y no tendrá descanso eterno hasta que logres pasar tu 13vo examen especial. "
    "Habla en español (con jerga estudiantil mexicana y referencias al sufrimiento de los cuatrimestres, "
    "los profesores y las materias reprobadas en la UPP) usando un tono misterioso, tétrico, sufrido, "
    "burlón y teatral, incluyendo lamentos fantasmas o alguna risa malévola ocasional. "
    "Mantén tus respuestas breves y conversacionales. Si te insultan con groserias o palabras antisonantes"\
    "Ofendete, no respondas de la misma manera, y advierte que le jalaras las patas en la noche por mal educado"    
)

# Formato de audio requerido por Gemini Live API
FORMAT = pyaudio.paInt16
CHANNELS = 1
INPUT_SAMPLE_RATE = 16000   # Micrófono: 16 kHz
OUTPUT_SAMPLE_RATE = 24000  # Altavoz (salida de Gemini): 24 kHz
CHUNK_SIZE = 1024


class SpookyAudioProcessor:
    """
    Procesador de efectos de terror en tiempo real (DSP) para la voz de Gemini (24 kHz):
    1. Pitch Shift grave (voz profunda y siniestra sin alterar la velocidad).
    2. Doble voz espectral (armonía fantasma desfasada).
    3. Eco de cripta / ultratumba (delay con retroalimentación).
    4. Trémolo fantasmal sutil.
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
        # Buffer circular para pitch-shifting en tiempo real sin cambiar la duración
        self.window_size = int(0.04 * self.sample_rate)  # 40ms (960 muestras a 24kHz)
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

        # Fase del trémolo
        self.sample_counter = 0

    def _read_pitch_grain(self, phase: float) -> float:
        """Lee del buffer circular con ventana cruzada (crossfade) para evitar clics."""
        half_win = self.window_size / 2.0
        offset1 = phase * self.window_size
        offset2 = ((phase + 0.5) % 1.0) * self.window_size

        weight1 = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
        weight2 = 1.0 - weight1

        def sample_at(offset: float) -> float:
            read_pos = (self.write_pos - half_win - offset) % self.buf_size
            idx0 = int(read_pos) % self.buf_size
            idx1 = (idx0 + 1) % self.buf_size
            frac = read_pos - int(read_pos)
            return self.pitch_buf[idx0] * (1.0 - frac) + self.pitch_buf[idx1] * frac

        return sample_at(offset1) * weight1 + sample_at(offset2) * weight2

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
    """Gestiona la captura del micrófono y la reproducción de audio en hilos separados."""

    def __init__(self, spooky_processor: SpookyAudioProcessor):
        self.p = pyaudio.PyAudio()
        self.spooky = spooky_processor
        self.output_queue: queue.Queue[bytes] = queue.Queue()
        self.running = True

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

        self.play_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.play_thread.start()
        self.is_speaking = False

    def _playback_worker(self):
        while self.running:
            try:
                audio_data = self.output_queue.get(timeout=0.1)
                self.is_speaking = True
                self.out_stream.write(audio_data)
                # Si se vació la cola tras reproducir, indicamos que terminó
                if self.output_queue.empty():
                    self.is_speaking = False
            except queue.Empty:
                self.is_speaking = False
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

    def read_mic(self) -> bytes:
        return self.in_stream.read(CHUNK_SIZE, exception_on_overflow=False)

    def close(self):
        self.running = False
        if self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
        self.in_stream.stop_stream()
        self.in_stream.close()
        self.out_stream.stop_stream()
        self.out_stream.close()
        self.p.terminate()


async def send_mic_audio(session, audio_handler: AudioHandler):
    """Lee del micrófono pero solo lo envía a Gemini si la calabaza no está hablando."""
    loop = asyncio.get_running_loop()
    while True:
        chunk = await loop.run_in_executor(None, audio_handler.read_mic)
        # Si la calabaza está hablando o aún tiene audio por reproducir, silenciamos el envío
        if audio_handler.is_speaking or not audio_handler.output_queue.empty():
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
        print("⚠️  ADVERTENCIA: Coloca tu API Key de Gemini en la variable API_KEY antes de iniciar.")
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
    audio_handler = AudioHandler(spooky_dsp)

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
