import argparse
import csv
import os
import traci
import xml.etree.ElementTree as ET

from capa_integracion import ActuadorTraCI, FuenteTraCI, construir_fuente

# ---------------------------------------------------------------------------
# CONFIGURACIÓN — lo único que deberían tocar seguido
# ---------------------------------------------------------------------------

SUMO_BINARY = "sumo"  # cambiar a "sumo-gui" para ver la simulación mientras corre
NET_FILE = os.path.join(os.path.dirname(__file__), "..", "sumo", "red_guaimaral_libertadores.net.xml.gz")
CARPETA_ESCENARIOS = os.path.join(os.path.dirname(__file__), "..", "data", "escenarios")
CARPETA_RESULTADOS = os.path.join(os.path.dirname(__file__), "..", "data", "resultados_demo")

# Archivos de la capa de sensores (B3): detectores E2 y su mapa carril->detector
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

# Tiempos fijos (segundos), aplicados por tipo de fase, línea base de comparación
VERDE_FIJO = 25
AMARILLA_FIJA = 3
TODO_ROJO_FIJO = 2

# Parámetros del algoritmo adaptativo (heurística cola + espera)
VERDE_MIN = 10          # nunca corta una fase antes de esto
VERDE_MAX = 45          # nunca extiende una fase más de esto
PESO_COLA = 1.0
PESO_ESPERA = 0.5
INTERVALO_DECISION = 5  # cada cuántos segundos se reevalúa si extender/cambiar de fase

# ---------------------------------------------------------------------------
# UTILIDADES DE PROGRAMA DE SEMÁFORO (fijo)
# ---------------------------------------------------------------------------

def construir_programa_fijo(tls_id):
    """
    Toma el programa de fases que ya existe en la red (los estados R/Y/G
    generados por netconvert) y le impone las duraciones fijas definidas
    arriba, según el tipo de cada fase (si tiene alguna 'G'/'g' es verde,
    si solo tiene 'y' es amarilla, si es todo 'r' es de limpieza).
    """
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
        nuevas_fases.append(
            traci.trafficlight.Phase(duracion, fase.state, duracion, duracion)
        )
    nueva_logica = traci.trafficlight.Logic(
        "fijo", 0, 0, phases=nuevas_fases
    )
    traci.trafficlight.setProgramLogic(tls_id, nueva_logica)


def es_fase_verde(estado):
    """Una fase es 'verde' si tiene algún verde ('G'/'g') y ningún amarillo."""
    e = estado.lower()
    return "g" in e and "y" not in e


def es_fase_amarilla(estado):
    """Una fase es de transición ('amarillo') si tiene algún 'y'."""
    return "y" in estado.lower()


def carriles_por_indice(tls_id):
    """
    Mapea cada índice de link a su carril de entrada (para poder sumar
    cola/espera por fase, no solo por carril suelto).
    """
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
    """
    Controlador por intersección (Opción A: sin coordinación entre semáforos).

    Decisión: elige la fase verde con mayor demanda (cola + espera). Cuando
    decide cambiar o se alcanza VERDE_MAX, el cambio NUNCA salta directo de un
    verde a otro: pasa por AMARILLO y luego TODO-ROJO antes de abrir el
    siguiente verde. Así no hay fases conflictivas (requisito de seguridad B1).

    Escribe por ESTADO (string R/Y/G) usando el actuador, de modo que el
    controlador es dueño total de la secuencia y el programa de la red no la
    pisa.
    """

    VERDE, AMARILLO, TODO_ROJO = "verde", "amarillo", "todo_rojo"

    def __init__(self, tls_id, fuente=None, actuador=None):
        self.tls_id = tls_id
        # Capa de abstraccion (B8): por defecto usa TraCI, igual que antes.
        self.fuente = fuente if fuente is not None else FuenteTraCI()
        self.actuador = actuador if actuador is not None else ActuadorTraCI()

        logica = traci.trafficlight.getAllProgramLogics(tls_id)[0]
        self.estados = [fase.state for fase in logica.phases]
        self.verdes = [i for i, s in enumerate(self.estados) if es_fase_verde(s)]
        self.amarillo_de = {g: self._amarillo_siguiente(g) for g in self.verdes}
        self.carril_por_indice = carriles_por_indice(tls_id)
        self.indices_por_fase = {
            i: [k for k, c in enumerate(self.estados[i]) if c in ("G", "g")]
            for i in self.verdes
        }
        self.todo_rojo = "r" * len(self.estados[0])

        self.fase_actual = self.verdes[0]
        self.estado_control = self.VERDE
        self.tiempo_en_estado = 0
        self.siguiente_verde = None
        self.actuador.aplicar_estado(tls_id, self.estados[self.fase_actual])

    def _amarillo_siguiente(self, verde):
        """Índice de la fase amarilla que sigue al verde dado (cíclico)."""
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
        """Se llama una vez por segundo de simulación."""
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

        # estado_control == VERDE: evaluar si extender o cambiar de fase
        self.tiempo_en_estado += 1
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


