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
import traci

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
