import traci

SUMO_BINARY = "sumo"  # o la ruta completa si no está en el PATH
NET_FILE = "sumo/red_guaimaral_libertadores.net.xml.gz"  # el nuevo nombre, ver nota abajo

TLS_IDS = ["a9c2t", "agc2t", "a9c5t", "agc5t", "alc8t", "alc4t", "alc2t"]

traci.start([SUMO_BINARY, "-n", NET_FILE])

for tls_id in TLS_IDS:
    try:
        carriles = traci.trafficlight.getControlledLanes(tls_id)
        carriles_unicos = list(dict.fromkeys(carriles))
        print(f"\nTLS: {tls_id}")
        print(f"Carriles controlados ({len(carriles_unicos)}):")
        for c in carriles_unicos:
            print(f"  - {c}")
    except traci.exceptions.TraCIException:
        print(f"\n⚠️  TLS '{tls_id}' no encontrado en la red — revisen que el ID esté bien escrito en netedit.")

traci.close()