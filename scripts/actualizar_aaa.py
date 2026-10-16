"""Publica polígonos individuales de productos AAA de la DMC con región administrativa."""
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
PRIORIDAD = {"Aviso": 1, "Alerta": 2, "Alarma": 3}

# Códigos verificados contra los archivos de zonas y regiones de la DMC.
# Las subdivisiones tienen significado propio; no se deben eliminar sus letras.
REGIONES_DMC = {
    "01a": "Arica y Parinacota",
    "01b": "Tarapacá",
    "02": "Antofagasta",
    "03": "Atacama",
    "04": "Coquimbo",
    "05": "Valparaíso",
    "05m": "Metropolitana de Santiago",
    "06": "Libertador General Bernardo O'Higgins",
    "07": "Maule",
    "08a": "Ñuble",
    "08b": "Biobío",
    "09": "La Araucanía",
    "10a": "Los Ríos",
    "10b": "Los Lagos",
    "11": "Aysén del General Carlos Ibáñez del Campo",
    "12": "Magallanes y de la Antártica Chilena",
    "ip": "Valparaíso",  # Isla de Pascua / Rapa Nui
    "jf": "Valparaíso",  # Archipiélago Juan Fernández
}


def descargar_json(sesion, archivo):
    respuesta = sesion.get(BASE + archivo, timeout=60)
    respuesta.raise_for_status()
    return respuesta.json()


def codigo_region(indice, propiedades):
    codigo = propiedades.get("region")
    if codigo is None:
        codigo = str(indice).split("_", 1)[0]
        if codigo.startswith("myCoordinates_"):
            codigo = codigo.removeprefix("myCoordinates_")
    return str(codigo).strip()


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

    indice_geometrico = {}
    codigos_desconocidos = set()
    for entidad in zonas + regiones:
        propiedades = entidad.get("properties") or {}
        indice = str(propiedades.get("indice", "")).strip()
        geometria = entidad.get("geometry")
        if not indice or not geometria:
            continue
        codigo = codigo_region(indice, propiedades)
        region = REGIONES_DMC.get(codigo)
        if region is None:
            codigos_desconocidos.add(codigo)
            continue
        indice_geometrico[indice] = (geometria, region)

    if codigos_desconocidos:
        raise ValueError(
            "Códigos regionales DMC no reconocidos: "
            + ", ".join(sorted(codigos_desconocidos))
        )

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
            for valor in str(evento.get("dataZonaAfecta") or "").split(",")
            if valor.strip()
        ]
        for indice in identificadores:
            encontrado = indice_geometrico.get(indice)
            if encontrado is None:
                sin_geometria.append((codigo, indice))
                continue
            geometria, region = encontrado
            propiedades = {
                "id": identificador,
                "codigoMeteo": codigo,
                "tipo": tipo,
                "fenomeno": evento.get("fenomeno"),
                "titulo": evento.get("titulo"),
                "region": region,
                "region_zona": indice,
                "desde": evento.get("desde"),
                "hasta": evento.get("hasta"),
                "emision": evento.get("emision"),
                "condicionSinoptica": evento.get("condicionSinoptica"),
                "fuente": "DMC",
            }
            resultado.append({
                "type": "Feature",
                "properties": propiedades,
                "geometry": geometria,
            })

    if sin_geometria:
        raise ValueError("Zonas sin geometría: " + str(sin_geometria[:20]))
    if eventos and not resultado:
        raise ValueError("Existen productos AAA sin polígonos")

    resultado.sort(key=lambda entidad: (
        PRIORIDAD[entidad["properties"]["tipo"]],
        str(entidad["properties"]["codigoMeteo"]),
        entidad["properties"]["region_zona"],
    ))
    geojson = {"type": "FeatureCollection", "features": resultado}
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    temporal = SALIDA.with_suffix(".tmp")
    with temporal.open("w", encoding="utf-8") as archivo:
        json.dump(geojson, archivo, ensure_ascii=False, separators=(",", ":"))
    temporal.replace(SALIDA)
    print(f"Productos AAA: {len(eventos)}")
    print(f"Polígonos generados: {len(resultado)}")
    print("Actualización:", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    generar_geojson()
