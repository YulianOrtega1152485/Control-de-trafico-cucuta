import argparse
import csv
import os
import traci
import xml.etree.ElementTree as ET

from capa_integracion import ActuadorTraCI, FuenteDetectoresE2, FuenteTraCI, construir_fuente
from monitor_seguridad import (
    MonitorSeguridad,
    capturar_estados_legales,
    es_fase_amarilla,
    es_fase_verde,
)

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

SUMO_BINARY = "sumo"  # cambiar a "sumo-gui" para ver la simulación
NET_FILE = os.path.join(os.path.dirname(__file__), "..", "sumo", "red_guaimaral_libertadores.net.xml.gz")
CARPETA_ESCENARIOS = os.path.join(os.path.dirname(__file__), "..", "data", "escenarios")
CARPETA_RESULTADOS = os.path.join(os.path.dirname(__file__), "..", "data", "resultados_demo")

ARCHIVO_DETECTORES = os.path.join(os.path.dirname(__file__), "..", "sumo", "detectores.add.xml")
MAPA_DETECTORES = os.path.join(os.path.dirname(__file__), "..", "sumo", "mapa_detectores.json")

TLS_IDS = ["a9c2t", "agc2t", "a9c5t", "agc5t", "alc8t", "alc4t", "alc2t"]

CARRILES = {
    "a9c2t": ["287898717#8_0", "287898717#8_1", "287901583#0_0", "287901583#0_1",
              "1051311974#2_0", "1051311974#2_1"],
    "agc2t": ["287898717#4_0", "287898717#4_1", "287901583#3_0", "287901583#3_1",
              "308249260#9_0", "308249260#9_1"],
    "a9c5t": ["1110048981_0", "1110048981_1", "808489157_0",
              "1051311973#3_0", "1051311973#3_1", "303916290#1_0"],
    "agc5t": ["176719622#6_0", "176719622#6_1", "308244808#1_0", "308244808#1_1",
              "317004236#2_0", "317004236#2_1", "308249260#6_0", "308249260#6_1"],
    "alc8t": ["305172115#1_0", "304831008#1_0", "304831008#1_1",
              "1051320860_0", "30710372#0_0", "30710372#0_1"],
    "alc4t": ["806535308#1_0", "1046571975_0", "1046571975_1", "1046571975_2",
              "305162658#7_0", "305162658#7_1", "30710372#9_0", "30710372#9_1"],
    "alc2t": ["69605068_0", "1050372505_0", "1050372505_1",
              "287901583#6_0", "287901583#6_1", "1050372500#3_0", "1050372500#3_1"],
}

VERDE_FIJO = 25
AMARILLA_FIJA = 3
TODO_ROJO_FIJO = 2

# Parámetros Adaptativos
VERDE_MIN = 10
VERDE_MAX = 45
PESO_COLA = 1.0
PESO_ESPERA = 0.5
INTERVALO_DECISION = 5

# ---------------------------------------------------------------------------
# LÓGICA PLAN DE WEBSTER (B2)
# ---------------------------------------------------------------------------

def calcular_tiempos_webster(tls_id, fuente):
    """
    Calcula el tiempo de ciclo óptimo y la distribución de verde según Webster.
    C = (1.5L + 5) / (1 - Y)
    """
    # 1. Definir parámetros básicos
    SATURACION = 1800 / 3600 # 1800 veh/hora = 0.5 veh/seg
    L = (AMARILLA_FIJA + TODO_ROJO_FIJO) * 2 # Supuesto: 2 fases críticas

    logica = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    verdes = [i for i, fase in enumerate(logica.phases) if es_fase_verde(fase.state)]

    # 2. Estimar flujo crítico q por fase (usando la cola actual como proxy de demanda)
    # En un caso real, q sería el volumen horario. Aquí usamos el estado actual del simulador.
    flujos_criticos = []
    for v in verdes:
        # Sumamos la cola de los carriles de esa fase
        cola_fase = 0
        enlaces = traci.trafficlight.getControlledLinks(tls_id)
        for indice, conexiones in enumerate(enlaces):
            if conexiones and any(c in ("G", "g") for c in logica.phases[v].state[indice if indice < len(logica.phases[v].state) else 0]):
                carril = conexiones[0][0]
                cola_fase += fuente.cola(carril)

        # Convertimos cola a un flujo estimado (simplificación para la demo)
        q = max(0.01, cola_fase * 0.1)
        flujos_criticos.append(q)

    # 3. Calcular Y (Suma de razones de flujo crítico)
    Y = sum([q / SATURACION for q in flujos_criticos])
    Y = min(0.9, Y) # Evitar división por cero o ciclos infinitos

    # 4. Ciclo Óptimo C
    ciclo_optimo = (1.5 * L + 5) / (1 - Y)
    ciclo_optimo = max(60, min(120, ciclo_optimo)) # Limitar entre 60s y 120s

    # 5. Distribución del verde total
    verde_total = ciclo_optimo - L
    distribucion_verde = {}

    for i, q in enumerate(flujos_criticos):
        proporcion = q / sum(flujos_criticos)
        distribucion_verde[verdes[i]] = int(proporcion * verde_total)

    return distribucion_verde

