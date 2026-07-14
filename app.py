# app.py — Interfaz Streamlit (punto de entrada: streamlit run app.py).
# Modificar aquí si cambia la UI: parámetros del sidebar, visualizaciones, flujo de carga.
# Para cambiar cálculos → calculos.py | formato Excel → exportar.py | lectura archivos → readers.py

import math
import tempfile
from io import BytesIO

import contextily as cx
import folium
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from pyproj import Transformer
from shapely.geometry import LineString, Polygon
from streamlit_folium import folium_static

from calculos  import calcular_hileras, resumen_sectores
from exportar  import crear_excel
from geometria import (
    angulo_lado_mas_largo,
    generar_hileras_desde_poligono,
    reordenar_hileras,
    transversales_proyectados,
)
from readers   import leer_hileras_dxf, leer_kmz

# ── Configuración ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="Cubicador Agrocover", layout="wide")
st.title("Cubicador Agrocover")
st.write("Versión 2.2")

archivo = st.file_uploader(
    "Seleccione archivo DXF o KMZ",
    type=["dxf", "kmz"],
    help="DXF: exportado desde AutoCAD/Revit con hileras dibujadas. "
         "KMZ: exportado desde Google Earth con hileras o polígono del terreno.",
)

# ── Sidebar — Datos del proyecto ──────────────────────────────────────────────

st.sidebar.header("Datos del proyecto")

with st.sidebar.expander("Información del cliente", expanded=False):
    empresa           = st.text_input("Empresa")
    rut               = st.text_input("RUT")
    encargado         = st.text_input("Encargado")
    direccion_cliente = st.text_input("Dirección")
    mail              = st.text_input("Mail")
    celular           = st.text_input("Celular")

with st.sidebar.expander("Datos del cultivo", expanded=False):
    especie        = st.text_input("Especie")
    variedad       = st.text_input("Variedad")
    superficie_ha  = st.number_input("Superficie (Ha)",        min_value=0.0, value=0.0, step=0.1)
    altura_plantas = st.number_input("Altura de plantas (m)",  min_value=0.0, value=0.0, step=0.1)

with st.sidebar.expander("Calidad (mermas)", expanded=False):
    merma_hil   = st.number_input("Merma hileras (%)",       min_value=0.0, value=0.0, step=1.0)
    merma_trans = st.number_input("Merma transversales (%)", min_value=0.0, value=0.0, step=1.0)


# ── Helper: parámetros de sector ─────────────────────────────────────────────

def _sector_params_ui(key: str) -> dict:
    """Widgets de configuración de un sector. Llamar dentro de un expander de sidebar."""
    st.markdown("**Marco de plantación**")
    dh = st.number_input("Entre hileras (m)", min_value=0.1, value=3.0, step=0.1,  key=f"dh_{key}")
    dp = st.number_input("Entre plantas (m)", min_value=0.1, value=2.0, step=0.1,  key=f"dp_{key}")
    st.markdown("**Dimensiones de la carpa**")
    ac = st.number_input("Ancho carpa (m)",       min_value=0.1, value=3.0,  step=0.1,  key=f"ac_{key}")
    lc = st.number_input("Largo carpa (m)",       min_value=0.1, value=12.0, step=0.5,  key=f"lc_{key}")
    lm = st.number_input("Largo mínimo (m)",      min_value=0.0, value=5.0,  step=0.5,  key=f"lm_{key}")
    cadic = st.number_input("Centrales adicionales", min_value=0, value=0, step=1, key=f"cadic_{key}")
    at = st.number_input(
        "Ángulo transversal (° respecto a perpendicular)",
        min_value=-45.0, max_value=45.0, value=0.0, step=0.5, key=f"at_{key}",
        help="Corrige el ángulo del transversal cuando no es exactamente perpendicular a las hileras.",
    )
    ap = st.number_input("Alto pilares (m)",      min_value=0.1, value=3.0,  step=0.1,  key=f"ap_{key}")
    le = st.number_input("Largo enterrado (m)",   min_value=0.0, value=0.5,  step=0.05, key=f"le_{key}")
    ah = st.number_input("Alto hombros (m)",      min_value=0.0, value=0.5,  step=0.05, key=f"ah_{key}")
    ca = st.number_input("Caída agua (negativo)", max_value=0.0, value=-0.3, step=0.05, key=f"ca_{key}")
    _a         = math.sqrt(max(ac ** 2 - ah ** 2, 0.0))
    ancho_vent = round(dh - _a * 2, 3)
    l_trans    = round(2 * math.sqrt(ac ** 2 + ah ** 2), 3)
    st.caption(f"Ventilación: **{ancho_vent} m**  |  Transversal: **{l_trans} m**")
    return {
        "d_hil": dh, "d_pl": dp, "ancho_c": ac, "l_carpa": lc,
        "l_min": lm, "alto_p": ap, "l_ent": le, "alto_h": ah,
        "caida": ca, "ancho_vent": ancho_vent, "l_trans": l_trans,
        "cent_adic": cadic, "ang_trans": at,
    }


