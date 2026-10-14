#!/usr/bin/env bash
#
# demo.sh — Lanzador de la demo de integración MQTT (B10)
# =======================================================
#
# Levanta toda la arquitectura de integración hacia semáforos reales:
#
#     [SUMO/TraCI] --nodo_mqtt.py--> [Mosquitto] <--control_remoto.py--> (decide)
#          ^                                                              |
#          +---------------- comando (TraCI) <---------------------------+
#
#   1) Broker Mosquitto local (mqtt/mosquitto.conf)
#   2) Centro de control remoto (src/control_remoto.py)  -> decide fases
#   3) Nodo de borde (src/nodo_mqtt.py)                  -> publica/actúa y mide latencia
#
# El nodo graba data/resultados_demo/<NOMBRE>_mqtt.csv, que es lo que usa el
# panel "pregrabado" de la interfaz (así se ve el panel hasta en GitHub Pages).
#
# Uso (desde cualquier carpeta):
#     bash mqtt/demo.sh
#
# Variables de entorno opcionales:
#     ESCENARIO=alto        (bajo|medio|alto)
#     FUENTE=traci          (traci|detectores)
#     DURACION=300          segundos de simulación a grabar
#     SEMILLA=42
#     ESPERA_PASO=0.05      segundos de reloj por paso (0=lo más rápido, 1=tiempo real)
#     NOMBRE=demo           prefijo del CSV de salida
#
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

ESCENARIO="${ESCENARIO:-alto}"
FUENTE="${FUENTE:-traci}"
DURACION="${DURACION:-300}"
SEMILLA="${SEMILLA:-42}"
ESPERA_PASO="${ESPERA_PASO:-0.05}"
NOMBRE="${NOMBRE:-demo}"
HOST="${BROKER_HOST:-127.0.0.1}"
PUERTO="${BROKER_PUERTO:-1883}"

# Usa el python del venv del proyecto si existe; si no, el del sistema.
if [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
else
  PY="python3"
fi

if ! command -v mosquitto >/dev/null 2>&1; then
  echo "ERROR: no se encontró 'mosquitto'."
  echo "  Arch:    sudo pacman -S mosquitto"
  echo "  Debian:  sudo apt install mosquitto"
  exit 1
fi

BROKER_PID=""
CENTRO_PID=""

limpiar() {
  echo ""
  echo "[demo] deteniendo procesos..."
  [ -n "$CENTRO_PID" ] && kill "$CENTRO_PID" 2>/dev/null || true
  [ -n "$BROKER_PID" ] && kill "$BROKER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap limpiar EXIT INT TERM

echo "[demo] 1/3 arrancando broker Mosquitto ($HOST:$PUERTO)..."
mosquitto -c mqtt/mosquitto.conf >/tmp/smartjunction_mosquitto.log 2>&1 &
BROKER_PID=$!
sleep 1

echo "[demo] 2/3 arrancando centro de control remoto..."
"$PY" src/control_remoto.py --broker-host "$HOST" --broker-puerto "$PUERTO" \
  --duracion "$((DURACION + 30))" >/tmp/smartjunction_centro.log 2>&1 &
CENTRO_PID=$!
sleep 1

echo "[demo] 3/3 corriendo nodo de borde (escenario=$ESCENARIO fuente=$FUENTE duración=$DURACION s)..."
"$PY" src/nodo_mqtt.py \
  --escenario "$ESCENARIO" --fuente "$FUENTE" \
  --duracion "$DURACION" --semilla "$SEMILLA" \
  --espera-paso "$ESPERA_PASO" --nombre "$NOMBRE" \
  --broker-host "$HOST" --broker-puerto "$PUERTO"

echo ""
echo "[demo] grabación lista: data/resultados_demo/${NOMBRE}_mqtt.csv"
echo "[demo] para volcarla al panel de la interfaz:  $PY src/combinar_datos.py"