def construir_programa_webster(tls_id, fuente):
    """Aplica los tiempos de Webster al semáforo."""
    tiempos_verde = calcular_tiempos_webster(tls_id, fuente)
    logica_actual = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    nuevas_fases = []

    for i, fase in enumerate(logica_actual.phases):
        estado = fase.state.lower()
        if "g" in estado:
            duracion = tiempos_verde.get(i, VERDE_FIJO)
        elif "y" in estado:
            duracion = AMARILLA_FIJA
        else:
            duracion = TODO_ROJO_FIJO
        nuevas_fases.append(traci.trafficlight.Phase(duracion, fase.state, duracion, duracion))

    nueva_logica = traci.trafficlight.Logic("webster", 0, 0, phases=nuevas_fases)
    traci.trafficlight.setProgramLogic(tls_id, nueva_logica)

# ---------------------------------------------------------------------------
# UTILIDADES PROGRAMA FIJO
# ---------------------------------------------------------------------------

def construir_programa_fijo(tls_id):
    logica_actual = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    nuevas_fases = []
    for fase in logica_actual.phases:
        estado = fase.state.lower()
        if "g" in estado:
            duracion = VERDE_FIJO
        elif "y" in estado:
            duracion = AMARILLA_FIJA
        else:
            duracion = TODO_ROJO_FIJO
        nuevas_fases.append(traci.trafficlight.Phase(duracion, fase.state, duracion, duracion))
    nueva_logica = traci.trafficlight.Logic("fijo", 0, 0, phases=nuevas_fases)
    traci.trafficlight.setProgramLogic(tls_id, nueva_logica)

def carriles_por_indice(tls_id):
    enlaces = traci.trafficlight.getControlledLinks(tls_id)
    mapa = {}
    for indice, conexiones in enumerate(enlaces):
        if conexiones:
            carril_entrada = conexiones[0][0]
            mapa[indice] = carril_entrada
    return mapa

# ---------------------------------------------------------------------------
# CONTROL ADAPTATIVO
# ---------------------------------------------------------------------------

