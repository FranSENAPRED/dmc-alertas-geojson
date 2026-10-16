"""Publica geometrías individuales de productos AAA de la DMC.

- tipoZonaAfecta='zonas': relaciona dataZonaAfecta con properties.indice.
- tipoZonaAfecta='area': interpreta pares longitud|latitud y construye un polígono.
- Mantiene los atributos originales y no modifica las geometrías oficiales de zonas.
"""
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://archivos.meteochile.gob.cl/portaldmc/AAA/"
ARCHIVOS = {
    "eventos": "datos_AAA.json",
    "zonas": "coordenadas_zonas_DMC_v3.geojson",
    "regiones": "coordenadas_regiones_DMC.geojson",
}
SALIDA = Path("data/eventos_AAA.geojson")
PRIORIDAD = {"Aviso": 1, "Alerta": 2, "Alarma": 3}
REGIONES_DMC = {
    "01a": "Arica y Parinacota", "01b": "Tarapacá", "02": "Antofagasta",
    "03": "Atacama", "04": "Coquimbo", "05": "Valparaíso",
    "05m": "Metropolitana de Santiago",
    "06": "Libertador General Bernardo O'Higgins", "07": "Maule",
    "08a": "Ñuble", "08b": "Biobío", "09": "La Araucanía",
    "10a": "Los Ríos", "10b": "Los Lagos",
    "11": "Aysén del General Carlos Ibáñez del Campo",
    "12": "Magallanes y de la Antártica Chilena",
    "ip": "Valparaíso", "jf": "Valparaíso",
}

