"""
combinar_trips.py
Junta 3 archivos de rutas (carros, motos, buses) de un mismo nivel
de demanda en un solo .rou.xml, ordenado por tiempo de salida (depart).
Soporta tanto formato <trip> como <vehicle> (el que genera randomTrips.py
con el flag -r).
"""

import argparse
import xml.etree.ElementTree as ET

ETIQUETAS_VEHICULO = ("trip", "vehicle")


def combinar(rutas_entrada, ruta_salida):
    raiz_salida = ET.Element("routes")
    vtypes_vistos = set()
    vehiculos = []

    for ruta in rutas_entrada:
        arbol = ET.parse(ruta)
        raiz = arbol.getroot()

        for vtype in raiz.findall("vType"):
            if vtype.get("id") not in vtypes_vistos:
                raiz_salida.append(vtype)
                vtypes_vistos.add(vtype.get("id"))

        for etiqueta in ETIQUETAS_VEHICULO:
            for vehiculo in raiz.findall(etiqueta):
                vehiculos.append(vehiculo)

    if not vehiculos:
        print(f"ADVERTENCIA: no se encontró ningún <trip> ni <vehicle> en {rutas_entrada}")

    vehiculos.sort(key=lambda v: float(v.get("depart")))

    for vehiculo in vehiculos:
        raiz_salida.append(vehiculo)

    arbol_salida = ET.ElementTree(raiz_salida)
    ET.indent(arbol_salida, space="  ")
    arbol_salida.write(ruta_salida, encoding="UTF-8", xml_declaration=True)
    print(f"Listo: {ruta_salida} ({len(vehiculos)} vehículos en total)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archivos", nargs=3, help="los 3 archivos a combinar (carros, motos, buses)")
    parser.add_argument("-o", "--salida", required=True, help="archivo .rou.xml de salida")
    args = parser.parse_args()

    combinar(args.archivos, args.salida)