class ControladorAdaptativo:
    VERDE, AMARILLO, TODO_ROJO = "verde", "amarillo", "todo_rojo"

    def __init__(self, tls_id, fuente=None, actuador=None,
                 estados_programa=None, carril_por_indice=None):
        self.tls_id = tls_id
        self.fuente = fuente if fuente is not None else FuenteTraCI()
        self.actuador = actuador if actuador is not None else ActuadorTraCI()

        if estados_programa is None:
            logica = traci.trafficlight.getAllProgramLogics(tls_id)[0]
            estados_programa = [fase.state for fase in logica.phases]
        self.estados = list(estados_programa)
        self.verdes = [i for i, s in enumerate(self.estados) if es_fase_verde(s)]
        self.amarillo_de = {g: self._amarillo_siguiente(g) for g in self.verdes}
        self.carril_por_indice = carril_por_indice if carril_por_indice is not None else carriles_por_indice(tls_id)
        self.indices_por_fase = {
            i: [k for k, c in enumerate(self.estados[i]) if c in ("G", "g")]
            for i in self.verdes
        }
        self.todo_rojo = "r" * len(self.estados[0])

        self.fase_actual = self.verdes[0]
        self.estado_control = self.VERDE
        self.tiempo_en_estado = 0
        self.siguiente_verde = None
        self.carriles_tls = sorted(set(self.carril_por_indice.values()))
        self.en_respaldo = False
        self.actuador.aplicar_estado(tls_id, self.estados[self.fase_actual])

    def _fuente_completa(self):
        return all(self.fuente.disponible(carril) for carril in self.carriles_tls)

    def _siguiente_verde_ciclico(self):
        posicion = self.verdes.index(self.fase_actual)
        return self.verdes[(posicion + 1) % len(self.verdes)]

    def _amarillo_siguiente(self, verde):
        n = len(self.estados)
        for avance in range(1, n + 1):
            j = (verde + avance) % n
            if es_fase_amarilla(self.estados[j]):
                return j
        return verde

    def _demanda_de_fase(self, verde):
        total = 0.0
        for indice in self.indices_por_fase[verde]:
            carril = self.carril_por_indice.get(indice)
            if carril is None:
                continue
            cola = self.fuente.cola(carril)
            espera = self.fuente.espera(carril)
            total += PESO_COLA * cola + PESO_ESPERA * espera
        return total

    def _entrar_amarillo(self, siguiente_verde):
        self.siguiente_verde = siguiente_verde
        self.estado_control = self.AMARILLO
        self.tiempo_en_estado = 0
        self.actuador.aplicar_estado(self.tls_id, self.estados[self.amarillo_de[self.fase_actual]])

    def _entrar_todo_rojo(self):
        self.estado_control = self.TODO_ROJO
        self.tiempo_en_estado = 0
        self.actuador.aplicar_estado(self.tls_id, self.todo_rojo)

    def _entrar_verde(self, verde):
        self.fase_actual = verde
        self.estado_control = self.VERDE
        self.tiempo_en_estado = 0
        self.siguiente_verde = None
        self.actuador.aplicar_estado(self.tls_id, self.estados[verde])

    def paso(self):
        self.en_respaldo = not self._fuente_completa()

        if self.estado_control == self.AMARILLO:
            self.tiempo_en_estado += 1
            if self.tiempo_en_estado >= AMARILLA_FIJA:
                self._entrar_todo_rojo()
            return

        if self.estado_control == self.TODO_ROJO:
            self.tiempo_en_estado += 1
            if self.tiempo_en_estado >= TODO_ROJO_FIJO:
                self._entrar_verde(self.siguiente_verde)
            return

        self.tiempo_en_estado += 1

        if self.en_respaldo:
            if self.tiempo_en_estado >= VERDE_FIJO:
                self._entrar_amarillo(self._siguiente_verde_ciclico())
            return

        if self.tiempo_en_estado < VERDE_MIN:
            return
        if self.tiempo_en_estado % INTERVALO_DECISION != 0 and self.tiempo_en_estado < VERDE_MAX:
            return

        demanda_actual = self._demanda_de_fase(self.fase_actual)
        mejor_verde, mejor_demanda = self.fase_actual, demanda_actual
        for candidata in self.verdes:
            if candidata == self.fase_actual:
                continue
            d = self._demanda_de_fase(candidata)
            if d > mejor_demanda:
                mejor_verde, mejor_demanda = candidata, d

        forzar_cambio = self.tiempo_en_estado >= VERDE_MAX
        if forzar_cambio or (mejor_verde != self.fase_actual and mejor_demanda > demanda_actual):
            self._entrar_amarillo(mejor_verde)

# ---------------------------------------------------------------------------
# MÉTRICAS Y EXPORTACIÓN
# ---------------------------------------------------------------------------

def registrar_frame(escritor_frames, t):
    for veh_id in traci.vehicle.getIDList():
        x, y = traci.vehicle.getPosition(veh_id)
        tipo = traci.vehicle.getTypeID(veh_id)
        escritor_frames.writerow([t, "vehiculo", veh_id, tipo, round(x, 2), round(y, 2), ""])
    for tls_id in TLS_IDS:
        estado = traci.trafficlight.getRedYellowGreenState(tls_id)
        escritor_frames.writerow([t, "semaforo", tls_id, "", "", "", estado])

