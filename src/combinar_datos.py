"""
combinar_datos.py
Empaqueta las 6 corridas (bajo/medio/alto x fijo/adaptativo) y la
geometria real de la red en un solo archivo interfaz/datos.js, listo
para que index.html lo cargue con <script src="datos.js"></script>.

Uso (desde la raiz del proyecto):
    python src/combinar_datos.py
"""

import csv
import json
import math
import os

import sumolib

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------

RAIZ = os.path.join(os.path.dirname(__file__), "..")
NET_FILE = os.path.join(RAIZ, "sumo", "red_guaimaral_libertadores.net.xml.gz")
CARPETA_RESULTADOS = os.path.join(RAIZ, "data", "resultados_demo")
SALIDA_JS = os.path.join(RAIZ, "interfaz", "datos.js")

TLS_IDS = ["a9c2t", "agc2t", "a9c5t", "agc5t", "alc8t", "alc4t", "alc2t"]
NIVELES = ["bajo", "medio", "alto"]
MODOS = ["fijo", "adaptativo"]

CANVAS_ANCHO = 1400
CANVAS_ALTO = 820
PADDING = 50

# Cada cuantos segundos de simulacion se guarda un frame para la interfaz.
# 1800s de simulacion / PASO_FRAME=2 -> 900 frames por corrida, tamano manejable.
PASO_FRAME = 2

PREFIJOS_TIPO = {
    "carro_": "carro",
    "moto_": "moto",
    "bus_": "bus",
}


# ---------------------------------------------------------------------------
# GEOMETRIA REAL DE LA RED
# ---------------------------------------------------------------------------

def construir_transformador(net):
    """
    Devuelve una funcion que convierte coordenadas reales de SUMO
    (metros, origen arbitrario) a coordenadas de canvas (pixeles,
    origen arriba-izquierda), manteniendo la proporcion real de la red
    y centrando con un margen (PADDING).
    """
    min_x, min_y, max_x, max_y = net.getBoundary()
    ancho_red = max(max_x - min_x, 1)
    alto_red = max(max_y - min_y, 1)

    escala = min(
        (CANVAS_ANCHO - 2 * PADDING) / ancho_red,
        (CANVAS_ALTO - 2 * PADDING) / alto_red,
    )

    def transformar(x, y):
        cx = PADDING + (x - min_x) * escala
        # Se invierte el eje Y: en SUMO "arriba" es Y creciente,
        # en el canvas "arriba" es Y = 0.
        cy = CANVAS_ALTO - (PADDING + (y - min_y) * escala)
        return [round(cx, 1), round(cy, 1)]

    return transformar


def extraer_geometria_red(net, transformar):
    calles = []
    for edge in net.getEdges():
        if edge.isSpecial():
            continue
        puntos = [transformar(x, y) for x, y in edge.getShape()]
        calles.append(puntos)

    semaforos = {}
    enlaces = {}          # tls_id -> [[x,y], ...], uno por via (edge) de entrada
    angulos_enlace = {}   # tls_id -> [rad], direccion del flujo en cada via
    indices_enlace = {}   # tls_id -> [[indices de link], ...], mismo orden que enlaces

    for tls_id in TLS_IDS:
        try:
            tls = net.getTLS(tls_id)
        except KeyError:
            print(f"ADVERTENCIA: no se encontro el TLS '{tls_id}' en la red.")
            continue

        conexiones = tls.getConnections()
        if not conexiones:
            print(f"ADVERTENCIA: el TLS '{tls_id}' no tiene conexiones asociadas.")
            continue

        nodos = set()
        datos_por_via = {}

        for conexion in conexiones:
            carril_entrada = conexion[0]
            indice_enlace = conexion[2]
            via_id = carril_entrada.getEdge().getID()

            nodos.add(carril_entrada.getEdge().getToNode())
            via = datos_por_via.setdefault(
                via_id,
                {"indices": [], "puntos": [], "forma_referencia": None},
            )
            via["indices"].append(indice_enlace)

            forma = carril_entrada.getShape()
            if len(forma) >= 2 and via["forma_referencia"] is None:
                via["forma_referencia"] = forma
            via["puntos"].append(transformar(*forma[-1]))

        xs = [n.getCoord()[0] for n in nodos]
        ys = [n.getCoord()[1] for n in nodos]
        semaforos[tls_id] = transformar(sum(xs) / len(xs), sum(ys) / len(ys))

        # Un semaforo por via de entrada (edge), no por carril paralelo.
        vias_ordenadas = list(datos_por_via.keys())
        puntos_via = []
        angulos_via = []
        indices_via = []

        for via_id in vias_ordenadas:
            via = datos_por_via[via_id]
            px = sum(p[0] for p in via["puntos"]) / len(via["puntos"])
            py = sum(p[1] for p in via["puntos"]) / len(via["puntos"])
            puntos_via.append([round(px, 1), round(py, 1)])

            forma = via["forma_referencia"] or []
            if len(forma) >= 2:
                p0 = transformar(*forma[-2])
                p1 = transformar(*forma[-1])
                angulo = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
            else:
                angulo = 0.0
            angulos_via.append(round(angulo, 4))

            indices_unicos = sorted(set(via["indices"]))
            indices_via.append(indices_unicos)

        enlaces[tls_id] = puntos_via
        angulos_enlace[tls_id] = angulos_via
        indices_enlace[tls_id] = indices_via

    return calles, semaforos, enlaces, indices_enlace, angulos_enlace


