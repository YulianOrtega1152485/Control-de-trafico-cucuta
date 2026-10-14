"""
capa_integracion.py
Capa de abstraccion (B8) del sistema de control. Define dos interfaces:

  - FuenteDeDatos : de donde salen las mediciones (cola y espera por carril).
  - Actuador      : como se aplican los cambios de fase al semaforo.

Gracias a esto, el algoritmo de decision (`ControladorAdaptativo`) no llama
a `traci.*` directamente: solo habla con estas interfaces. Asi se puede
cambiar la fuente de datos (TraCI "perfecto" o detectores E2 simulados) y el
actuador (TraCI hoy, MQTT despues) sin tocar la logica de decision.
"""

from abc import ABC, abstractmethod
from collections import deque
import json
import time
import traci

# Topicos MQTT (B10). El nodo de borde publica estado y escucha comando.
TOPICO_ESTADO = "interseccion/{tls}/estado"
TOPICO_COMANDO = "interseccion/{tls}/comando"
TOPICO_ACK = "interseccion/{tls}/ack"
TOPICO_CONFIG = "interseccion/{tls}/config"

# Tamano de la ventana movil (segundos) para estimar la espera desde los E2.
# Se guarda 1 muestra por segundo; 'espera' es la suma de los ultimos
# VENTANA_ESPERA segundos, para que refleje la situacion actual y no crezca
# sin limite durante toda la corrida.
VENTANA_ESPERA = 60


# ---------------------------------------------------------------------------
# INTERFAZ DE LECTURA
# ---------------------------------------------------------------------------

class FuenteDeDatos(ABC):
    """De donde el controlador obtiene cola y espera por carril."""

    def paso(self):
        """Se llama una vez por segundo de simulacion. Por defecto, nada."""
        return None

    @abstractmethod
    def cola(self, carril):
        """Vehiculos detenidos (en cola) en el carril, en este instante."""

    @abstractmethod
    def espera(self, carril):
        """Medida de espera acumulada del carril."""

    def disponible(self, carril):
        """
        True si la fuente tiene datos válidos para el carril. Base: siempre
        disponible. Los detectores E2 la sobreescriben para señalar fallas.
        """
        return True


class FuenteTraCI(FuenteDeDatos):
    """
    Lectura directa de TraCI: es la que usa el proyecto desde la Entrega 1.
    Es la referencia "perfecta" (el simulador nos dice todo).
    """

    def cola(self, carril):
        return traci.lane.getLastStepHaltingNumber(carril)

    def espera(self, carril):
        return traci.lane.getWaitingTime(carril)


