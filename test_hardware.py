"""
Script de diagnóstico para Raspberry Pi 4:
Prueba de forma aislada el LED, Altavoz y Micrófono antes de iniciar la IA.
Uso: python test_hardware.py
"""

import time
import math
import sys
from array import array

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("==========================================")
print("🎃 DIAGNÓSTICO DE HARDWARE: CALABAZA PARLANTE")
print("==========================================\n")

# 1. PRUEBA DEL LED
print("1️⃣  PROBANDO LED (GPIO 18 / Pin 12)...")
try:
    from gpiozero import PWMLED
    led = PWMLED(18)
    print("   -> Atenuando y parpadeando LED...")
    for _ in range(3):
        # Efecto de respiración/pulsación
        for b in range(0, 101, 5):
            led.value = b / 100.0
            time.sleep(0.01)
        for b in range(100, -1, -5):
            led.value = b / 100.0
            time.sleep(0.01)
    led.close()
    print("   ✅ LED funcionando correctamente.\n")
except Exception as e:
    print(f"   ⚠️  Fallo en LED o sin hardware GPIO: {e}\n")

# 2. PRUEBA DE DISPOSITIVOS DE AUDIO (ALSA / PYAUDIO)
print("2️⃣  DETECCIÓN DE DISPOSITIVOS DE AUDIO (PyAudio)...")
try:
    import pyaudio
    p = pyaudio.PyAudio()
    dev_count = p.get_device_count()
    print(f"   Dispositivos encontrados: {dev_count}")
    mic_found = False
    speaker_found = False

    for i in range(dev_count):
        dev = p.get_device_info_by_index(i)
        name = dev.get("name", "Desconocido")
        max_in = dev.get("maxInputChannels", 0)
        max_out = dev.get("maxOutputChannels", 0)
        tipo = []
        if max_in > 0:
            tipo.append(f"MICRÓFONO ({max_in} ch)")
            mic_found = True
        if max_out > 0:
            tipo.append(f"ALTAVOZ ({max_out} ch)")
            speaker_found = True
        print(f"   [ID {i}] {name} -> {', '.join(tipo) if tipo else 'Sin I/O'}")

    # Prueba de sonido (tono sinusoidal de 440 Hz)
    if speaker_found:
        print("\n3️⃣  PROBANDO REPRODUCCIÓN DE AUDIO (Tono de prueba)...")
        sample_rate = 24000
        duration = 0.5
        samples = array("h")
        for n in range(int(sample_rate * duration)):
            val = int(10000 * math.sin(2 * math.pi * 440 * (n / sample_rate)))
            samples.append(val)
        
        try:
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=sample_rate, output=True)
            stream.write(samples.tobytes())
            stream.stop_stream()
            stream.close()
            print("   ✅ Altavoz probado exitosamente.")
        except Exception as e:
            print(f"   ⚠️  Error al reproducir audio: {e}")

    p.terminate()
except Exception as e:
    print(f"   ⚠️  PyAudio no instalado o error: {e}")

print("\n==========================================")
print("🎉 Diagnóstico finalizado.")
print("==========================================")

