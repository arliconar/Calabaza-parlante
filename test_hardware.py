"""
Script de diagnóstico para Raspberry Pi 3 B+ y 4:
Prueba de forma aislada el LED, Altavoz y Micrófono antes de iniciar la IA.
Uso: python test_hardware.py
"""

import math
import sys
import time
from array import array

# Suprimir avisos de ALSA en terminal
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("==========================================")
print("🎃 DIAGNÓSTICO DE HARDWARE: CALABAZA PARLANTE")
print("==========================================\n")

# 1. PRUEBA DEL LED
print("1️⃣  PROBANDO LED (GPIO 18 / Pin físico 12)...")
try:
    from gpiozero import PWMLED, LED
    try:
        led = PWMLED(18)
        print("   -> Modo PWM detectado. Atenuando y parpadeando LED...")
        for _ in range(2):
            for b in range(0, 101, 10):
                led.value = b / 100.0
                time.sleep(0.02)
            for b in range(100, -1, -10):
                led.value = b / 100.0
                time.sleep(0.02)
        led.close()
    except Exception:
        led = LED(18)
        print("   -> Modo Digital detectado. Parpadeando LED...")
        for _ in range(4):
            led.on()
            time.sleep(0.2)
            led.off()
            time.sleep(0.2)
        led.close()
    print("   ✅ Prueba de LED terminada.\n")
except Exception as e:
    print(f"   ⚠️  Fallo en LED o sin hardware GPIO: {e}\n")

# 2. PRUEBA DE DISPOSITIVOS DE AUDIO (ALSA / PYAUDIO)
print("2️⃣  DETECCIÓN DE DISPOSITIVOS DE AUDIO...")
try:
    import pyaudio

    p = pyaudio.PyAudio()
    dev_count = p.get_device_count()
    mic_list = []
    speaker_list = []

    for i in range(dev_count):
        dev = p.get_device_info_by_index(i)
        name = dev.get("name", "Desconocido")
        max_in = dev.get("maxInputChannels", 0)
        max_out = dev.get("maxOutputChannels", 0)
        tipo = []
        if max_in > 0:
            tipo.append(f"MICRÓFONO ({max_in} ch)")
            mic_list.append((i, name))
        if max_out > 0:
            tipo.append(f"ALTAVOZ ({max_out} ch)")
            speaker_list.append((i, name))
        print(f"   [ID {i}] {name} -> {', '.join(tipo) if tipo else 'Sin canales I/O'}")

    print("\n------------------------------------------")
    if not mic_list:
        print("❌ ¡ATENCIÓN! No se detectó NINGÚN micrófono.")
        print("   👉 La Raspberry Pi 3 B+ NO tiene micrófono integrado.")
        print("   👉 Debes conectar un micrófono USB o tarjeta de sonido USB.")
    else:
        print(f"🎤 Micrófonos detectados: {len(mic_list)}")
        for idx, name in mic_list:
            print(f"   -> ID {idx}: {name}")

    if not speaker_list:
        print("❌ No se detectaron altavoces.")
    else:
        print(f"🔊 Altavoces detectados: {len(speaker_list)}")
        for idx, name in speaker_list:
            print(f"   -> ID {idx}: {name}")
    print("------------------------------------------\n")

    # 3. PRUEBA DE REPRODUCCIÓN DE AUDIO
    if speaker_list:
        print("3️⃣  PROBANDO REPRODUCCIÓN DE AUDIO (Tono audible fuerte de 1.5s)...")

        # Generar tono de prueba de 440 Hz (tono La) audible a buen volumen
        sample_rate = 44100
        duration = 1.5
        samples = array("h")
        for n in range(int(sample_rate * duration)):
            # Tono estéreo (2 canales) para máxima compatibilidad con el jack bcm2835
            val = int(25000 * math.sin(2 * math.pi * 440 * (n / sample_rate)))
            samples.append(val)  # Canal izquierdo
            samples.append(val)  # Canal derecho

        audio_bytes = samples.tobytes()

        # Probar en cada altavoz disponible
        for spk_id, spk_name in speaker_list:
            print(f"   -> Enviando sonido a [ID {spk_id}]: {spk_name}...")
            # Intentar primero en estéreo a 44.1 kHz (estándar bcm2835)
            played = False
            for rate, channels, buf in [
                (44100, 2, audio_bytes),
                (48000, 2, audio_bytes),
                (24000, 1, samples[::2].tobytes()),
            ]:
                try:
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=channels,
                        rate=rate,
                        output=True,
                        output_device_index=spk_id,
                    )
                    stream.write(buf)
                    stream.stop_stream()
                    stream.close()
                    played = True
                    print(f"      ✅ Sonido reproducido con éxito a {rate} Hz ({channels} ch).")
                    break
                except Exception as ex:
                    continue

            if not played:
                print(f"      ⚠️ No se pudo reproducir en ID {spk_id}.")

    p.terminate()

except Exception as e:
    print(f"   ⚠️  Error en PyAudio: {e}")

print("\n==========================================")
print("🎉 Diagnóstico finalizado.")
print("==========================================")