class FuenteDetectoresE2(FuenteDeDatos):
    """
    Lectura desde los detectores E2 (sensores simulados, B3).

    - cola   : vehiculos en situacion de cola dentro del detector
               (`getJamLengthVehicle`), es decir, detenidos en los ultimos
               metros del carril antes del semaforo.
    - espera : un detector NO mide tiempo de espera, asi que se estima con
               una ventana movil de los ultimos VENTANA_ESPERA segundos:
               se suman los vehiculos detenidos en el detector durante esa
               ventana. Al ir descartando lo viejo, el valor se mantiene
               acotado y refleja la congestion actual (no un acumulado que
               crece toda la corrida).

    Requiere el mapa carril -> id_detector que genera generar_detectores.py.
    """

    def __init__(self, ruta_mapa, ventana_segundos=VENTANA_ESPERA):
        with open(ruta_mapa, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
        self.det_por_carril = datos["por_carril"]
        self.ventana = {
            carril: deque(maxlen=ventana_segundos) for carril in self.det_por_carril
        }
        self.caidos = set()  # carriles cuyo detector está fallando (B5)

    def _detector(self, carril):
        det = self.det_por_carril.get(carril)
        if det is None:
            raise KeyError(
                f"No hay detector E2 para el carril '{carril}'. "
                f"Corre src/generar_detectores.py y revisa mapa_detectores.json."
            )
        return det

    def marcar_falla(self, carril, caido=True):
        """Simula (o repara) la caída del detector de un carril (B5)."""
        if caido:
            self.caidos.add(carril)
        else:
            self.caidos.discard(carril)

    def disponible(self, carril):
        return carril not in self.caidos

    def paso(self):
        for carril, det in self.det_por_carril.items():
            if carril in self.caidos:
                continue  # detector caído: no aporta lecturas
            self.ventana[carril].append(traci.lanearea.getJamLengthVehicle(det))

    def cola(self, carril):
        return traci.lanearea.getJamLengthVehicle(self._detector(carril))

    def espera(self, carril):
        self._detector(carril)  # valida que exista
        return sum(self.ventana[carril])


# ---------------------------------------------------------------------------
# INTERFAZ DE ESCRITURA
# ---------------------------------------------------------------------------

class Actuador(ABC):
    """Como se leen y aplican los estados (R/Y/G) de un semaforo."""

    @abstractmethod
    def estado_actual(self, tls_id):
        """String de estado activo del semaforo (una letra R/Y/G por link)."""

    @abstractmethod
    def aplicar_estado(self, tls_id, estado):
        """Impone el string de estado indicado al semaforo."""


class ActuadorTraCI(Actuador):
    """
    Actuador que escribe directamente por TraCI.

    Se trabaja por ESTADO (no por indice de fase) porque el controlador
    necesita pasar por amarillo y todo-rojo antes de abrir el siguiente
    verde; `setRedYellowGreenState` permite imponer esos estados exactos.
    """

    def estado_actual(self, tls_id):
        return traci.trafficlight.getRedYellowGreenState(tls_id)

    def aplicar_estado(self, tls_id, estado):
        traci.trafficlight.setRedYellowGreenState(tls_id, estado)


class FuenteMQTT(FuenteDeDatos):
    """
    Fuente de datos (B8/B10) que lee el estado de las intersecciones desde
    los mensajes MQTT `interseccion/<tls>/estado`. El nodo de borde los
    publica; aqui solo se guarda el ultimo recibido por semaforo.
    """

    def __init__(self, carril_a_tls):
        self.carril_a_tls = carril_a_tls  # carril -> tls
        self.ultimo = {}                  # tls -> {"cola": {...}, "espera": {...}}

    def actualizar(self, tls_id, datos):
        self.ultimo[tls_id] = datos

    def _dato(self, carril, campo):
        tls_id = self.carril_a_tls.get(carril)
        if tls_id is None:
            return 0
        return (self.ultimo.get(tls_id) or {}).get(campo, {}).get(carril, 0)

    def cola(self, carril):
        return self._dato(carril, "cola")

    def espera(self, carril):
        return self._dato(carril, "espera")

    def disponible(self, carril):
        tls_id = self.carril_a_tls.get(carril)
        return tls_id in self.ultimo


class ActuadorMQTT(Actuador):
    """
    Actuador (B8/B10) que publica el estado deseado en
    `interseccion/<tls>/comando`. El nodo de borde lo aplica por TraCI.
    """

    def __init__(self, cliente, topico=TOPICO_COMANDO):
        self.cliente = cliente
        self.topico = topico
        self.ultimo_estado = {}

    def estado_actual(self, tls_id):
        return self.ultimo_estado.get(tls_id, "")

    def aplicar_estado(self, tls_id, estado):
        self.ultimo_estado[tls_id] = estado
        payload = json.dumps({"tls": tls_id, "estado": estado, "ts": time.time()})
        self.cliente.publish(self.topico.format(tls=tls_id), payload)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def construir_fuente(nombre, ruta_mapa):
    """Crea la fuente de datos segun el nombre pedido ('traci' o 'detectores')."""
    if nombre == "traci":
        return FuenteTraCI()
    if nombre == "detectores":
        return FuenteDetectoresE2(ruta_mapa)
    raise ValueError(f"Fuente de datos desconocida: '{nombre}' (usa 'traci' o 'detectores')")
