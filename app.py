# app.py — Interfaz Streamlit (punto de entrada: streamlit run app.py).
# Modificar aquí si cambia la UI: parámetros del sidebar, visualizaciones, flujo de carga.
# Para cambiar cálculos → calculos.py | formato Excel → exportar.py | lectura archivos → readers.py

import math
import tempfile

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from shapely.geometry import LineString, Polygon

from calculos  import calcular_hileras, resumen_sectores
from exportar  import crear_excel
from geometria import angulo_lado_mas_largo, generar_hileras_desde_poligono, reordenar_hileras
from readers   import leer_hileras_dxf, leer_kmz

# ── Configuración ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="Cubicador Agrocover", layout="wide")
st.title("Cubicador Agrocover")
st.write("Versión 2.1")

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

st.sidebar.subheader("Marco de plantación")
dist_hileras = st.sidebar.number_input("Entre hileras (m)", min_value=0.1, value=3.0, step=0.1)
dist_plantas = st.sidebar.number_input("Entre plantas (m)", min_value=0.1, value=2.0, step=0.1)
merma_hil    = st.sidebar.number_input("Merma hileras (%)",        min_value=0.0, value=0.0, step=1.0)
merma_trans  = st.sidebar.number_input("Merma transversales (%)",  min_value=0.0, value=0.0, step=1.0)

st.sidebar.subheader("Dimensiones de la carpa")
ancho_carpa     = st.sidebar.number_input("Ancho carpa (m)",       min_value=0.1, value=3.0,  step=0.1)
largo_carpa     = st.sidebar.number_input("Largo carpa (m)",       min_value=0.1, value=12.0, step=0.5)
largo_minimo    = st.sidebar.number_input("Largo mínimo (m)",      min_value=0.0, value=5.0,  step=0.5)
alto_pilares    = st.sidebar.number_input("Alto pilares (m)",      min_value=0.1, value=3.0,  step=0.1)
largo_enterrado = st.sidebar.number_input("Largo enterrado (m)",   min_value=0.0, value=0.5,  step=0.05)
alto_hombros    = st.sidebar.number_input("Alto hombros (m)",      min_value=0.0, value=0.5,  step=0.05)
caida_agua      = st.sidebar.number_input("Caída agua (negativo)", max_value=0.0, value=-0.3, step=0.05)

# Valores derivados
_a                = math.sqrt(max(ancho_carpa ** 2 - alto_hombros ** 2, 0.0))
ancho_ventilacion = round(dist_hileras - _a * 2, 3)
largo_transversal = round(2 * math.sqrt(ancho_carpa ** 2 + alto_hombros ** 2), 3)

st.sidebar.markdown(f"**Ancho ventilación:** {ancho_ventilacion} m")
st.sidebar.markdown(f"**Largo transversal:** {largo_transversal} m")


# ── Helpers de visualización ──────────────────────────────────────────────────

def _plot_hileras(df_vista, pol_shapely=None):
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

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.grid(True)
    ax.legend(title="Bloque / Zona", bbox_to_anchor=(1.01, 1),
              loc="upper left", fontsize=8)
    plt.tight_layout()
    return fig


def _plot_preview(pol_pts, df_hileras, angulo, n_hileras):
    """Polígono + hileras superpuestas. H1 resaltada para identificar punto de inicio."""
    fig, ax = plt.subplots(figsize=(6, 2))

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