# ---------------------------------------------------------------------------
# LECTURA DE FRAMES Y RESUMEN POR CORRIDA
# ---------------------------------------------------------------------------

def tipo_por_id(veh_id):
    for prefijo, tipo in PREFIJOS_TIPO.items():
        if veh_id.startswith(prefijo):
            return tipo
    return "carro"  # valor por defecto si algun id no tiene el prefijo esperado


def leer_frames(ruta_csv, transformar):
    """
    Agrupa las filas del CSV de frames por segundo de simulacion y las
    convierte a la estructura compacta que usa la interfaz. Solo se
    conserva 1 de cada PASO_FRAME segundos para no generar un archivo
    demasiado pesado.
    """
    frames_por_tiempo = {}

    with open(ruta_csv, "r", encoding="utf-8") as archivo:
        lector = csv.DictReader(archivo)
        for fila in lector:
            tiempo = int(float(fila["tiempo"]))
            if tiempo % PASO_FRAME != 0:
                continue

            frame = frames_por_tiempo.setdefault(tiempo, {"t": tiempo, "v": [], "s": {}})

            if fila["tipo"] == "vehiculo":
                x, y = transformar(float(fila["x"]), float(fila["y"]))
                frame["v"].append([fila["id"], tipo_por_id(fila["id"]), x, y])
            elif fila["tipo"] == "semaforo":
                frame["s"][fila["id"]] = fila["estado_semaforo"]

    return [frames_por_tiempo[t] for t in sorted(frames_por_tiempo)]


def leer_resumen(ruta_csv):
    valores = {}
    with open(ruta_csv, "r", encoding="utf-8") as archivo:
        lector = csv.reader(archivo)
        next(lector)  # salta encabezado "metrica,valor"
        for fila in lector:
            if len(fila) == 2:
                valores[fila[0]] = fila[1]
    return valores

def calcular_indices_libres(corridas, indices_enlace):
    """
    Detecta, por cada semaforo, los indices de enlace que estan en verde
    (G/g) en absolutamente todos los frames de todas las corridas. Esto
    identifica movimientos "libres" (ej. un giro canalizado sin conflicto
    real de transito) que SUMO deja siempre en verde sin importar la fase
    activa. Si se mezclan con otros movimientos del mismo carril que si
    dependen del ciclo, tapan visualmente el color real de ese carril.
    """
    libres = {}
    for tls_id, grupos in indices_enlace.items():
        todos_los_indices = sorted({i for grupo in grupos for i in grupo})
        es_libre = {i: True for i in todos_los_indices}

        for corrida in corridas.values():
            for frame in corrida["frames"]:
                estado = frame["s"].get(tls_id)
                if not estado:
                    continue
                for i in todos_los_indices:
                    if i < len(estado) and estado[i] not in ("G", "g"):
                        es_libre[i] = False

        libres[tls_id] = sorted(i for i, libre in es_libre.items() if libre)

    return libres


# ---------------------------------------------------------------------------
# PRINCIPAL
# ---------------------------------------------------------------------------

def main():
    print("Leyendo red real...")
    net = sumolib.net.readNet(NET_FILE)
    transformar = construir_transformador(net)
    calles, semaforos, enlaces, indices_enlace, angulos_enlace = extraer_geometria_red(
        net, transformar
    )

    comparativo = {}
    corridas = {}

    for nivel in NIVELES:
        comparativo[nivel] = {}
        for modo in MODOS:
            clave = f"{nivel}_{modo}"
            ruta_frames = os.path.join(CARPETA_RESULTADOS, f"{clave}_frames.csv")
            ruta_resumen = os.path.join(CARPETA_RESULTADOS, f"{clave}_resumen.csv")

            if not os.path.exists(ruta_frames) or not os.path.exists(ruta_resumen):
                print(f"AVISO: faltan archivos para '{clave}', se omite.")
                continue

            print(f"Procesando {clave}...")
            corridas[clave] = {"frames": leer_frames(ruta_frames, transformar)}

            resumen = leer_resumen(ruta_resumen)
            comparativo[nivel][modo] = float(resumen.get("espera_promedio_por_vehiculo_s", 0))
    indices_libres = calcular_indices_libres(corridas, indices_enlace)
    datos = {
        "red": {
            "ancho": CANVAS_ANCHO,
            "alto": CANVAS_ALTO,
            "calles": calles,
            "semaforos": semaforos,
            "enlaces": enlaces,
            "indices_enlace": indices_enlace,
            "angulos_enlace": angulos_enlace,
            "indices_libres": indices_libres,
        },
        "comparativo": comparativo,
        "corridas": corridas,
    }

    os.makedirs(os.path.dirname(SALIDA_JS), exist_ok=True)
    with open(SALIDA_JS, "w", encoding="utf-8") as archivo_js:
        archivo_js.write("// Generado automaticamente por combinar_datos.py — no editar a mano.\n")
        archivo_js.write("const DATOS_SIMULACION = ")
        json.dump(datos, archivo_js, ensure_ascii=False)
        archivo_js.write(";\n")

    print(f"\nListo: {SALIDA_JS}")
    print(f"Corridas incluidas: {list(corridas.keys())}")


if __name__ == "__main__":
    main()
