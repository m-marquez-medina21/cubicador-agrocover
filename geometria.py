# geometria.py — Generación y ordenamiento espacial de hileras.
# Modificar aquí si cambia cómo se generan las hileras desde un polígono (ángulo, offset, inversión)
# o cómo se ordenan las hileras de un DXF para definir cuál es H1.

import math

import pandas as pd
from shapely.affinity import rotate
from shapely.geometry import LineString, Polygon


def angulo_lado_mas_largo(pol_pts: list) -> float:
    """Ángulo en grados del lado más largo del polígono (para auto-detectar dirección de hileras)."""
    exterior = list(Polygon(pol_pts).exterior.coords)
    max_largo, angulo = 0.0, 0.0
    for i in range(len(exterior) - 1):
        dx = exterior[i + 1][0] - exterior[i][0]
        dy = exterior[i + 1][1] - exterior[i][1]
        largo = math.hypot(dx, dy)
        if largo > max_largo:
            max_largo = largo
            angulo = math.degrees(math.atan2(dy, dx))
    return angulo


def generar_hileras_desde_poligono(
    pol_pts: list,
    dist_hileras: float,
    angulo_grados: float = 0.0,
    largo_minimo: float = 0.0,
    sector: str = "Sector",
    offset_inicio: float = 0.0,
    invertir: bool = False,
) -> pd.DataFrame:
    """
    Genera hileras paralelas dentro de un polígono UTM (metros).
    Retorna DataFrame con columnas: Sector, Bloque, Largo_m, Puntos.
    """
    poligono  = Polygon(pol_pts)
    centroide = poligono.centroid

    pol_rot            = rotate(poligono, -angulo_grados, origin=centroide)
    minx, miny, maxx, maxy = pol_rot.bounds

    hileras = []
    y = miny + (offset_inicio % dist_hileras)
    while y <= maxy + dist_hileras:
        linea = LineString([(minx - 1, y), (maxx + 1, y)])
        inter = pol_rot.intersection(linea)

        geoms = (
            [inter]              if inter.geom_type == "LineString"      else
            list(inter.geoms)    if inter.geom_type == "MultiLineString"  else
            []
        )

        for seg in geoms:
            seg_orig = rotate(seg, angulo_grados, origin=centroide)
            pts      = list(seg_orig.coords)
            largo    = seg_orig.length
            if largo >= largo_minimo and len(pts) >= 2:
                hileras.append({
                    "Sector":  sector,
                    "Bloque":  sector,
                    "Largo_m": round(largo, 2),
                    "Puntos":  pts,
                })

        y += dist_hileras

    if invertir:
        hileras = hileras[::-1]

    return pd.DataFrame(hileras)


def centrales_proyectados(df: pd.DataFrame, l_carpa: float) -> pd.Series:
    """
    Cuenta los centrales (postes interiores) de cada hilera proyectando líneas de
    corte perpendiculares cada l_carpa metros a lo largo de la hilera más larga del
    sector, y contando cuántas de esas líneas intersectan a cada hilera dentro del
    polígono — aunque esa hilera no empiece en el mismo punto que la más larga
    (bordes irregulares, terrenos no rectangulares).
    """
    if df.empty:
        return pd.Series([], dtype=int)

    idx_ref = df["Largo_m"].idxmax()
    p0, p1  = df.loc[idx_ref, "Puntos"][0], df.loc[idx_ref, "Puntos"][-1]
    dx, dy  = p1[0] - p0[0], p1[1] - p0[1]
    largo_ref = math.hypot(dx, dy)

    if largo_ref == 0:
        return pd.Series([0] * len(df), index=df.index)

    dirx, diry   = dx / largo_ref, dy / largo_ref
    perpx, perpy = -diry, dirx

    todas_pts = [p for pts in df["Puntos"] for p in pts]
    xs = [p[0] for p in todas_pts]
    ys = [p[1] for p in todas_pts]
    extension = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) + 1

    n_cortes = int(largo_ref // l_carpa)
    cortes = []
    for i in range(1, n_cortes + 1):
        t  = i * l_carpa
        cx = p0[0] + dirx * t
        cy = p0[1] + diry * t
        cortes.append(LineString([
            (cx - perpx * extension, cy - perpy * extension),
            (cx + perpx * extension, cy + perpy * extension),
        ]))

    conteos = []
    for pts in df["Puntos"]:
        linea = LineString(pts)
        conteos.append(sum(1 for corte in cortes if linea.intersects(corte)))

    return pd.Series(conteos, index=df.index)


def reordenar_hileras(df: pd.DataFrame, invertir: bool | dict = False) -> pd.DataFrame:
    """
    Ordena las hileras de cada sector por posición espacial (de un borde al opuesto),
    de modo que H1 corresponda a un extremo geográfico definido.
    Útil cuando las hileras vienen de un DXF con orden arbitrario.
    invertir puede ser bool (aplica a todos) o dict {sector: bool} (por sector).
    """
    if df.empty:
        return df

    grupos = []
    for sector, grp in df.groupby("Sector", sort=False):
        grp = grp.copy()
        inv = invertir.get(sector, False) if isinstance(invertir, dict) else invertir

        # Centroide de cada hilera
        grp["_cx"] = grp["Puntos"].apply(lambda pts: sum(p[0] for p in pts) / len(pts))
        grp["_cy"] = grp["Puntos"].apply(lambda pts: sum(p[1] for p in pts) / len(pts))

        # Ángulo dominante: dirección de la primera hilera con longitud real
        angulo_rad = 0.0
        for pts in grp["Puntos"]:
            if len(pts) >= 2:
                dx = pts[-1][0] - pts[0][0]
                dy = pts[-1][1] - pts[0][1]
                if abs(dx) + abs(dy) > 1e-6:
                    angulo_rad = math.atan2(dy, dx)
                    break

        # Proyectar centroides sobre la dirección perpendicular a las hileras
        perp_x = math.cos(angulo_rad + math.pi / 2)
        perp_y = math.sin(angulo_rad + math.pi / 2)
        grp["_proj"] = grp["_cx"] * perp_x + grp["_cy"] * perp_y

        grp = grp.sort_values("_proj", ascending=not inv).drop(
            columns=["_cx", "_cy", "_proj"]
        )
        grupos.append(grp)

    return pd.concat(grupos).reset_index(drop=True)