df_raw, poligonos_dxf = leer_kmz(ruta_tmp) if es_kmz else leer_hileras_dxf(ruta_tmp)
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

    st.sidebar.subheader("Orientación de hileras")
    dfs_generados = []

    for pol_data in poligonos_dxf:
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
        if inc_key not in st.session_state: st.session_state[inc_key] = True

        def _sl_to_ni(sl=sl_key, ni=ni_key):
            st.session_state[ni] = round(st.session_state[sl], 1)
        def _ni_to_sl(sl=sl_key, ni=ni_key):
            st.session_state[sl] = st.session_state[ni]

        # ── Controles en sidebar (uno por polígono) ───────────────────────────
        if n_pols > 1:
            st.sidebar.markdown(f"**— {pol_nombre} —**")

        st.sidebar.text_input("Nombre del sector", value=pol_nombre, key=sec_key)
        st.sidebar.checkbox("Incluir en cálculo", key=inc_key)
        st.sidebar.caption(f"Ángulo auto-detectado: **{angulo_auto}°**")
        st.sidebar.slider(
            "Ángulo — ajuste rápido", 0.0, 179.5, step=1.0,
            key=sl_key, on_change=_sl_to_ni, help="0°=E-O · 90°=N-S",
        )
        st.sidebar.number_input(
            "Ángulo exacto (°)", 0.0, 179.5, step=0.5,
            key=ni_key, on_change=_ni_to_sl,
        )
        st.sidebar.toggle("Invertir H1 (lado opuesto)", key=inv_key)
        st.sidebar.number_input(
            "Desplazamiento borde (m)", 0.0, float(dist_hileras), step=0.1, key=of_key,
        )
        if n_pols > 1:
            st.sidebar.divider()

        if not st.session_state[inc_key]:
            continue

        angulo_hil    = st.session_state[ni_key]
        offset_inicio = st.session_state[of_key]
        invertir      = st.session_state[inv_key]
        sector_nombre = st.session_state[sec_key] or pol_nombre

        df_pol = generar_hileras_desde_poligono(
            pol_pts, dist_hileras, angulo_hil, largo_minimo,
            sector_nombre, offset_inicio, invertir,
        )

        if df_pol.empty:
            st.warning(f"**{pol_nombre}**: sin hileras con los parámetros actuales.")
            continue

        dfs_generados.append(df_pol)

        # Vista previa individual por polígono
        if n_pols > 1:
            st.subheader(f"Vista previa — {sector_nombre}")
        st.pyplot(_plot_preview(pol_pts, df_pol, angulo_hil, len(df_pol)))
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
    # ── DXF o KMZ con hileras dibujadas: control de orientación H1 ────────────
    st.sidebar.subheader("Orientación de hileras")
    inv_key = "invertir_h1_archivo"
    invertir_h1 = st.sidebar.toggle(
        "Invertir H1 (iniciar desde el lado opuesto)",
        key=inv_key,
        value=False,
        help="Las hileras se ordenan automáticamente de un borde al otro. "
             "Active esto para que H1 quede en el extremo contrario.",
    )
    df_raw = reordenar_hileras(df_raw, invertir=invertir_h1)

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

# ── Cálculos ──────────────────────────────────────────────────────────────────

df_calc = calcular_hileras(
    df_filt, largo_minimo, largo_carpa, dist_plantas,
    merma_hil, merma_trans, ancho_carpa, largo_transversal,
)
df_calc["N_hilera"] = range(1, len(df_calc) + 1)

if df_calc.empty:
    st.warning(f"Todas las hileras son menores al largo mínimo ({largo_minimo} m).")
    st.stop()

df_res = resumen_sectores(df_calc)

# Indicador de filtrado
hileras_excluidas = len(df_filt) - len(df_calc)
msg = f"**{len(df_calc)} hileras a cubicar**"
if hileras_excluidas > 0:
    msg += f" ({hileras_excluidas} excluidas por largo mínimo < {largo_minimo} m)"
st.info(msg)

# ── Totales de materiales ─────────────────────────────────────────────────────

st.subheader("Totales de materiales")
factor_h  = 1 + merma_hil / 100
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("CUMBRERAS (m)",    round(df_calc["Largo_m"].sum() * factor_h, 2))
mc2.metric("HOMBROS (m)",      round(df_calc["Largo_m"].sum() * 2 * factor_h, 2))
mc3.metric("TRANSVERSALES (m)", round(df_calc["Uso_T_m"].sum(), 2))
mc4.metric("PERIMETRO (m)",    round(df_calc["Uso_P_m"].sum(), 2))

# ── Resumen por sector ────────────────────────────────────────────────────────

st.subheader("Resumen por sector")
st.dataframe(df_res, use_container_width=True)

# ── Visualización ─────────────────────────────────────────────────────────────

st.subheader("Vista visual por sector")
sector_vista = st.selectbox("Sector a visualizar", sectores_sel)
df_vista     = df_calc[df_calc["Sector"] == sector_vista]

fig = _plot_hileras(df_vista, pol_shapely)
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

params = (
    empresa, rut, encargado, direccion_cliente, mail, celular,
    especie, variedad, superficie_ha, altura_plantas,
    dist_hileras, dist_plantas, merma_hil, merma_trans,
    ancho_carpa, largo_carpa, largo_minimo, alto_pilares,
    largo_enterrado, alto_hombros, caida_agua,
    ancho_ventilacion, largo_transversal,
)
excel_bytes = crear_excel(df_calc, df_res, params)

st.download_button(
    label="Descargar cubicación en Excel",
    data=excel_bytes,
    file_name="cubicacion_agrocover.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