# ── Helpers de visualización ──────────────────────────────────────────────────

def _dibujar_transversales(ax, df_hileras, l_carpa, angulo_offset=0.0):
    """Dibuja los cortes transversales proyectados (línea + puntos de cruce) para
    validar visualmente su posición y ángulo respecto a las hileras."""
    if not l_carpa or df_hileras.empty:
        return
    cortes = transversales_proyectados(df_hileras, l_carpa, angulo_offset)
    primera_leyenda = True
    for c in cortes:
        sx, sy = c["segmento"].xy
        ax.plot(sx, sy, linewidth=0.6, color="darkorange", linestyle="--", alpha=0.8,
                 zorder=4, label="Transversal proyectado" if primera_leyenda else None)
        primera_leyenda = False
        px = [p[0] for p in c["puntos"]]
        py = [p[1] for p in c["puntos"]]
        ax.scatter(px, py, s=4, color="darkorange", alpha=0.8, zorder=6)


def _generar_imagen_sector_png(df_sec, epsg=None, l_carpa=None, angulo_trans=0.0, pol_shapely=None) -> bytes:
    """Genera una imagen PNG (hileras + transversales, con satelital si hay EPSG)
    para incrustar en la hoja del Excel de ese cuartel."""
    n_h1 = df_sec["N_hilera"].min()
    fig, ax = plt.subplots(figsize=(9, 6.5))

    for _, row in df_sec.iterrows():
        xs = [p[0] for p in row["Puntos"]]
        ys = [p[1] for p in row["Puntos"]]
        if row["N_hilera"] == n_h1:
            ax.plot(xs, ys, linewidth=2.2, color="red", zorder=5)
        else:
            ax.plot(xs, ys, linewidth=0.8, color="cyan" if epsg else "royalblue", zorder=3)

    if pol_shapely is not None:
        px, py = pol_shapely.exterior.xy
        ax.plot(px, py, color="lime" if epsg else "green", linewidth=1.5, zorder=2)

    _dibujar_transversales(ax, df_sec, l_carpa, angulo_trans)

    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"Cuartel: {df_sec['Sector'].iloc[0]}", fontsize=11)

    if epsg:
        try:
            cx.add_basemap(ax, crs=f"EPSG:{epsg}", source=cx.providers.Esri.WorldImagery, zorder=-10)
        except Exception:
            pass

    plt.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


_TAB10_HEX = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _a_latlon(transformer, pts):
    """Convierte una lista de puntos UTM (x, y) a pares (lat, lon) para Folium."""
    return [(lat, lon) for lon, lat in transformer.itransform(pts)]


def _mapa_base(centro_latlon):
    """Mapa Folium con capa satelital (Esri) y mapa base (OSM) intercambiables,
    con zoom y desplazamiento interactivos — igual que Google Earth."""
    m = folium.Map(location=centro_latlon, zoom_start=17, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, "
             "Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
        name="Satelital (Esri)",
        max_zoom=19,
        show=True,
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="Mapa (OSM)", show=False).add_to(m)
    return m


def _agregar_transversales_folium(m, transformer, df_hileras, l_carpa, angulo_offset):
    if not l_carpa or df_hileras.empty:
        return
    fg = folium.FeatureGroup(name="Transversales proyectados")
    for c in transversales_proyectados(df_hileras, l_carpa, angulo_offset):
        folium.PolyLine(
            _a_latlon(transformer, list(c["segmento"].coords)),
            color="darkorange", weight=1.5, opacity=0.8, dash_array="4,4",
        ).add_to(fg)
    fg.add_to(m)


