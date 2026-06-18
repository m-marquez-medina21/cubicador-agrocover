# readers.py — Lectura de archivos de entrada (DXF y KMZ).
# Modificar aquí si cambia el formato de entrada: nuevas capas DXF, estructura KML, proyección UTM.
# Salidas: (DataFrame[Sector, Bloque, Largo_m, Puntos], lista de polígonos)

import zipfile
import xml.etree.ElementTree as ET

import ezdxf
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString


# ── DXF ───────────────────────────────────────────────────────────────────────

def _puntos_lwpolyline(ent) -> list:
    return [(p[0], p[1]) for p in ent.get_points("xy")]


def leer_hileras_dxf(ruta: str) -> tuple[pd.DataFrame, list]:
    """
    Lee un DXF y devuelve:
    - DataFrame de hileras  (Sector, Bloque, Largo_m, Puntos)
    - lista de polígonos cerrados  [{"Nombre", "Capa", "Puntos"}]
    """
    doc = ezdxf.readfile(ruta)
    msp = doc.modelspace()
    hileras:   list[dict] = []
    poligonos: list[dict] = []

    # Polígonos cerrados directamente en el modelspace
    for e in msp:
        if e.dxftype() == "LWPOLYLINE" and e.closed and "Sect" not in e.dxf.layer:
            pts = _puntos_lwpolyline(e)
            if len(pts) >= 3:
                poligonos.append({"Nombre": e.dxf.layer, "Capa": e.dxf.layer, "Puntos": pts})

    # Hileras y polígonos dentro de bloques INSERT
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        nombre = e.dxf.name
        try:
            bloque = doc.blocks[nombre]
        except Exception:
            continue
        for ent in bloque:
            if ent.dxftype() != "LWPOLYLINE":
                continue
            pts = _puntos_lwpolyline(ent)
            if "Sect" in ent.dxf.layer:
                if len(pts) >= 2:
                    hileras.append({
                        "Sector":  ent.dxf.layer,
                        "Bloque":  nombre,
                        "Largo_m": round(LineString(pts).length, 2),
                        "Puntos":  pts,
                    })
            elif ent.closed and len(pts) >= 3:
                poligonos.append({
                    "Nombre": f"{nombre} — {ent.dxf.layer}",
                    "Capa":   ent.dxf.layer,
                    "Puntos": pts,
                })

    return pd.DataFrame(hileras), poligonos


# ── KMZ ───────────────────────────────────────────────────────────────────────

def _kml_coords(texto: str | None) -> list:
    puntos = []
    for token in (texto or "").strip().split():
        partes = token.split(",")
        if len(partes) >= 2:
            puntos.append((float(partes[0]), float(partes[1])))
    return puntos


def _utm_transformer(lons: list, lats: list) -> Transformer:
    lon_c = sum(lons) / len(lons)
    lat_c = sum(lats) / len(lats)
    zona  = int((lon_c + 180) / 6) + 1
    epsg  = 32600 + zona if lat_c >= 0 else 32700 + zona
    return Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)


def leer_kmz(ruta: str) -> tuple[pd.DataFrame, list]:
    """
    Lee un KMZ y devuelve:
    - DataFrame de hileras si el KMZ contiene LineStrings
    - lista de polígonos si el KMZ contiene Polygons
    Las coordenadas se proyectan automáticamente a UTM (metros).
    """
    with zipfile.ZipFile(ruta) as z:
        kml_file = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
        if kml_file is None:
            return pd.DataFrame(), []
        kml_bytes = z.read(kml_file)

    root = ET.fromstring(kml_bytes)
    # Quitar namespace para simplificar búsquedas
    for el in root.iter():
        if el.tag.startswith("{"):
            el.tag = el.tag.split("}", 1)[1]

    hileras_raw:   list[dict] = []
    poligonos_raw: list[dict] = []

    def _placemark(pm, sector: str):
        nombre = pm.findtext("name") or sector
        ls = pm.find(".//LineString/coordinates")
        pg = (pm.find(".//Polygon//outerBoundaryIs//coordinates")
              or pm.find(".//Polygon//coordinates"))
        if ls is not None:
            coords = _kml_coords(ls.text)
            if len(coords) >= 2:
                hileras_raw.append({"sector": sector, "nombre": nombre, "coords": coords})
        elif pg is not None:
            coords = _kml_coords(pg.text)
            if len(coords) >= 3:
                poligonos_raw.append({"nombre": nombre, "coords": coords})

    for folder in root.findall(".//Folder"):
        sector = folder.findtext("name") or "Sin sector"
        for pm in folder.findall("Placemark"):
            _placemark(pm, sector)

    doc = root.find("Document") or root
    for pm in doc.findall("Placemark"):
        _placemark(pm, "Sin sector")

    if not hileras_raw and not poligonos_raw:
        return pd.DataFrame(), []

    todas = (
        [c for h in hileras_raw  for c in h["coords"]] +
        [c for p in poligonos_raw for c in p["coords"]]
    )
    transformer = _utm_transformer([c[0] for c in todas], [c[1] for c in todas])

    def proyectar(coords):
        return [transformer.transform(lon, lat) for lon, lat in coords]

    hileras = [
        {
            "Sector":  h["sector"],
            "Bloque":  h["sector"],
            "Largo_m": round(LineString(proyectar(h["coords"])).length, 2),
            "Puntos":  proyectar(h["coords"]),
        }
        for h in hileras_raw
    ]

    poligonos = [
        {"Nombre": p["nombre"], "Capa": p["nombre"], "Puntos": proyectar(p["coords"])}
        for p in poligonos_raw
    ]

    return pd.DataFrame(hileras), poligonos
