#!/bin/bash
# ========================================================
# Script de auto-inicio para la Calabaza Parlante
# Raspberry Pi 3 Model B+ / Raspberry Pi 4
# ========================================================

# 1. DEFINE AQUÍ LA RUTA A TU CARPETA DEL PROYECTO:
PROJECT_DIR="/home/pi/Calabaza parlante"
VENV_PATH="$PROJECT_DIR/venv"

echo "=================================================="
echo "🎃 Iniciando lanzador de la Calabaza Parlante..."
echo "=================================================="

# 2. ASEGURAR QUE PULSEAUDIO ESTÉ CORRIENDO (Para audio Bluetooth)
echo "🔊 Verificando servidor PulseAudio..."
pulseaudio --start --log-target=syslog 2>/dev/null || true
sleep 2

# 3. ESPERAR CONEXIÓN A INTERNET
# Gemini Live API requiere conexión activa a internet
echo "🌐 Comprobando conexión a internet..."
MAX_INTENTOS=60
INTENTO=0

while ! ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; do
    INTENTO=$((INTENTO + 1))
    echo "   [Intento $INTENTO/$MAX_INTENTOS] Esperando conexión a internet..."
    if [ $INTENTO -ge $MAX_INTENTOS ]; then
        echo "❌ Error: Tiempo de espera agotado sin conexión a internet."
        exit 1
    fi
    sleep 3
done

echo "✅ Conexión a internet establecida con éxito."

# Pequeña pausa para permitir que los audífonos Bluetooth se auto-conecten tras el arranque
echo "🎧 Esperando sincronización Bluetooth (5 segundos)..."
sleep 5

# 4. ENTRAR A LA CARPETA DEL PROYECTO
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Error: La carpeta '$PROJECT_DIR' no existe. Revisa la variable PROJECT_DIR en este script."
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
echo "📁 Directorio de trabajo: $(pwd)"

# 5. ACTIVAR EL ENTORNO VIRTUAL
if [ -f "$VENV_PATH/bin/activate" ]; then
    echo "🐍 Activando entorno virtual ($VENV_PATH)..."
    source "$VENV_PATH/bin/activate"
else
    echo "⚠️  Aviso: No se encontró entorno virtual en '$VENV_PATH'. Usando Python global del sistema."
fi

# 6. EJECUTAR LA CALABAZA PARLANTE
echo "🚀 Arrancando main.py..."
exec python3 main.py