def _mapa_preview_folium(pol_pts, df_hileras, angulo, n_hileras, epsg, l_carpa=None, angulo_trans=0.0):
    """Equivalente interactivo de _plot_preview: polígono + hileras generadas sobre
    imagen satelital real, con zoom/desplazamiento como Google Earth."""
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    centro = _a_latlon(transformer, [Polygon(pol_pts).centroid.coords[0]])[0]
    m = _mapa_base(centro)

    folium.PolyLine(
        _a_latlon(transformer, pol_pts + [pol_pts[0]]),
        color="lime", weight=2.5, tooltip="Contorno del terreno",
    ).add_to(m)

    fg_hileras = folium.FeatureGroup(name=f"Hileras ({n_hileras})")
    for i, (_, row) in enumerate(df_hileras.iterrows()):
        pts = _a_latlon(transformer, row["Puntos"])
        if i == 0:
            folium.PolyLine(pts, color="red", weight=4, tooltip="H1 (primera hilera)").add_to(fg_hileras)
        else:
            folium.PolyLine(pts, color="cyan", weight=1.5, opacity=0.8).add_to(fg_hileras)
    fg_hileras.add_to(m)

    _agregar_transversales_folium(m, transformer, df_hileras, l_carpa, angulo_trans)

    folium.LayerControl(collapsed=False).add_to(m)
    try:
        m.fit_bounds(m.get_bounds())
    except Exception:
        pass
    return m


def _mapa_resultados_folium(df_vista, epsg, pol_shapely=None, l_carpa=None, angulo_trans=0.0):
    """Equivalente interactivo de _plot_hileras: hileras calculadas sobre imagen
    satelital real, con zoom/desplazamiento como Google Earth."""
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    centro = _a_latlon(transformer, [df_vista.iloc[0]["Puntos"][0]])[0]
    m = _mapa_base(centro)

    if pol_shapely is not None:
        folium.PolyLine(
            _a_latlon(transformer, list(pol_shapely.exterior.coords)),
            color="lime", weight=2, dash_array="6,4", tooltip="Polígono terreno",
        ).add_to(m)

    n_h1     = df_vista["N_hilera"].min()
    bloques  = list(df_vista["Bloque"].unique())
    colores  = {b: _TAB10_HEX[i % len(_TAB10_HEX)] for i, b in enumerate(bloques)}

    fg_hileras = folium.FeatureGroup(name="Hileras")
    for _, row in df_vista.iterrows():
        pts = _a_latlon(transformer, row["Puntos"])
        if row["N_hilera"] == n_h1:
            folium.PolyLine(pts, color="red", weight=4, tooltip="H1 (primera hilera)").add_to(fg_hileras)
        else:
            folium.PolyLine(
                pts, color=colores[row["Bloque"]], weight=1.5, opacity=0.85,
                tooltip=f"Hilera {int(row['N_hilera'])} · {row['Largo_m']} m · {row['Bloque']}",
            ).add_to(fg_hileras)
    fg_hileras.add_to(m)

    _agregar_transversales_folium(m, transformer, df_vista, l_carpa, angulo_trans)

    folium.LayerControl(collapsed=False).add_to(m)
    try:
        m.fit_bounds(m.get_bounds())
    except Exception:
        pass
    return m


def _plot_hileras(df_vista, pol_shapely=None, l_carpa=None, angulo_trans=0.0):
    bloques  = df_vista["Bloque"].unique()
    _tab10   = [plt.cm.tab10(i) for i in range(10)]
    colores  = {b: _tab10[i % 10] for i, b in enumerate(bloques)}

    # H1 = hilera con N_hilera mínimo en esta vista
    n_h1 = df_vista["N_hilera"].min()

    fig, ax = plt.subplots(figsize=(8, 5))
    for bloque, df_b in df_vista.groupby("Bloque"):
        primera_leyenda = True
        for _, row in df_b.iterrows():
            xs = [p[0] for p in row["Puntos"]]
            ys = [p[1] for p in row["Puntos"]]
            if row["N_hilera"] == n_h1:
                ax.plot(xs, ys, linewidth=2.5, color="red", zorder=5,
                        label="H1 (primera hilera)")
                mx = (xs[0] + xs[-1]) / 2
                my = (ys[0] + ys[-1]) / 2
                ax.annotate(
                    "H1", xy=(mx, my), fontsize=9, fontweight="bold", color="red",
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="red", alpha=0.8),
                    ha="center", va="center", zorder=6,
                )
            else:
                ax.plot(xs, ys, linewidth=0.8, color=colores[bloque],
                        label=bloque if primera_leyenda else None)
                primera_leyenda = False

    if pol_shapely is not None:
        px, py = pol_shapely.exterior.xy
        ax.fill(px, py, alpha=0.07, color="green")
        ax.plot(px, py, color="green", linewidth=1.5,
                linestyle="--", label="Polígono terreno")

    _dibujar_transversales(ax, df_vista, l_carpa, angulo_trans)

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.grid(True)
    ax.legend(title="Bloque / Zona", bbox_to_anchor=(1.01, 1),
              loc="upper left", fontsize=8)
    plt.tight_layout()
    return fig


