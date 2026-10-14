"""
nodo_mqtt.py — Nodo de borde MQTT (B10)
=======================================

Corre la simulación SUMO por TraCI y actúa como puente entre el simulador y
el broker MQTT (arquitectura de integración hacia semáforos reales):

  - PUBLICA el estado de cada intersección en  -> interseccion/<tls>/estado
  - ESCUCHA comandos en                        <- interseccion/<tls>/comando
  - APLICA el estado recibido por TraCI y mide la LATENCIA entre que el
    comando fue publicado y fue aplicado (publicar -> actuar).

Graba todo en un CSV para el panel de la interfaz:
    data/resultados_demo/<nombre>_mqtt.csv

Uso:
    mosquitto -c mqtt/mosquitto.conf &
    python src/nodo_mqtt.py --escenario alto --duracion 300 --semilla 42
"""

import argparse
import csv
import json
import os
import queue
import time

import paho.mqtt.client as mqtt
import traci

from capa_integracion import (
    TOPICO_ACK,
    TOPICO_COMANDO,
    TOPICO_CONFIG,
    TOPICO_ESTADO,
    construir_fuente,
)
from control_export import (
    ARCHIVO_DETECTORES,
    CARPETA_ESCENARIOS,
    CARPETA_RESULTADOS,
    CARRILES,
    MAPA_DETECTORES,
    NET_FILE,
    SUMO_BINARY,
    TLS_IDS,
    carriles_por_indice,
)


def crear_cliente(client_id, host, puerto, on_connect, on_message):
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    cliente.on_connect = on_connect
    cliente.on_message = on_message
    cliente.connect(host, puerto)
    cliente.loop_start()
    return cliente


def main():
    parser = argparse.ArgumentParser(description="Nodo de borde MQTT (B10)")
    parser.add_argument("--escenario", default="alto", choices=["bajo", "medio", "alto"])
    parser.add_argument("--duracion", type=int, default=300)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument("--fuente", default="traci", choices=["traci", "detectores"])
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-puerto", type=int, default=1883)
    parser.add_argument("--nombre", default="nodo",
                        help="prefijo del archivo de salida <nombre>_mqtt.csv")
    parser.add_argument("--espera-paso", type=float, default=0.0,
                        help="segundos de reloj a esperar por paso (0 = lo más rápido; "
                             "1.0 = tiempo real, útil para la demo en vivo)")
    args = parser.parse_args()

    cola_comandos = queue.Queue()

    def on_connect(cliente, usuario, flags, rc, properties=None):
        for tls_id in TLS_IDS:
            cliente.subscribe(TOPICO_COMANDO.format(tls=tls_id))
        print(f"[nodo] conectado al broker {args.broker_host}:{args.broker_puerto}")

    def on_message(cliente, usuario, mensaje):
        try:
            datos = json.loads(mensaje.payload.decode())
        except (ValueError, UnicodeDecodeError):
            return
        cola_comandos.put((datos.get("tls"), datos.get("estado"), datos.get("ts", time.time())))

    cliente = crear_cliente("nodo_borde", args.broker_host, args.broker_puerto,
                            on_connect, on_message)

    # --- Arranque de SUMO (igual que control_export) ---
    ruta_rou = os.path.join(CARPETA_ESCENARIOS, f"{args.escenario}.rou.xml")
    comando = [SUMO_BINARY, "-n", NET_FILE, "-r", ruta_rou, "--no-warnings", "true",
               "--seed", str(args.semilla)]
    if args.fuente == "detectores":
        comando += ["-a", ARCHIVO_DETECTORES]
    traci.start(comando)
    fuente = construir_fuente(args.fuente, MAPA_DETECTORES)

    # Publica (retenido) el programa de fases y el mapa link->carril REALES de
    # TraCI, para que el centro de control remoto use exactamente esta geometría.
    for tls_id in TLS_IDS:
        logica = traci.trafficlight.getAllProgramLogics(tls_id)[0]
        estados = [fase.state for fase in logica.phases]
        mapa = {str(k): v for k, v in carriles_por_indice(tls_id).items()}
        cliente.publish(TOPICO_CONFIG.format(tls=tls_id),
                        json.dumps({"estados": estados, "carril_por_indice": mapa}),
                        retain=True)

    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    ruta_mqtt = os.path.join(CARPETA_RESULTADOS, f"{args.nombre}_mqtt.csv")

    pasos = 0
    comandos_aplicados = 0
    latencias = []

    with open(ruta_mqtt, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(["tiempo", "tls", "tipo", "topico", "resumen", "latencia_ms"])

        while pasos < args.duracion and traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            fuente.paso()

            # 1) Aplicar los comandos recibidos y medir la latencia (publicar -> actuar)
            while not cola_comandos.empty():
                tls_id, estado, ts_emitido = cola_comandos.get()
                if not estado:
                    continue
                traci.trafficlight.setRedYellowGreenState(tls_id, estado)
                latencia_ms = round((time.time() - ts_emitido) * 1000, 3)
                comandos_aplicados += 1
                latencias.append(latencia_ms)
                cliente.publish(TOPICO_ACK.format(tls=tls_id),
                                json.dumps({"tls": tls_id, "estado": estado,
                                            "latencia_ms": latencia_ms}))
                escritor.writerow([pasos, tls_id, "recibe_comando",
                                   TOPICO_COMANDO.format(tls=tls_id), estado, latencia_ms])

            # 2) Publicar el estado de cada intersección (telemetría)
            for tls_id in TLS_IDS:
                estado = traci.trafficlight.getRedYellowGreenState(tls_id)
                cola = {c: fuente.cola(c) for c in CARRILES[tls_id]}
                espera = {c: fuente.espera(c) for c in CARRILES[tls_id]}
                payload = json.dumps({"tiempo": pasos, "tls": tls_id, "estado": estado,
                                      "cola": cola, "espera": espera})
                cliente.publish(TOPICO_ESTADO.format(tls=tls_id), payload)
                escritor.writerow([pasos, tls_id, "publica_estado",
                                   TOPICO_ESTADO.format(tls=tls_id), estado, ""])

            pasos += 1
            if args.espera_paso > 0:
                time.sleep(args.espera_paso)

    traci.close()
    cliente.loop_stop()

    if latencias:
        promedio = sum(latencias) / len(latencias)
        print(f"[nodo] comandos aplicados: {comandos_aplicados} | latencia ms "
              f"(min/prom/max): {min(latencias):.3f} / {promedio:.3f} / {max(latencias):.3f}")
    else:
        print("[nodo] sin comandos recibidos (¿arrancó control_remoto.py?)")
    print(f"[nodo] registro: {ruta_mqtt}")


if __name__ == "__main__":
    main()
