"""
generar_detectores.py
Crea un detector E2 (laneAreaDetector) al final de cada carril de entrada
de los 7 semaforos del corredor Guaimaral / Libertadores, leyendo la red
con sumolib.

Salidas (junto a la red, en sumo/):
  - detectores.add.xml    -> definicion de los detectores para SUMO
  - mapa_detectores.json  -> correspondencia carril <-> detector y
                             semaforo <-> detectores, para que la capa de
                             integracion no tenga que re-parsear la red.

Uso (desde la raiz del proyecto):
    python src/generar_detectores.py
    python src/generar_detectores.py --longitud 40
    python src/generar_detectores.py --red sumo/otra_red.net.xml.gz

Nota: este script solo GENERA los archivos. Para que SUMO los use hay que
correr con el additional-file (ya se agrega a sumo/osm.sumocfg).
"""

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET

import sumolib

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------

RAIZ = os.path.join(os.path.dirname(__file__), "..")
RED_POR_DEFECTO = os.path.join(RAIZ, "sumo", "red_guaimaral_libertadores.net.xml.gz")
ADD_SALIDA = os.path.join(RAIZ, "sumo", "detectores.add.xml")
MAPA_SALIDA = os.path.join(RAIZ, "sumo", "mapa_detectores.json")

TLS_IDS = ["a9c2t", "agc2t", "a9c5t", "agc5t", "alc8t", "alc4t", "alc2t"]

# Longitud del detector (metros). Si el carril es mas corto, se usa el
# carril completo. Un E2 de 50 m al final del carril mide cola y ocupacion
# justo aguas arriba del punto de parada.
LONGITUD_DETECTOR = 50.0

# Periodo de agregacion (segundos de simulacion) de cada detector.
PERIODO = 1.0

# SUMO exige el atributo 'file' en los E2. Como los leemos por TraCI (no nos
# interesa el volcado a disco), apuntamos a "NUL", que SUMO interpreta como
# dispositivo nulo en cualquier plataforma y no crea ningun archivo.
ARCHIVO_SALIDA = "NUL"


# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

def id_detector(carril_id):
    """
    Convierte un id de carril de SUMO (ej. '287898717#8_0') en un id de
    detector seguro para XML y para topicos MQTT (ej. 'det_287898717_8_0').
    """
    limpio = re.sub(r"[^0-9A-Za-z_]", "_", carril_id)
    return f"det_{limpio}"


def ordenar_carriles(net, tls_id):
    """
    Devuelve los carriles de entrada unicos de un semaforo, en el orden en
    que SUMO los lista en sus conexiones (mismo orden que usa TraCI).
    """
    tls = net.getTLS(tls_id)
    vistos = []
    for conexion in tls.getConnections():
        carril_entrada = conexion[0]  # objeto Lane
        carril_id = carril_entrada.getID()
        if carril_id not in vistos:
            vistos.append(carril_id)
    return vistos


# ---------------------------------------------------------------------------
# PRINCIPAL
# ---------------------------------------------------------------------------

def generar(ruta_red, longitud, ruta_add, ruta_mapa):
    print(f"Leyendo red: {ruta_red}")
    net = sumolib.net.readNet(ruta_red)

    detectores = []          # lista de dicts con los datos de cada detector
    por_carril = {}          # carril_id -> id_detector
    por_semaforo = {}        # tls_id -> [id_detector, ...]

    for tls_id in TLS_IDS:
        try:
            carriles = ordenar_carriles(net, tls_id)
        except KeyError:
            print(f"ADVERTENCIA: no se encontro el TLS '{tls_id}' en la red, se omite.")
            continue

        ids_semaforo = []
        for carril_id in carriles:
            carril = net.getLane(carril_id)
            largo = carril.getLength()

            # El detector ocupa los ultimos 'longitud' metros del carril
            # (o el carril completo si es mas corto que eso).
            largo_detector = min(longitud, largo)
            fin = largo
            inicio = max(0.0, largo - largo_detector)

            det_id = id_detector(carril_id)
            detectores.append({
                "id": det_id,
                "lane": carril_id,
                "pos": round(inicio, 2),
                "endPos": round(fin, 2),
            })
            por_carril[carril_id] = det_id
            ids_semaforo.append(det_id)

        por_semaforo[tls_id] = ids_semaforo

    escribir_add(detectores, ruta_add)
    escribir_mapa(por_carril, por_semaforo, ruta_mapa)

    print(f"\nDetectores generados: {len(detectores)}")
    for tls_id in TLS_IDS:
        if tls_id in por_semaforo:
            print(f"  {tls_id}: {len(por_semaforo[tls_id])} carriles")
    print(f"\nListo: {ruta_add}")
    print(f"Listo: {ruta_mapa}")


def escribir_add(detectores, ruta_add):
    os.makedirs(os.path.dirname(ruta_add), exist_ok=True)
    raiz = ET.Element("additional")
    # Orden estable por id para que el archivo sea reproducible.
    for d in sorted(detectores, key=lambda x: x["id"]):
        ET.SubElement(raiz, "laneAreaDetector", {
            "id": d["id"],
            "lane": d["lane"],
            "pos": str(d["pos"]),
            "endPos": str(d["endPos"]),
            "period": str(PERIODO),
            "file": ARCHIVO_SALIDA,
            "friendlyPos": "true",
        })
    arbol = ET.ElementTree(raiz)
    ET.indent(arbol, space="  ")
    arbol.write(ruta_add, encoding="UTF-8", xml_declaration=True)


def escribir_mapa(por_carril, por_semaforo, ruta_mapa):
    os.makedirs(os.path.dirname(ruta_mapa), exist_ok=True)
    mapa = {
        "por_carril": por_carril,
        "por_semaforo": por_semaforo,
    }
    with open(ruta_mapa, "w", encoding="utf-8") as archivo:
        json.dump(mapa, archivo, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--red", default=RED_POR_DEFECTO, help="ruta de la red .net.xml(.gz)")
    parser.add_argument("--longitud", type=float, default=LONGITUD_DETECTOR,
                        help="longitud del detector en metros (por defecto 50)")
    parser.add_argument("--add-salida", default=ADD_SALIDA, help="ruta de detectores.add.xml")
    parser.add_argument("--mapa-salida", default=MAPA_SALIDA, help="ruta de mapa_detectores.json")
    args = parser.parse_args()

    generar(args.red, args.longitud, args.add_salida, args.mapa_salida)