def _plot_preview(pol_pts, df_hileras, angulo, n_hileras, l_carpa=None, angulo_trans=0.0):
    """Polígono + hileras superpuestas (fallback sin georreferencia). H1 resaltada
    para identificar punto de inicio."""
    fig, ax = plt.subplots(figsize=(8, 5))

    # Polígono de fondo
    xs = [p[0] for p in pol_pts] + [pol_pts[0][0]]
    ys = [p[1] for p in pol_pts] + [pol_pts[0][1]]
    ax.fill(xs, ys, alpha=0.10, color="green")
    ax.plot(xs, ys, color="green", linewidth=2, label="Contorno del terreno")

    # Hileras: H1 en rojo destacado, resto en azul
    for i, (_, row) in enumerate(df_hileras.iterrows()):
        pts = row["Puntos"]
        hxs = [p[0] for p in pts]
        hys = [p[1] for p in pts]
        if i == 0:
            ax.plot(hxs, hys, linewidth=2.5, color="red", zorder=5, label="H1 (primera hilera)")
            # Etiqueta "H1" en el punto medio de la hilera
            mx = (hxs[0] + hxs[-1]) / 2
            my = (hys[0] + hys[-1]) / 2
            ax.annotate(
                "H1",
                xy=(mx, my),
                fontsize=9, fontweight="bold", color="red",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="red", alpha=0.8),
                ha="center", va="center", zorder=6,
            )
        elif i == 1:
            ax.plot(hxs, hys, linewidth=0.7, color="royalblue", label=f"Hileras restantes ({n_hileras - 1})")
        else:
            ax.plot(hxs, hys, linewidth=0.7, color="royalblue")

    # Flecha mostrando la dirección de numeración (de H1 hacia H2)
    if len(df_hileras) >= 2:
        p1 = df_hileras.iloc[0]["Puntos"]
        p2 = df_hileras.iloc[1]["Puntos"]
        # Punto medio de cada hilera
        m1x = (p1[0][0] + p1[-1][0]) / 2
        m1y = (p1[0][1] + p1[-1][1]) / 2
        m2x = (p2[0][0] + p2[-1][0]) / 2
        m2y = (p2[0][1] + p2[-1][1]) / 2
        ax.annotate(
            "", xy=(m2x, m2y), xytext=(m1x, m1y),
            arrowprops=dict(arrowstyle="->", color="red", lw=1.5),
            zorder=7,
        )

    _dibujar_transversales(ax, df_hileras, l_carpa, angulo_trans)

    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"Vista previa — ángulo {angulo}°  |  {n_hileras} hileras  |  H1 en rojo")
    ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)"); ax.grid(True, alpha=0.4)
    ax.legend(loc="upper right", fontsize=9)
    plt.tight_layout()
    return fig


# ── Flujo principal ───────────────────────────────────────────────────────────

if not archivo:
    st.info("Cargue un archivo DXF o KMZ para comenzar.")
    st.stop()

# Leer archivo
es_kmz = archivo.name.lower().endswith(".kmz")
sufijo = ".kmz" if es_kmz else ".dxf"
with tempfile.NamedTemporaryFile(delete=False, suffix=sufijo) as tmp:
    tmp.write(archivo.read())
    ruta_tmp = tmp.name

if es_kmz:
    df_raw, poligonos_dxf, epsg_kmz = leer_kmz(ruta_tmp)
else:
    df_raw, poligonos_dxf = leer_hileras_dxf(ruta_tmp)
    epsg_kmz = None
st.success(f"Archivo cargado: {archivo.name}")

