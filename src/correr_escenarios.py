"""
correr_escenarios.py — Regenera los 6 resultados base (Entrega 2)
=================================================================

Corre las combinaciones {bajo, medio, alto} x {fijo, adaptativo} con la MISMA
semilla (por defecto 42), de modo que la comparación fijo vs adaptativo sea
justa: la demanda de vehículos es idéntica en ambos modos.

Genera en data/resultados_demo/:
    <nivel>_<modo>_frames.csv     (para el mapa de la interfaz)
    <nivel>_<modo>_resumen.csv    (incluye semilla, fuente_datos, violaciones_seguridad)
    <nivel>_<modo>_seguridad.csv  (B1: violaciones, debe quedar vacío)
    <nivel>_<modo>_respaldo.csv   (B5: eventos de modo respaldo)
    <nivel>_<modo>_tripinfo.xml   (métricas por vehículo)

Uso (desde la raíz del proyecto):
    python src/correr_escenarios.py
    python src/correr_escenarios.py --semilla 42 --duracion 1800

Al terminar, para volcarlos a la interfaz:
    python src/combinar_datos.py
"""

import argparse
import time

from control_export import correr

NIVELES = ["bajo", "medio", "alto"]
MODOS = ["fijo", "adaptativo"]


def main():
    parser = argparse.ArgumentParser(description="Regenera las 6 corridas base")
    parser.add_argument("--semilla", type=int, default=42,
                        help="misma semilla para fijo y adaptativo")
    parser.add_argument("--duracion", type=int, default=1800,
                        help="segundos de simulación por corrida")
    parser.add_argument("--fuente", default="traci", choices=["traci", "detectores"])
    parser.add_argument("--niveles", nargs="+", default=NIVELES, choices=NIVELES,
                        help="permite regenerar solo algunos niveles")
    args = parser.parse_args()

    combinaciones = [(n, m) for n in args.niveles for m in MODOS]
    print(f"Corriendo {len(combinaciones)} combinaciones | semilla={args.semilla} "
          f"duración={args.duracion} s | fuente={args.fuente}\n", flush=True)

    inicio = time.time()
    for i, (nivel, modo) in enumerate(combinaciones, 1):
        print(f"[{i}/{len(combinaciones)}] {nivel} · {modo} ...", flush=True)
        t0 = time.time()
        correr(nivel, modo, args.duracion, fuente_datos=args.fuente, semilla=args.semilla)
        print(f"    -> terminado en {time.time() - t0:.1f} s\n", flush=True)

    print(f"Todo listo en {time.time() - inicio:.1f} s.")
    print("Ahora corre:  python src/combinar_datos.py")


if __name__ == "__main__":
    main()
