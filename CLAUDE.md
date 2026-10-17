# Contexto del proyecto SmartJunction-SUMO (para continuar en un chat nuevo)

Seminario Integrador II, Ingeniería de Sistemas, Universidad Francisco de Paula Santander (UFPS), Cúcuta, Colombia. 2026.
Integrantes: María José Osorio Ovallos, Yulian Andrés Ortega Machado, Bryan Sneider Gelvez. Docente: Carlos Pardo.
Quien escribe es el **Integrante 2** (lógica de control y TraCI). Integrante 1 = SUMO/NetEdit. Integrante 3 = interfaz, datos y documentación.

## 1. Qué es el proyecto

Simulación y control adaptativo de semáforos en una red de **7 intersecciones** del corredor **Avenida Guaimaral - Avenida Libertadores** (zona norte de Cúcuta, cerca del INEM José Eusebio Caro). Un algoritmo heurístico ajusta el tiempo de verde según la cola y el tiempo de espera de cada carril, y se compara contra un esquema de tiempo fijo.

- Cada intersección se controla de forma **independiente** (Opción A). No hay coordinación tipo "onda verde".
- No hay cámaras ni visión artificial en el alcance. Los datos salen del simulador.
- IDs de los 7 semáforos (TLS): `a9c2t`, `agc2t`, `a9c5t`, `agc5t`, `alc8t`, `alc4t`, `alc2t`.
- Línea base de tiempo fijo (supuesto del equipo): 25 s verde, 3 s amarillo, 2 s todo-rojo.
- Escenarios: demanda baja, media y alta, más uno variable. Se generan con `randomTrips.py` sobre una sola red exportada con OSM Web Wizard.
- Diseño experimental: 2 esquemas (fijo, adaptativo) x escenarios, mínimo 5 semillas por combinación.

## 2. Estructura real del repositorio (VS Code, carpeta raíz `control-de-tra...`)

```
.vscode/
config/
data/
  escenarios/
  resultados_demo/
interfaz/
  image/
  datos.js
  index.html
src/
  combinar_datos.py
  combinar_trips.py
  control_export.py          <- controlador real (adaptativo + fijo)
  generar_detectores.py      <- genera detectores E2 (ya ejecutado)
  capa_integracion.py        <- B3/B8: interfaces FuenteDeDatos/Actuador + E2
  monitor_seguridad.py       <- B1: monitor de violaciones
  nodo_mqtt.py               <- B10: nodo de borde MQTT
  control_remoto.py          <- B10: centro de control remoto
  correr_escenarios.py       <- automatiza corridas
  listar_carriles_tlc.py
sumo/
  osm.poly.xml.gz
  osm.sumocfg
  osm.view.xml
  red_guaimaral_libertadores.net.xml.gz
.gitignore
requimientos.txt             <- tiene typo, conviene renombrar a requirements.txt
trips.trips.xml              <- suelto en la raíz
```

La interfaz es una página HTML + JavaScript con canvas (no Streamlit): selector de nivel de tráfico (bajo/medio/alto), modo de control (tiempo fijo/adaptativo) y métricas en vivo (vehículos activos, cola total, espera promedio).

## 3. Estado actual

**Hecho (Entrega 1):** red de 7 intersecciones en SUMO, escenarios de demanda, controlador fijo y adaptativo, interfaz web con estado en vivo, primera comparación fijo vs adaptativo.

**Hecho (detectores E2):** `src/generar_detectores.py` ya fue ejecutado. Genera `sumo/detectores.add.xml` y `sumo/mapa_detectores.json`. `sumo/osm.sumocfg` incluye `additional-files`.

**Hecho (capa de integración):** `src/capa_integracion.py` (ABCs, `FuenteTraCI`, `FuenteDetectoresE2`, `ActuadorTraCI`, `FuenteMQTT`, `ActuadorMQTT`).

**Hecho (B1):** `src/monitor_seguridad.py` integrado en `control_export.py` (0 violaciones en las corridas).

**Hecho (B5):** soporte de falla simulada con `--falla-tls`.

**Hecho (B10):** `src/nodo_mqtt.py`, `src/control_remoto.py`, `mqtt/mosquitto.conf`, demo en `data/resultados_demo/demo_mqtt.csv` (latencia ~28 ms).

## 4. Qué se quiere hacer ahora (Entrega 2, semana 13, demo en vivo)

El profesor pidió mostrar **cómo se integraría el sistema a los semáforos reales de Cúcuta**, simulando la arquitectura de integración y no solo el tráfico. Lo que hay que implementar, en este orden:

1. **Detectores E2 (B3)** — hecho.
2. **Capa de abstracción (B8)** — hecho.
3. **Monitor de seguridad (B1)** — hecho.
4. **Modo de respaldo y falla simulada (B5)** — hecho.
5. **Nodo MQTT (B10)** — hecho.
6. **Plan de Webster (B2):** pendiente (segunda línea base con método de Webster, comparar adaptativo vs 25/3/2 vs Webster).

Fuera de esta entrega: análisis de sensibilidad con ruido (B4), pruebas estadísticas sobre todas las corridas (B9), modo sombra, tráfico mixto y maqueta física.

## 5. Entregables formales de la Entrega 2

- Sistema funcionando: 7 intersecciones, control adaptativo e interfaz, con comparación fijo vs adaptativo en vivo (misma semilla).
- Capa de integración demostrada (detectores, abstracción, MQTT).
- Plan de integración real documentado: qué preguntarle al STMC (tipo de controlador, si acepta comandos externos), fases (levantamiento, recalibración, modo sombra, piloto, escalado) y riesgos.
- Tabla de resultados preliminares.
- Seguridad demostrada: contador de violaciones en cero y falla con reversión a plan fijo.

## 6. Cómo quiero que trabajes

- Responde en español, simple y directo. El profesor pidió "sin carreta": herramientas y pasos concretos, nada genérico.
- Antes de modificar código, pídeme el archivo actual y léelo. No asumas su contenido.
- Un paso a la vez. Entrega cada pieza como archivo `.py` listo para copiar en `src/`, con las rutas de mi estructura real.
- No tienes SUMO instalado, así que dime claramente qué probaste y qué no. Yo corro las pruebas con SUMO.
- No cambies la lógica que ya funciona en `control_export.py` sin avisarme.
- Pregúntame mi sistema operativo antes de darme comandos de instalación de Mosquitto.

## 7. Primer paso en el chat nuevo

Te voy a subir `src/control_export.py`, `sumo/osm.sumocfg` y `src/generar_detectores.py`. Léelos, dime cómo está estructurado el control actual y cómo propones construir `FuenteDeDatos` y `Actuador` encima de ese código sin romperlo.

## 8. Herramientas usadas

SUMO, OSM Web Wizard, NetEdit, TraCI, sumolib, Python 3, detectores E2 de SUMO, Mosquitto, paho-mqtt, HTML + JavaScript (canvas), Pandas, Matplotlib/Plotly, Git y GitHub.
