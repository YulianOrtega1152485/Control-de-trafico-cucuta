"""
monitor_seguridad.py
Monitor de seguridad (B1) del sistema de control.

Verifica, paso a paso y de forma independiente al controlador, que:

  1. ningún verde dure menos que el mínimo ni más que el máximo;
  2. ningún cambio de fase salte el amarillo (ni el intervalo de despeje):
     no puede aparecer un verde nuevo sin haber pasado por una transición;
  3. todo estado observado sea un estado legal del programa (así nunca hay
     dos movimientos conflictivos en verde a la vez).

Lleva un contador de violaciones que debe quedar en CERO.

El monitor no depende de TraCI ni del controlador: recibe el estado observado
de cada semáforo en cada paso. Así sirve igual para el actuador TraCI o para
el futuro actuador MQTT.
"""

import traci


def es_fase_verde(estado):
    """Una fase es 'verde' si tiene algún verde ('G'/'g') y ningún amarillo."""
    e = estado.lower()
    return "g" in e and "y" not in e


def es_fase_amarilla(estado):
    """Una fase es de transición ('amarillo') si tiene algún 'y'."""
    return "y" in estado.lower()


def indices_verdes(estado):
    """Conjunto de índices de link que están en verde ('G'/'g')."""
    return frozenset(i for i, c in enumerate(estado) if c in ("G", "g"))


def capturar_estados_legales(tls_id):
    """
    Lee el programa actual del semáforo y devuelve el conjunto de estados
    legales (verdes, amarillos y todo-rojo). Llamar ANTES de que el
    controlador tome el control del semáforo.
    """
    logica = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    estados = [fase.state for fase in logica.phases]
    n_links = len(estados[0]) if estados else 0
    return {
        "verdes": set(s for s in estados if es_fase_verde(s)),
        "amarillos": set(s for s in estados if es_fase_amarilla(s)),
        "todo_rojo": "r" * n_links,
    }


class MonitorSeguridad:
    """Acumula violaciones de seguridad de todos los semáforos."""

    def __init__(self, estados_legales_por_tls, verde_min, verde_max):
        self.verde_min = verde_min
        self.verde_max = verde_max
        self.info = {}
        for tls_id, legal in estados_legales_por_tls.items():
            legales = set(legal["verdes"]) | set(legal["amarillos"]) | {legal["todo_rojo"]}
            self.info[tls_id] = {
                "legales": legales,
                "prev": None,
                "inicio_verde": None,
            }
        self.violaciones = []          # (tiempo, tls, tipo, detalle)
        self.por_tipo = {}             # tipo -> conteo
        self.por_tls = {}              # tls -> conteo
        self.conteo = 0

    def _registrar(self, tiempo, tls_id, tipo, detalle):
        self.violaciones.append((tiempo, tls_id, tipo, detalle))
        self.por_tipo[tipo] = self.por_tipo.get(tipo, 0) + 1
        self.por_tls[tls_id] = self.por_tls.get(tls_id, 0) + 1
        self.conteo += 1

    def paso(self, tiempo, tls_id, estado):
        info = self.info[tls_id]

        # (3) estado legal / sin conflictos
        if estado not in info["legales"]:
            self._registrar(tiempo, tls_id, "estado_ilegal", estado)

        # (2) transición insegura: aparece un verde nuevo sin amarillo de por medio
        prev = info["prev"]
        if prev is not None and estado != prev:
            nuevos_verdes = indices_verdes(estado) - indices_verdes(prev)
            hubo_verde = bool(indices_verdes(prev))
            sin_transicion = not es_fase_amarilla(prev) and not es_fase_amarilla(estado)
            if hubo_verde and nuevos_verdes and sin_transicion:
                self._registrar(tiempo, tls_id, "transicion_sin_amarillo",
                                f"{prev} -> {estado}")

        # (1) duración de verde (mínimo y máximo)
        if es_fase_verde(estado):
            if info["inicio_verde"] is None:
                info["inicio_verde"] = tiempo
        else:
            if info["inicio_verde"] is not None:
                duracion = tiempo - info["inicio_verde"]
                if duracion < self.verde_min:
                    self._registrar(tiempo, tls_id, "verde_minimo", duracion)
                elif duracion > self.verde_max:
                    self._registrar(tiempo, tls_id, "verde_maximo", duracion)
                info["inicio_verde"] = None

        info["prev"] = estado
