"""
Script de prueba y calibración para Dimmer AC (110V/127V 60Hz):
Verifica la detección de cruce por cero (Pzd) y el control de fase (Pd).
Uso: python test_dimmer.py
"""

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("======================================================")
print("🎃 PRUEBA DE DIMMER AC - CALABAZA PARLANTE")
print("======================================================")

PIN_PD = 18    # Pin de Disparo (Salida) -> Pin físico 12
PIN_PZD = 24   # Pin Cruce por Cero (Entrada) -> Pin físico 18
FREQ_HZ = 60   # Red eléctrica en México / 110V-127V (60 Hz = 8.33 ms por semiciclo)
HALF_CYCLE = 1.0 / (FREQ_HZ * 2)  # ~0.00833 segundos

print(f"\n📌 Pines:")
print(f"   -> Pd  (Disparo/Pulso): GPIO {PIN_PD} (Pin físico 12)")
print(f"   -> Pzd (Cruce por cero): GPIO {PIN_PZD} (Pin físico 18)")
print(f"   -> Red AC: {FREQ_HZ} Hz (Semiciclo: {HALF_CYCLE*1000:.2f} ms)")
print(f"   -> ⚠️  Verifica que VCC del dimmer esté a 3.3V (Pin 1).\n")

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("❌ Error: RPi.GPIO no está instalado o no estás en una Raspberry Pi.")
    sys.exit(1)

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Configurar pines
GPIO.setup(PIN_PD, GPIO.OUT)
GPIO.output(PIN_PD, GPIO.LOW)
GPIO.setup(PIN_PZD, GPIO.IN, pull_up_down=GPIO.PUD_UP)

zc_counter = 0


def count_zc(channel):
    global zc_counter
    zc_counter += 1


# 1. Comprobación de la red eléctrica
print("1️⃣  Verificando pulsos de cruce por cero en Pzd (GPIO 24)...")
GPIO.add_event_detect(PIN_PZD, GPIO.FALLING, callback=count_zc, bouncetime=5)
time.sleep(1.0)
GPIO.remove_event_detect(PIN_PZD)

print(f"   -> Pulsos detectados en 1 segundo: {zc_counter}")
if zc_counter < 50:
    print("   ⚠️  ADVERTENCIA: Muy pocos o ningún pulso detectado.")
    print("   👉 Asegúrate de que el dimmer esté conectado a la corriente AC 110V.")
    print("   👉 Verifica la conexión de Pzd en GPIO 24 y GND.")
else:
    print(f"   ✅ ¡Red eléctrica detectada! (~{zc_counter} Hz semiciclos detectados).")

# Variables de control del dimmer con filtro anti-ruido
brightness = 0.0
running = True
is_firing = False
last_zc_time = 0.0


def dimmer_callback(channel):
    global brightness, running, is_firing, last_zc_time
    if not running or brightness <= 0.02 or is_firing:
        return

    # Filtro anti-ruido inductivo: en 60Hz ningún cruce por cero ocurre en menos de 5ms
    now = time.perf_counter()
    if (now - last_zc_time) < 0.0055:
        return
    last_zc_time = now

    is_firing = True
    try:
        # Mapeo seguro para focos AC y focos LED de 110V:
        # b = 1.0 -> delay = 0.4 ms (máxima potencia)
        # b = 0.1 -> delay = 6.2 ms (potencia mínima estable)
        eff_b = max(0.05, min(1.0, brightness))
        delay = 0.0004 + (1.0 - eff_b) * 0.0060

        time.sleep(delay)

        # Pulso de disparo de 25 microsegundos al gate del TRIAC
        GPIO.output(PIN_PD, GPIO.HIGH)
        time.sleep(0.000025)
        GPIO.output(PIN_PD, GPIO.LOW)
    finally:
        is_firing = False


# Registrar interrupción con bouncetime de 5ms por hardware
GPIO.add_event_detect(PIN_PZD, GPIO.FALLING, callback=dimmer_callback, bouncetime=5)

try:
    print("\n2️⃣  Prueba de atenuación suave (0% -> 100% -> 0%)...")
    for b in range(10, 101, 5):
        brightness = b / 100.0
        print(f"   Brillo: {b}%  ", end="\r")
        time.sleep(0.12)
    for b in range(100, -1, -5):
        brightness = b / 100.0
        print(f"   Brillo: {b}%  ", end="\r")
        time.sleep(0.12)
    print("\n   ✅ Atenuación gradual completada con éxito.")

    print("\n3️⃣  PARPADEO DE VELA DE HALLOWEEN EN BUCLE CONTINUO:")
    print("   💡 El foco parpadeará como vela encantada indefinidamente.")
    print("   🛑 Presiona Ctrl+C cuando desees terminar la prueba.\n")

    # Patrón de parpadeo realista de vela / antorcha de Halloween
    candle_pattern = [
        (0.95, 0.12),
        (0.40, 0.08),
        (0.85, 0.15),
        (0.25, 0.07),
        (0.90, 0.10),
        (0.15, 0.06),
        (0.75, 0.20),
        (0.00, 0.08),
        (0.80, 0.14),
        (0.35, 0.09),
    ]

    while True:
        for b_level, duration in candle_pattern:
            brightness = b_level
            time.sleep(duration)

except KeyboardInterrupt:
    print("\n\n🛑 Prueba detenida por el usuario.")

finally:
    running = False
    brightness = 0.0
    GPIO.output(PIN_PD, GPIO.LOW)
    GPIO.cleanup()
    print("Pines GPIO liberados correctamente.")