# ── Caso: KMZ con polígono(s) → generar hileras ──────────────────────────────
if df_raw.empty and poligonos_dxf:
    n_pols = len(poligonos_dxf)
    st.info(
        f"El archivo contiene **{n_pols} polígono(s)**. "
        "Configure cada uno en el sidebar y se generarán hileras para todos los activados."
        if n_pols > 1 else
        "El archivo contiene un polígono del terreno. "
        "Ajuste el ángulo en el sidebar hasta que las hileras coincidan con el campo real."
    )

    st.sidebar.subheader("Sectores")
    params_sector = {}
    poligonos_por_sector = {}
    dfs_generados = []

    for i_pol, pol_data in enumerate(poligonos_dxf):
        pol_nombre  = pol_data["Nombre"]
        pol_pts     = pol_data["Puntos"]
        angulo_auto = round(angulo_lado_mas_largo(pol_pts), 1) % 180

        sl_key  = f"ang_sl_{pol_nombre}"
        ni_key  = f"ang_ni_{pol_nombre}"
        of_key  = f"offset_{pol_nombre}"
        inv_key = f"inv_{pol_nombre}"
        inc_key = f"incl_{pol_nombre}"
        sec_key = f"sect_{pol_nombre}"

        if sl_key  not in st.session_state: st.session_state[sl_key]  = float(angulo_auto)
        if ni_key  not in st.session_state: st.session_state[ni_key]  = float(angulo_auto)
        if of_key  not in st.session_state: st.session_state[of_key]  = 0.0
        if inv_key not in st.session_state: st.session_state[inv_key] = False

        def _sl_to_ni(sl=sl_key, ni=ni_key):
            st.session_state[ni] = round(st.session_state[sl], 1)
        def _ni_to_sl(sl=sl_key, ni=ni_key):
            st.session_state[sl] = st.session_state[ni]

        # ── Expander por polígono — todos los parámetros de ese sector ────────
        with st.sidebar.expander(f"📍 {pol_nombre}", expanded=(i_pol == 0)):
            st.text_input("Nombre del sector", value=pol_nombre, key=sec_key)
            incluir = st.checkbox("Incluir en cálculo", value=True, key=inc_key)
            sp = _sector_params_ui(f"pol_{pol_nombre}")
            st.markdown("**Orientación de hileras**")
            st.caption(f"Ángulo auto-detectado: **{angulo_auto}°**")
            st.slider(
                "Ángulo — ajuste rápido", 0.0, 179.5, step=1.0,
                key=sl_key, on_change=_sl_to_ni, help="0°=E-O · 90°=N-S",
            )
            st.number_input(
                "Ángulo exacto (°)", 0.0, 179.5, step=0.5,
                key=ni_key, on_change=_ni_to_sl,
            )
            st.toggle("Invertir H1 (lado opuesto)", key=inv_key)
            st.number_input(
                "Desplazamiento borde (m)", 0.0, float(sp["d_hil"]), step=0.1, key=of_key,
            )

        if not incluir:
            continue

        angulo_hil    = st.session_state[ni_key]
        offset_inicio = st.session_state[of_key]
        invertir      = st.session_state[inv_key]
        sector_nombre = st.session_state[sec_key] or pol_nombre

        df_pol = generar_hileras_desde_poligono(
            pol_pts, sp["d_hil"], angulo_hil, sp["l_min"],
            sector_nombre, offset_inicio, invertir,
        )
        if df_pol.empty:
            st.warning(f"**{pol_nombre}**: sin hileras con los parámetros actuales.")
            continue

        dfs_generados.append(df_pol)
        params_sector[sector_nombre] = sp
        poligonos_por_sector[sector_nombre] = pol_pts

        # Vista previa individual por polígono
        if n_pols > 1:
            st.subheader(f"Vista previa — {sector_nombre}")
        if epsg_kmz:
            folium_static(_mapa_preview_folium(
                pol_pts, df_pol, angulo_hil, len(df_pol), epsg_kmz,
                l_carpa=sp["l_carpa"], angulo_trans=sp.get("ang_trans", 0.0),
            ), height=550)
        else:
            st.pyplot(_plot_preview(
                pol_pts, df_pol, angulo_hil, len(df_pol),
                l_carpa=sp["l_carpa"], angulo_trans=sp.get("ang_trans", 0.0),
            ))
        st.caption(
            f"**{sector_nombre}**: {len(df_pol)} hileras · ángulo {angulo_hil}° · "
            f"offset {offset_inicio} m · {'invertida' if invertir else 'normal'}"
        )

    if not dfs_generados:
        st.error("Ningún polígono activo. Active al menos uno en el sidebar.")
        st.stop()

    df_raw = pd.concat(dfs_generados).reset_index(drop=True)

