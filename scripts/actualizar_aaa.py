
import json
from pathlib import Path
from datetime import datetime, timezone

import requests

BASE = "https://archivos.meteochile.gob.cl/portaldmc/AAA/"

ARCHIVOS = {
    "eventos": "datos_AAA.json",
    "zonas": "coordenadas_zonas_DMC_v3.geojson",
    "regiones": "coordenadas_regiones_DMC.geojson",
}

SALIDA = Path("data/eventos_AAA.geojson")

PRIORIDAD = {
    "Aviso": 1,
    "Alerta": 2,
    "Alarma": 3,
}


def descargar_json(sesion, archivo):
    url = BASE + archivo
    respuesta = sesion.get(url, timeout=60)
    respuesta.raise_for_status()
    return respuesta.json()


def generar_geojson():
    with requests.Session() as sesion:
        datos = {
            nombre: descargar_json(sesion, archivo)
            for nombre, archivo in ARCHIVOS.items()
        }

    eventos = datos["eventos"].get("AAA", [])
    zonas = datos["zonas"].get("features", [])
    regiones = datos["regiones"].get("features", [])

    if not isinstance(eventos, list):
        raise ValueError("Listado AAA inválido")

    if not zonas or not regiones:
        raise ValueError("Geometrías DMC vacías")

    # Indexar geometrías mediante el atributo indice.
    indice_geometrico = {}

    for entidad in zonas + regiones:
        propiedades = entidad.get("properties") or {}
        indice = str(propiedades.get("indice", "")).strip()

        if indice and entidad.get("geometry"):
            indice_geometrico[indice] = entidad["geometry"]

    resultado = []
    sin_geometria = []

    for evento in eventos:
        identificador = evento.get("id")
        tipo = evento.get("tipo", "")
        codigo = evento.get("codigoMeteo", "")

        if tipo not in PRIORIDAD:
            raise ValueError(f"Tipo AAA desconocido: {tipo}")

        identificadores = [
            valor.strip()
            for valor in str(
                evento.get("dataZonaAfecta") or ""
            ).split(",")
            if valor.strip()
        ]

        for indice in identificadores:
            geometria = indice_geometrico.get(indice)

            if geometria is None:
                sin_geometria.append((codigo, indice))
                continue

            propiedades = {
                "id": identificador,
                "codigoMeteo": codigo,
                "tipo": tipo,
                "fenomeno": evento.get("fenomeno"),
                "titulo": evento.get("titulo"),
                "region_zona": indice,
                "desde": evento.get("desde"),
                "hasta": evento.get("hasta"),
                "emision": evento.get("emision"),
                "condicionSinoptica": evento.get(
                    "condicionSinoptica"
                ),
                "fuente": "DMC",
            }

            resultado.append({
                "type": "Feature",
                "properties": propiedades,
                "geometry": geometria,
            })

    if sin_geometria:
        raise ValueError(
            "Zonas sin geometría: "
            + str(sin_geometria[:20])
        )

    if eventos and not resultado:
        raise ValueError(
            "Existen productos AAA sin polígonos"
        )

    resultado.sort(
        key=lambda entidad: (
            PRIORIDAD[entidad["properties"]["tipo"]],
            str(entidad["properties"]["codigoMeteo"]),
            entidad["properties"]["region_zona"],
        )
    )

    geojson = {
        "type": "FeatureCollection",
        "features": resultado,
    }

    SALIDA.parent.mkdir(parents=True, exist_ok=True)

    temporal = SALIDA.with_suffix(".tmp")

    with temporal.open("w", encoding="utf-8") as archivo:
        json.dump(
            geojson,
            archivo,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    temporal.replace(SALIDA)

    print(f"Productos AAA: {len(eventos)}")
    print(f"Polígonos generados: {len(resultado)}")
    print(
        "Actualización:",
        datetime.now(timezone.utc).isoformat()
    )


if __name__ == "__main__":
    generar_geojson()
