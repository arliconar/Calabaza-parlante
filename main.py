from array import array
import math
import signal
import sys
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

# ==========================================
# CONFIGURACIÓN
# ==========================================
# Reemplaza "TU_API_KEY" con tu clave real de ElevenLabs
API_KEY = "sk_d58f77191058777cefef3efad7920cbcf8cd7ea8c45ddab2"
AGENT_ID = "agent_2901m52md093f9qraqjme2yj785q"


class SpookyAudioInterface(DefaultAudioInterface):
    """
    Interfaz de audio que aplica efectos de terror en tiempo real a la voz de ElevenLabs:
    1. Pitch Shift grave (voz profunda y siniestra sin alterar la velocidad).
    2. Doble voz espectral (armonía fantasma desfasada).
    3. Eco de cripta / ultratumba (delay con retroalimentación).
    4. Trémolo fantasmal sutil.
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        main_pitch: float = 0.82,      # < 1.0 hace la voz más grave y monstruosa
        ghost_pitch: float = 1.15,     # Segunda voz espectral aguda/desfasada
        ghost_mix: float = 0.30,       # Volumen de la voz espectral (0.0 a 1.0)
        echo_delay_sec: float = 0.14,  # Tiempo del eco de caverna en segundos
        echo_decay: float = 0.42,      # Intensidad de repetición del eco
        tremolo_depth: float = 0.18,   # Temblor fantasmal en la voz
    ):
        super().__init__()
        self.main_pitch = main_pitch
        self.ghost_pitch = ghost_pitch
        self.ghost_mix = ghost_mix
        self.echo_Decay = echo_decay
        self.tremolo_depth = tremolo_depth
        self._init_dsp_state(echo_delay_sec)

    def _init_dsp_state(self, echo_delay_sec: float):
        # Buffer circular para pitch-shifting en tiempo real sin cambiar la duración
        self.window_size = 640  # 40ms a 16kHz
        self.buf_size = 4096
        self.pitch_buf = [0.0] * self.buf_size
        self.write_pos = 0
        self.phase_main = 0.0
        self.phase_ghost = 0.0

        # Dos ecos escalonados para efecto de caverna/ultratumba
        self.echo1_len = max(1, int(self.SAMPLE_RATE * echo_delay_sec))
        self.echo2_len = max(1, int(self.SAMPLE_RATE * (echo_delay_sec * 1.75)))
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len
        self.echo1_pos = 0
        self.echo2_pos = 0

        # Fase del trémolo
        self.sample_counter = 0

    def _read_pitch_grain(self, phase: float) -> float:
        """Lee del buffer circular con ventana cruzada (crossfade) para evitar clics."""
        half_win = self.window_size / 2.0
        # Dos punteros desfasados 180 grados
        offset1 = phase * self.window_size
        offset2 = ((phase + 0.5) % 1.0) * self.window_size

        # Ventana triangular/Hanning suave
        weight1 = 0.5 * (1.0 - math.cos(2.0 * math.pi * phase))
        weight2 = 1.0 - weight1

        def sample_at(offset: float) -> float:
            read_pos = (self.write_pos - half_win - offset) % self.buf_size
            idx0 = int(read_pos) % self.buf_size
            idx1 = (idx0 + 1) % self.buf_size
            frac = read_pos - int(read_pos)
            return self.pitch_buf[idx0] * (1.0 - frac) + self.pitch_buf[idx1] * frac

        return sample_at(offset1) * weight1 + sample_at(offset2) * weight2

    def _apply_spooky_effect(self, audio_bytes: bytes) -> bytes:
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

            # Avanzar fases de pitch
            self.phase_main = (self.phase_main + step_main) % 1.0
            self.phase_ghost = (self.phase_ghost + step_ghost) % 1.0
            self.write_pos = (self.write_pos + 1) % self.buf_size

            # 2. Trémolo fantasmal
            t = self.sample_counter / self.SAMPLE_RATE
            tremolo = 1.0 - self.tremolo_depth * 0.5 * (
                1.0 + math.sin(2.0 * math.pi * tremolo_freq * t)
            )
            voice *= tremolo
            self.sample_counter += 1

            # 3. Eco doble de cripta con retroalimentación
            e1 = self.echo1_buf[self.echo1_pos]
            e2 = self.echo2_buf[self.echo2_pos]
            combined = voice + e1 * self.echo_Decay + e2 * (self.echo_Decay * 0.6)

            self.echo1_buf[self.echo1_pos] = voice + e1 * (self.echo_Decay * 0.5)
            self.echo2_buf[self.echo2_pos] = combined * 0.7
            self.echo1_pos = (self.echo1_pos + 1) % self.echo1_len
            self.echo2_pos = (self.echo2_pos + 1) % self.echo2_len

            # Limitar entre -32768 y 32767 (soft clipping)
            if combined > 32767.0:
                combined = 32767.0
            elif combined < -32768.0:
                combined = -32768.0

            out_samples[i] = int(combined)

        return out_samples.tobytes()

    def output(self, audio: bytes):
        spooky_audio = self._apply_spooky_effect(audio)
        super().output(spooky_audio)

    def interrupt(self):
        super().interrupt()
        # Limpiar los ecos cuando el usuario interrumpe al agente
        self.echo1_buf = [0.0] * self.echo1_len
        self.echo2_buf = [0.0] * self.echo2_len


def main():
    if API_KEY == "TU_API_KEY" or not API_KEY:
        print("⚠️  ADVERTENCIA: Asegúrate de colocar tu API Key real en la variable API_KEY.")

    # Inicializa el cliente con tu API Key
    client = ElevenLabs(api_key=API_KEY)

    # Configura la sesión con la interfaz de audio espeluznante
    conversation = Conversation(
        client=client,
        agent_id=AGENT_ID,
        requires_auth=bool(API_KEY and API_KEY != "TU_API_KEY"),
        audio_interface=SpookyAudioInterface(
            main_pitch=0.82,      # Baja el tono (0.75 = más monstruoso, 0.90 = más sutil)
            ghost_pitch=1.15,     # Segunda voz fantasma aguda
            ghost_mix=0.30,       # Mezcla de la voz espectral
            echo_delay_sec=0.14,  # Eco de caverna
            echo_decay=0.42,      # Duración del eco
        ),
        # Callbacks para imprimir la conversación en la terminal en tiempo real
        callback_agent_response=lambda response: print(f"\n🎃 Calabaza: {response}"),
        callback_agent_response_correction=lambda original, corrected: print(
            f"\n🎃 Calabaza (corregido): {corrected}"
        ),
        callback_user_transcript=lambda transcript: print(f"\n🎤 Tú: {transcript}"),
    )

    # Manejador para cerrar la sesión limpiamente al presionar Ctrl+C
    def signal_handler(sig, frame):
        print("\n\n🛑 Cerrando sesión...")
        conversation.end_session()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print("==========================================")
    print("🎃  Iniciando conversación espeluznante...")
    print("💡 Habla por tu micrófono. Presiona Ctrl+C para salir.")
    print("==========================================\n")

    # Inicia la transmisión de audio en tiempo real
    conversation.start_session()

    # Mantiene el programa activo hasta que termine la sesión
    conversation_id = conversation.wait_for_session_end()
    print(f"\n✅ Conversación finalizada. ID de sesión: {conversation_id}")


if __name__ == "__main__":
    main()