elif df_raw.empty:
    st.error(
        "No se encontraron hileras ni polígonos en el archivo. "
        "Verifique que las capas del DXF contengan 'Sect', o que el KMZ tenga "
        "líneas o polígonos."
    )
    st.stop()

else:
    # ── DXF o KMZ con hileras dibujadas: configuración por sector ─────────────
    st.sidebar.subheader("Sectores")
    params_sector    = {}
    poligonos_por_sector = {}
    sectores_archivo = sorted(df_raw["Sector"].unique())
    for i_sec, sec in enumerate(sectores_archivo):
        with st.sidebar.expander(f"📍 {sec}", expanded=(i_sec == 0)):
            sp  = _sector_params_ui(f"sec_{sec}")
            inv = st.toggle("Invertir H1 (lado opuesto)", key=f"inv_dxf_{sec}", value=False)
        params_sector[sec] = {**sp, "inv": inv}
    invertir_map = {sec: ps["inv"] for sec, ps in params_sector.items()}
    df_raw = reordenar_hileras(df_raw, invertir=invertir_map)

# ── Diagnóstico ───────────────────────────────────────────────────────────────

st.subheader("Diagnóstico")
c1, c2, c3 = st.columns(3)
c1.metric("Hileras detectadas",   len(df_raw))
c2.metric("Sectores detectados",  len(df_raw["Sector"].unique()))
c3.metric("Bloques detectados",   len(df_raw["Bloque"].unique()))

# ── Filtros en sidebar ────────────────────────────────────────────────────────

st.sidebar.subheader("Filtros de selección")

sectores_sel = st.sidebar.multiselect(
    "Sectores a cubicar",
    options=sorted(df_raw["Sector"].unique()),
    default=sorted(df_raw["Sector"].unique()),
)
bloques_disponibles = sorted(
    df_raw[df_raw["Sector"].isin(sectores_sel)]["Bloque"].unique()
)
bloques_sel = st.sidebar.multiselect(
    "Zonas / Bloques a incluir",
    options=bloques_disponibles,
    default=bloques_disponibles,
    help="Deseleccione bloques para excluir zonas (caminos, áreas sin techado, etc.)",
)

# Filtro por polígono del DXF (si existe)
pol_shapely = None
if poligonos_dxf:
    opciones_pol = ["— Sin filtro de polígono —"] + [p["Nombre"] for p in poligonos_dxf]
    pol_sel = st.sidebar.selectbox("Polígono del terreno (opcional)", opciones_pol)
    if pol_sel != "— Sin filtro de polígono —":
        pol_pts    = next(p["Puntos"] for p in poligonos_dxf if p["Nombre"] == pol_sel)
        pol_shapely = Polygon(pol_pts)

# ── Aplicar filtros ───────────────────────────────────────────────────────────

df_filt = df_raw[
    df_raw["Sector"].isin(sectores_sel) & df_raw["Bloque"].isin(bloques_sel)
].copy()

if pol_shapely is not None and not df_filt.empty:
    n_antes  = len(df_filt)
    df_filt  = df_filt[df_filt["Puntos"].apply(
        lambda pts: pol_shapely.intersects(LineString(pts))
    )].copy()
    st.info(f"Filtro de polígono: {n_antes - len(df_filt)} hileras excluidas por estar fuera del contorno.")

if df_filt.empty:
    st.warning("No hay hileras con los filtros seleccionados.")
    st.stop()

# ── Cálculos por sector ───────────────────────────────────────────────────────

sectores_activos = sorted(df_filt["Sector"].unique().tolist())

dfs_calc = []
for sec in sectores_activos:
    sp     = params_sector.get(sec, {})
    df_sec = df_filt[df_filt["Sector"] == sec].copy()
    df_sec_calc = calcular_hileras(
        df_sec,
        sp.get("l_min",   5.0),
        sp.get("l_carpa", 12.0),
        sp.get("d_pl",    2.0),
        merma_hil, merma_trans,
        sp.get("ancho_c", 3.0),
        sp.get("l_trans", 6.0),
        d_hil=sp.get("d_hil", 3.0),
        centrales_adic=sp.get("cent_adic", 0.0),
        angulo_trans=sp.get("ang_trans", 0.0),
    )
    df_sec_calc["N_hilera"] = range(1, len(df_sec_calc) + 1)
    dfs_calc.append(df_sec_calc)