# ---------------------------------------------------------------------------
# CORRIDA PRINCIPAL
# ---------------------------------------------------------------------------

def calcular_metricas_por_vehiculo(ruta_tripinfo):
    """
    Lee el archivo tripinfo que genera SUMO (uno por vehículo que
    completó su viaje) y calcula el promedio real de espera y
    duración por vehículo — a diferencia de la métrica de red que
    ya teníamos, esta sí es comparable con la literatura de tránsito.
    """
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

def correr(escenario, modo, duracion_segundos, fuente_datos="traci", semilla=42):
    ruta_rou = os.path.join(CARPETA_ESCENARIOS, f"{escenario}.rou.xml")

    # La fuente 'traci' conserva los nombres de archivo originales; las demas
    # fuentes llevan sufijo para no pisar la linea base.
    sufijo = "" if fuente_datos == "traci" else f"_{fuente_datos}"
    base = f"{escenario}_{modo}{sufijo}"

    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    ruta_frames = os.path.join(CARPETA_RESULTADOS, f"{base}_frames.csv")
    ruta_resumen = os.path.join(CARPETA_RESULTADOS, f"{base}_resumen.csv")
    ruta_tripinfo = os.path.join(CARPETA_RESULTADOS, f"{base}_tripinfo.xml")

    comando = [
        SUMO_BINARY, "-n", NET_FILE, "-r", ruta_rou, "--no-warnings", "true",
        "--tripinfo-output", ruta_tripinfo, "--seed", str(semilla),
    ]
    if fuente_datos == "detectores":
        comando += ["-a", ARCHIVO_DETECTORES]
    traci.start(comando)

    # Capa de abstraccion (B8): una sola fuente, compartida por los 7 controladores.
    fuente = construir_fuente(fuente_datos, MAPA_DETECTORES)

    if modo == "fijo":
        for tls_id in TLS_IDS:
            construir_programa_fijo(tls_id)
        controladores = {}
    elif modo == "adaptativo":
        controladores = {
            tls_id: ControladorAdaptativo(tls_id, fuente=fuente, actuador=ActuadorTraCI())
            for tls_id in TLS_IDS
        }
    else:
        raise ValueError("modo debe ser 'fijo' o 'adaptativo'")

    sumas_espera = []
    sumas_cola = []

    with open(ruta_frames, "w", newline="", encoding="utf-8") as archivo_frames:
        escritor = csv.writer(archivo_frames)
        escritor.writerow(["tiempo", "tipo", "id", "subtipo_o_veh", "x", "y", "estado_semaforo"])

        paso = 0
        while paso < duracion_segundos and traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            fuente.paso()

            if modo == "adaptativo":
                for controlador in controladores.values():
                    controlador.paso()

            registrar_frame(escritor, paso)
            vehiculos, cola, espera = calcular_metricas_instantaneas()
            sumas_cola.append(cola)
            sumas_espera.append(espera)

            paso += 1

    traci.close()

    completados, espera_promedio_veh, duracion_promedio_veh = calcular_metricas_por_vehiculo(ruta_tripinfo)

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

    print(f"Listo: {ruta_frames}")
    print(f"Listo: {ruta_resumen}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--escenario", required=True, choices=["bajo", "medio", "alto"])
    parser.add_argument("--modo", required=True, choices=["fijo", "adaptativo"])
    parser.add_argument("--fuente", default="traci", choices=["traci", "detectores"],
                        help="origen de datos: 'traci' (perfecto) o 'detectores' (E2)")
    parser.add_argument("--semilla", type=int, default=42,
                        help="semilla aleatoria de SUMO para corridas reproducibles")
    parser.add_argument("--duracion", type=int, default=1800, help="segundos de simulación (no de generación de vehículos)")
    args = parser.parse_args()

    correr(args.escenario, args.modo, args.duracion, args.fuente, args.semilla)