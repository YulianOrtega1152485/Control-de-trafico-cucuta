"""
control_remoto.py — Centro de control remoto por MQTT (B10)
==========================================================

Proceso que representa el "centro de control" (tipo STMC): NO corre SUMO.
Se suscribe a:
  - `interseccion/<tls>/config` (retenido): programa de fases y mapa
    link->carril REALES que publica el nodo de borde.
  - `interseccion/<tls>/estado`: cola y espera por carril.

Aplica la misma heurística adaptativa (reutiliza `ControladorAdaptativo` de
control_export.py) y publica el cambio de fase en `interseccion/<tls>/comando`.
El nodo de borde (`nodo_mqtt.py`) es quien lo aplica por TraCI.

Uso:
    python src/control_remoto.py --duracion 300
"""

import argparse
import json
import time

import paho.mqtt.client as mqtt

from capa_integracion import (
    ActuadorMQTT,
    FuenteMQTT,
    TOPICO_COMANDO,
    TOPICO_CONFIG,
    TOPICO_ESTADO,
)
from control_export import ControladorAdaptativo, TLS_IDS


def main():
    parser = argparse.ArgumentParser(description="Centro de control remoto MQTT (B10)")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-puerto", type=int, default=1883)
    parser.add_argument("--duracion", type=int, default=300,
                        help="segundos de reloj antes de terminar (0 = infinito)")
    args = parser.parse_args()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="centro_control")
    actuador = ActuadorMQTT(cliente, TOPICO_COMANDO)

    carril_a_tls = {}
    fuente = FuenteMQTT(carril_a_tls)
    controladores = {}

    def crear_controlador(tls_id, config):
        if tls_id in controladores:
            return
        estados = config["estados"]
        mapa = {int(k): v for k, v in config["carril_por_indice"].items()}
        for carril in mapa.values():
            carril_a_tls[carril] = tls_id
        controladores[tls_id] = ControladorAdaptativo(
            tls_id, fuente=fuente, actuador=actuador,
            estados_programa=estados, carril_por_indice=mapa,
        )
        print(f"[centro] intersección lista: {tls_id}")

    def on_connect(cli, usuario, flags, rc, properties=None):
        cli.subscribe(TOPICO_CONFIG.format(tls="+"))
        cli.subscribe(TOPICO_ESTADO.format(tls="+"))
        print(f"[centro] conectado al broker {args.broker_host}:{args.broker_puerto}")

    def on_message(cli, usuario, mensaje):
        partes = mensaje.topic.split("/")  # interseccion / <tls> / <tipo>
        if len(partes) != 3:
            return
        tls_id, tipo = partes[1], partes[2]
        try:
            datos = json.loads(mensaje.payload.decode())
        except (ValueError, UnicodeDecodeError):
            return

        if tipo == "config":
            crear_controlador(tls_id, datos)
        elif tipo == "estado":
            controlador = controladores.get(tls_id)
            if controlador is None:
                return
            fuente.actualizar(tls_id, {"cola": datos.get("cola", {}),
                                       "espera": datos.get("espera", {})})
            controlador.paso()

    cliente.on_connect = on_connect
    cliente.on_message = on_message
    cliente.connect(args.broker_host, args.broker_puerto)
    cliente.loop_start()

    print("[centro] en marcha. Ctrl+C para salir.")
    try:
        if args.duracion > 0:
            time.sleep(args.duracion)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        cliente.loop_stop()
        print("[centro] detenido.")


if __name__ == "__main__":
    main()