df_calc = pd.concat(dfs_calc).reset_index(drop=True) if dfs_calc else pd.DataFrame()

if df_calc.empty:
    st.warning("Todas las hileras son menores al largo mínimo configurado por sector.")
    st.stop()

df_res = resumen_sectores(df_calc, m_hil=merma_hil)

# Indicador de filtrado
hileras_excluidas = len(df_filt) - len(df_calc)
msg = f"**{len(df_calc)} hileras a cubicar**"
if hileras_excluidas > 0:
    msg += f" ({hileras_excluidas} excluidas por largo mínimo)"
st.info(msg)

# ── Totales de materiales ─────────────────────────────────────────────────────

st.subheader("Totales de materiales")
factor_h  = 1 + merma_hil / 100
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("CUMBRERAS (m)",    round(df_calc["Largo_m"].sum() * factor_h, 2))
mc2.metric("HOMBROS (m)",      round(df_calc["Largo_m"].sum() * 2 * factor_h, 2))
mc3.metric("TRANSVERSALES (m)", round(df_calc["Uso_T_m"].sum(), 2))
mc4.metric("PERIMETRO (m)",    round(df_res["Uso_P_total"].sum(), 2))

# ── Resumen por sector ────────────────────────────────────────────────────────

st.subheader("Resumen por sector")
st.dataframe(df_res, use_container_width=True)

# ── Visualización ─────────────────────────────────────────────────────────────

st.subheader("Vista visual por sector")
sector_vista = st.selectbox("Sector a visualizar", sectores_sel)
df_vista     = df_calc[df_calc["Sector"] == sector_vista]

sp_vista      = params_sector.get(sector_vista, {})
l_carpa_vista = sp_vista.get("l_carpa", 12.0)
ang_trans_vista = sp_vista.get("ang_trans", 0.0)

if epsg_kmz:
    folium_static(_mapa_resultados_folium(
        df_vista, epsg_kmz, pol_shapely=pol_shapely,
        l_carpa=l_carpa_vista, angulo_trans=ang_trans_vista,
    ), height=550)
else:
    fig = _plot_hileras(df_vista, pol_shapely, l_carpa=l_carpa_vista, angulo_trans=ang_trans_vista)
    fig.axes[0].set_title(f"Hileras medidas — {sector_vista}")
    st.pyplot(fig)

# ── Detalle de hileras ────────────────────────────────────────────────────────

st.subheader("Detalle de hileras")
cols_vista = [
    "Sector", "N_hilera", "Largo_m", "N_plantas",
    "Centrales", "Centrales_Adic", "Carpas", "Uso_C_m2",
    "Trans_cant", "Trans_largo", "Uso_T_m", "Perim_cant", "Uso_P_m",
]
st.dataframe(df_calc[cols_vista], use_container_width=True)

# ── Descarga Excel ────────────────────────────────────────────────────────────

st.subheader("Exportar")

params = (
    empresa, rut, encargado, direccion_cliente, mail, celular,
    especie, variedad, superficie_ha, altura_plantas,
    3.0, 2.0, merma_hil, merma_trans,
    3.0, 12.0, 5.0, 3.0, 0.5, 0.5, -0.3, 0.0, 6.0, 0, 0.0,
)

if st.button("Generar Excel con imagen por cuartel"):
    with st.spinner("Generando imágenes por cuartel (incluye satelital, puede tardar unos segundos)..."):
        imagenes_por_sector = {}
        for sec in sectores_activos:
            df_sec_calc = df_calc[df_calc["Sector"] == sec]
            sp_sec      = params_sector.get(sec, {})
            pol_sec_pts = poligonos_por_sector.get(sec)
            pol_sec     = Polygon(pol_sec_pts) if pol_sec_pts else pol_shapely
            imagenes_por_sector[sec] = _generar_imagen_sector_png(
                df_sec_calc, epsg=epsg_kmz,
                l_carpa=sp_sec.get("l_carpa", 12.0),
                angulo_trans=sp_sec.get("ang_trans", 0.0),
                pol_shapely=pol_sec,
            )
        st.session_state["excel_bytes"] = crear_excel(
            df_calc, df_res, params, params_por_sector=params_sector,
            imagenes_por_sector=imagenes_por_sector,
        )
    st.success("Excel generado.")

if st.session_state.get("excel_bytes"):
    st.download_button(
        label="Descargar cubicación en Excel",
        data=st.session_state["excel_bytes"],
        file_name="cubicacion_agrocover.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
