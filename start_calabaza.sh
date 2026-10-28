#!/bin/bash
# ========================================================
# Script de auto-inicio para la Calabaza Parlante
# Raspberry Pi 3 Model B+ / Raspberry Pi 4
# ========================================================

# 1. RUTA DEL PROYECTO
PROJECT_DIR="/home/angel/Calabaza-parlante"
VENV_PATH="$PROJECT_DIR/venv"

echo "=================================================="
echo "🎃 Iniciando lanzador de la Calabaza Parlante..."
echo "=================================================="

# 2. ASEGURAR QUE PULSEAUDIO ESTÉ CORRIENDO (Para audio Bluetooth)
echo "🔊 Verificando servidor PulseAudio..."
pulseaudio --start --log-target=syslog 2>/dev/null || true
sleep 2

# 3. ESPERAR CONEXIÓN A INTERNET
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

# Pausa para permitir que los audífonos Bluetooth se auto-conecten
echo "🎧 Esperando sincronización Bluetooth (5 segundos)..."
sleep 5

# 4. ENTRAR A LA CARPETA DEL PROYECTO
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ Error: La carpeta '$PROJECT_DIR' no existe."
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
echo "📁 Directorio de trabajo: $(pwd)"

# 5. LOCALIZAR EL EJECUTABLE DE PYTHON DEL ENTORNO VIRTUAL
if [ -x "$VENV_PATH/bin/python" ]; then
    PYTHON_BIN="$VENV_PATH/bin/python"
    echo "🐍 Usando Python del entorno virtual: $PYTHON_BIN"
elif [ -x "$VENV_PATH/bin/python3" ]; then
    PYTHON_BIN="$VENV_PATH/bin/python3"
    echo "🐍 Usando Python del entorno virtual: $PYTHON_BIN"
else
    echo "⚠️  Aviso: No se encontró ejecutable en '$VENV_PATH'. Usando python3 global."
    PYTHON_BIN="python3"
fi

# 6. EJECUTAR LA CALABAZA PARLANTE
echo "🚀 Arrancando main.py..."
exec "$PYTHON_BIN" main.py