def calcular_metricas_instantaneas():
    total_cola = 0
    total_espera = 0.0
    total_vehiculos = len(traci.vehicle.getIDList())
    for carriles in CARRILES.values():
        for carril in carriles:
            total_cola += traci.lane.getLastStepHaltingNumber(carril)
            total_espera += traci.lane.getWaitingTime(carril)
    return total_vehiculos, total_cola, total_espera

def calcular_metricas_por_vehiculo(ruta_tripinfo):
    arbol = ET.parse(ruta_tripinfo)
    raiz = arbol.getroot()
    esperas = []
    duraciones = []
    for info_viaje in raiz.findall("tripinfo"):
        esperas.append(float(info_viaje.get("waitingTime")))
        duraciones.append(float(info_viaje.get("duration")))
    completados = len(esperas)
    espera_promedio = sum(esperas) / completados if completados else 0
    duracion_promedio = sum(duraciones) / completados if completados else 0
    return completados, espera_promedio, duracion_promedio

# ---------------------------------------------------------------------------
# CORRIDA PRINCIPAL
# ---------------------------------------------------------------------------

def correr(escenario, modo, duracion_segundos, fuente_datos="traci", semilla=42,
           falla_tls=None, falla_desde=60, falla_hasta=120):
    ruta_rou = os.path.join(CARPETA_ESCENARIOS, f"{escenario}.rou.xml")

    sufijo = "" if fuente_datos == "traci" else f"_{fuente_datos}"
    base = f"{escenario}_{modo}{sufijo}"

    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    ruta_frames = os.path.join(CARPETA_RESULTADOS, f"{base}_frames.csv")
    ruta_resumen = os.path.join(CARPETA_RESULTADOS, f"{base}_resumen.csv")
    ruta_tripinfo = os.path.join(CARPETA_RESULTADOS, f"{base}_tripinfo.xml")
    ruta_seguridad = os.path.join(CARPETA_RESULTADOS, f"{base}_seguridad.csv")
    ruta_respaldo = os.path.join(CARPETA_RESULTADOS, f"{base}_respaldo.csv")

    comando = [
        SUMO_BINARY, "-n", NET_FILE, "-r", ruta_rou, "--no-warnings", "true",
        "--tripinfo-output", ruta_tripinfo, "--seed", str(semilla),
    ]
    if fuente_datos == "detectores":
        comando += ["-a", ARCHIVO_DETECTORES]
    traci.start(comando)

    estados_legales = {tls_id: capturar_estados_legales(tls_id) for tls_id in TLS_IDS}
    monitor = MonitorSeguridad(estados_legales, VERDE_MIN, VERDE_MAX)
    fuente = construir_fuente(fuente_datos, MAPA_DETECTORES)

    if modo == "fijo":
        for tls_id in TLS_IDS:
            construir_programa_fijo(tls_id)
        controladores = {}
    elif modo == "webster":
        for tls_id in TLS_IDS:
            construir_programa_webster(tls_id, fuente)
        controladores = {}
    elif modo == "adaptativo":
        controladores = {
            tls_id: ControladorAdaptativo(tls_id, fuente=fuente, actuador=ActuadorTraCI())
            for tls_id in TLS_IDS
        }
    else:
        raise ValueError("modo debe ser 'fijo', 'webster' o 'adaptativo'")

    sumas_espera = []
    sumas_cola = []
    eventos_respaldo = []
    respaldo_prev = {tls_id: False for tls_id in controladores}

    with open(ruta_frames, "w", newline="", encoding="utf-8") as archivo_frames:
        escritor = csv.writer(archivo_frames)
        escritor.writerow(["tiempo", "tipo", "id", "subtipo_o_veh", "x", "y", "estado_semaforo"])

        paso = 0
        while paso < duracion_segundos and traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            fuente.paso()

            if falla_tls is not None and isinstance(fuente, FuenteDetectoresE2):
                caido = falla_desde <= paso < falla_hasta
                for carril in CARRILES[falla_tls]:
                    fuente.marcar_falla(carril, caido)

            if modo == "adaptativo":
                for controlador in controladores.values():
                    controlador.paso()

            registrar_frame(escritor, paso)
            for tls_id in TLS_IDS:
                monitor.paso(paso, tls_id, traci.trafficlight.getRedYellowGreenState(tls_id))

            for tls_id, controlador in controladores.items():
                if controlador.en_respaldo != respaldo_prev[tls_id]:
                    evento = "entra_respaldo" if controlador.en_respaldo else "sale_respaldo"
                    eventos_respaldo.append((paso, tls_id, evento))
                    respaldo_prev[tls_id] = controlador.en_respaldo

            vehiculos, cola, espera = calcular_metricas_instantaneas()
            sumas_cola.append(cola)
            sumas_espera.append(espera)

            paso += 1

    traci.close()

    completados, espera_promedio_veh, duracion_promedio_veh = calcular_metricas_por_vehiculo(ruta_tripinfo)

    with open(ruta_seguridad, "w", newline="", encoding="utf-8") as archivo_seguridad:
        escritor_seg = csv.writer(archivo_seguridad)
        escritor_seg.writerow(["tiempo", "tls", "tipo", "detalle"])
        for violacion in monitor.violaciones:
            escritor_seg.writerow(violacion)

    with open(ruta_respaldo, "w", newline="", encoding="utf-8") as archivo_respaldo:
        escritor_resp = csv.writer(archivo_respaldo)
        escritor_resp.writerow(["tiempo", "tls", "evento"])
        for evento in eventos_respaldo:
            escritor_resp.writerow(evento)

    with open(ruta_resumen, "w", newline="", encoding="utf-8") as archivo_resumen:
        escritor = csv.writer(archivo_resumen)
        escritor.writerow(["metrica", "valor"])
        escritor.writerow(["escenario", escenario])
        escritor.writerow(["modo", modo])
        escritor.writerow(["fuente_datos", fuente_datos])
        escritor.writerow(["semilla", semilla])
        escritor.writerow(["duracion_simulada_s", paso])
        escritor.writerow(["cola_promedio_red", sum(sumas_cola) / len(sumas_cola) if sumas_cola else 0])
        escritor.writerow(["espera_acumulada_red_promedio", sum(sumas_espera) / len(sumas_espera) if sumas_espera else 0])
        escritor.writerow(["vehiculos_completados", completados])
        escritor.writerow(["espera_promedio_por_vehiculo_s", espera_promedio_veh])
        escritor.writerow(["duracion_viaje_promedio_s", duracion_promedio_veh])
        escritor.writerow(["violaciones_seguridad", monitor.conteo])
        escritor.writerow(["tls_con_falla", falla_tls if falla_tls else ""])
        escritor.writerow(["eventos_respaldo", len(eventos_respaldo)])

    print(f"Listo: {ruta_frames}")
    print(f"Listo: {ruta_resumen}")
    print(f"Violaciones de seguridad (B1): {monitor.conteo}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--escenario", required=True, choices=["bajo", "medio", "alto"])
    parser.add_argument("--modo", required=True, choices=["fijo", "webster", "adaptativo"])
    parser.add_argument("--fuente", default="traci", choices=["traci", "detectores"],
                        help="origen de datos: 'traci' (perfecto) o 'detectores' (E2)")
    parser.add_argument("--semilla", type=int, default=42,
                        help="semilla aleatoria de SUMO para corridas reproducibles")
    parser.add_argument("--duracion", type=int, default=1800, help="segundos de simulación")
    parser.add_argument("--falla-tls", default=None, choices=TLS_IDS,
                        help="TLS al que se le simula falla de detectores (B5)")
    parser.add_argument("--falla-desde", type=int, default=60)
    parser.add_argument("--falla-hasta", type=int, default=120)
    args = parser.parse_args()

    if args.falla_tls is not None and args.fuente != "detectores":
        args.falla_tls = None

    correr(args.escenario, args.modo, args.duracion, args.fuente, args.semilla,
           args.falla_tls, args.falla_desde, args.falla_hasta)