# Nombre breve y orden geográfico de norte a sur (1 a 16).
REGIONES_ABREVIADAS = {
    "Arica y Parinacota": ("Arica y Parinacota", 1),
    "Tarapacá": ("Tarapacá", 2),
    "Antofagasta": ("Antofagasta", 3),
    "Atacama": ("Atacama", 4),
    "Coquimbo": ("Coquimbo", 5),
    "Valparaíso": ("Valparaíso", 6),
    "Metropolitana de Santiago": ("Metropolitana", 7),
    "Libertador General Bernardo O'Higgins": ("O'Higgins", 8),
    "Maule": ("Maule", 9),
    "Ñuble": ("Ñuble", 10),
    "Biobío": ("Biobío", 11),
    "La Araucanía": ("La Araucanía", 12),
    "Los Ríos": ("Los Ríos", 13),
    "Los Lagos": ("Los Lagos", 14),
    "Aysén del General Carlos Ibáñez del Campo": ("Aysén", 15),
    "Magallanes y de la Antártica Chilena": ("Magallanes", 16),
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def descargar_json(sesion, archivo):
    respuesta = sesion.get(BASE + archivo, timeout=60)
    respuesta.raise_for_status()
    return respuesta.json()


def codigo_region(indice, propiedades):
    codigo = propiedades.get("region")
    if codigo is not None and str(codigo).strip() in REGIONES_DMC:
        return str(codigo).strip()
    return indice.split("_", 1)[0].strip()


def poligono_area(texto):
    """Lee dataZonaAfecta como 'longitud|latitud,longitud|latitud,...'."""
    puntos = []
    for parte in str(texto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        componentes = parte.split("|")
        if len(componentes) != 2:
            raise ValueError(f"Coordenada de área inválida: {parte!r}")
        lon, lat = (float(v.strip()) for v in componentes)
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"Coordenada fuera de rango: {parte!r}")
        puntos.append([lon, lat])
    if len(set(map(tuple, puntos))) < 3:
        raise ValueError("El área requiere al menos tres vértices diferentes")
    if puntos[0] != puntos[-1]:
        puntos.append(puntos[0])
    return {"type": "Polygon", "coordinates": [puntos]}


def punto_en_anillo(punto, anillo):
    x, y = punto
    dentro = False
    for i in range(len(anillo)):
        x1, y1 = anillo[i - 1][:2]
        x2, y2 = anillo[i][:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


def punto_en_geometria(punto, geometria):
    tipo = geometria.get("type")
    coordenadas = geometria.get("coordinates") or []
    poligonos = [coordenadas] if tipo == "Polygon" else coordenadas if tipo == "MultiPolygon" else []
    return any(
        poligono and punto_en_anillo(punto, poligono[0])
        and not any(punto_en_anillo(punto, hueco) for hueco in poligono[1:])
        for poligono in poligonos
    )


def region_para_area(geometria, regiones):
    """Solo asigna región cuando TODOS los vértices del área caen en la misma región.

    Deja region=None en áreas transregionales, marítimas o sin cobertura.
    """
    vertices = geometria["coordinates"][0][:-1]
    nombres = set()
    for punto in vertices:
        encontrados = {
            nombre for geo, nombre in regiones if punto_en_geometria(punto, geo)
        }
        if len(encontrados) != 1:
            return None
        nombres.update(encontrados)
    return next(iter(nombres)) if len(nombres) == 1 else None


def generar_geojson():
    with requests.Session() as sesion:
        datos = {clave: descargar_json(sesion, archivo) for clave, archivo in ARCHIVOS.items()}

    eventos = datos["eventos"].get("AAA", [])
    zonas = datos["zonas"].get("features", [])
    regiones_fuente = datos["regiones"].get("features", [])
    if not isinstance(eventos, list):
        raise ValueError("Listado AAA inválido")
    if not zonas or not regiones_fuente:
        raise ValueError("Geometrías DMC vacías")

    indice_geometrico = {}
    regiones_geometricas = []
    desconocidos = set()
    for entidad, es_region in [(e, False) for e in zonas] + [(e, True) for e in regiones_fuente]:
        propiedades = entidad.get("properties") or {}
        indice = str(propiedades.get("indice") or "").strip()
        geometria = entidad.get("geometry")
        if not indice or not geometria:
            continue
        codigo = codigo_region(indice, propiedades)
        nombre_region = REGIONES_DMC.get(codigo)
        if nombre_region is None:
            desconocidos.add(codigo)
            continue
        if indice in indice_geometrico:
            logging.warning("Índice duplicado en catálogos DMC: %s", indice)
        indice_geometrico[indice] = (geometria, nombre_region)
        if es_region:
            regiones_geometricas.append((geometria, nombre_region))
    if desconocidos:
        logging.warning("Códigos DMC no reconocidos (omitidos): %s", ", ".join(sorted(desconocidos)))

    resultado = []
    faltantes = []
    conteo = Counter()
    for evento in eventos:
        tipo = str(evento.get("tipo") or "").strip()
        if tipo not in PRIORIDAD:
            logging.warning("Evento omitido por tipo desconocido: %r", tipo)
            continue
        codigo = evento.get("codigoMeteo")
        modalidad = str(evento.get("tipoZonaAfecta") or "zonas").strip().lower()
        data_zonas = evento.get("dataZonaAfecta")
        elementos = []
        if modalidad == "zonas":
            for indice in (x.strip() for x in str(data_zonas or "").split(",")):
                if not indice:
                    continue
                encontrado = indice_geometrico.get(indice)
                if encontrado is None:
                    faltantes.append((codigo, indice))
                    continue
                geometria, region = encontrado
                elementos.append((indice, geometria, region))
        elif modalidad in ("area", "área"):
            try:
                geometria = poligono_area(data_zonas)
            except (ValueError, TypeError) as exc:
                faltantes.append((codigo, f"área inválida: {exc}"))
                continue
            region = region_para_area(geometria, regiones_geometricas)
            elementos.append(("area", geometria, region))
        else:
            faltantes.append((codigo, f"tipoZonaAfecta desconocido: {modalidad}"))
            continue

        for indice, geometria, region in elementos:
            reg_n, reg_num = REGIONES_ABREVIADAS.get(region, (None, None))
            propiedades = {
                "id": evento.get("id"), "codigoMeteo": codigo, "tipo": tipo,
                "fenomeno": evento.get("fenomeno"), "titulo": evento.get("titulo"),
                "region": region, "reg_n": reg_n, "reg_num": reg_num,
                "region_zona": indice,
                "tipoZonaAfecta": modalidad, "desde": evento.get("desde"),
                "hasta": evento.get("hasta"), "emision": evento.get("emision"),
                "condicionSinoptica": evento.get("condicionSinoptica"),
                "fuente": "DMC",
            }
            resultado.append({"type": "Feature", "properties": propiedades, "geometry": geometria})
            conteo[modalidad] += 1

    if faltantes:
        logging.warning("Zonas/áreas sin geometría (%d): %s", len(faltantes), faltantes[:20])
    if eventos and not resultado:
        raise ValueError("Existen productos AAA pero no se pudo generar ninguna geometría")

    resultado.sort(key=lambda e: (
        PRIORIDAD[e["properties"]["tipo"]],
        str(e["properties"]["codigoMeteo"]),
        e["properties"]["region_zona"],
    ))
    geojson = {"type": "FeatureCollection", "features": resultado}
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    temporal = SALIDA.with_suffix(".tmp")
    with temporal.open("w", encoding="utf-8") as archivo:
        json.dump(geojson, archivo, ensure_ascii=False, separators=(",", ":"))
    temporal.replace(SALIDA)
    logging.info("Productos AAA: %d", len(eventos))
    logging.info("Geometrías generadas: %d (%s)", len(resultado), dict(conteo))
    logging.info("Actualización UTC: %s", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    generar_geojson()
