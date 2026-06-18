import streamlit as st
import ezdxf
import tempfile
import pandas as pd
from io import BytesIO
import matplotlib.pyplot as plt
from PIL import Image
from shapely.geometry import Polygon, LineString
from streamlit_drawable_canvas import st_canvas

st.set_page_config(page_title="Cubicador Agrocover", layout="wide")

st.title("🌱 Cubicador Agrocover")
st.write("Versión 1.2 - Dibujar polígono y cubicar hileras")

archivo = st.file_uploader("Seleccione archivo DXF", type=["dxf"])

st.sidebar.header("Parámetros de cálculo")

distancia_plantas = st.sidebar.number_input("Distancia entre plantas (m)", min_value=0.1, value=2.0, step=0.1)
distancia_centrales = st.sidebar.number_input("Distancia centrales (m)", min_value=0.1, value=12.0, step=0.5)
factor_merma = st.sidebar.number_input("Merma / factor adicional (%)", min_value=0.0, value=0.0, step=1.0)

def puntos_lwpolyline(ent):
    return [(p[0], p[1]) for p in ent.get_points("xy")]

def convertir_excel(df_resumen, df_hileras):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_resumen.to_excel(writer, index=False, sheet_name="Resumen")
        df_hileras.to_excel(writer, index=False, sheet_name="Hileras")
    return output.getvalue()

def leer_hileras_dxf(ruta_dxf):
    doc = ezdxf.readfile(ruta_dxf)
    msp = doc.modelspace()
    hileras = []

    for e in msp:
        if e.dxftype() == "INSERT":
            try:
                block = doc.blocks[e.dxf.name]
            except Exception:
                continue

            for ent in block:
                if ent.dxftype() == "LWPOLYLINE" and "Sect" in ent.dxf.layer:
                    pts = puntos_lwpolyline(ent)
                    if len(pts) >= 2:
                        line = LineString(pts)
                        hileras.append({
                            "Sector": ent.dxf.layer,
                            "Largo_m": line.length,
                            "Linea": line,
                            "Puntos": pts
                        })

    return hileras

def escalar_puntos(puntos, minx, miny, escala, alto_canvas):
    salida = []
    for x, y in puntos:
        sx = (x - minx) * escala
        sy = alto_canvas - ((y - miny) * escala)
        salida.append((sx, sy))
    return salida

def desescalar_puntos(puntos_canvas, minx, miny, escala, alto_canvas):
    salida = []
    for x, y in puntos_canvas:
        dx = x / escala + minx
        dy = (alto_canvas - y) / escala + miny
        salida.append((dx, dy))
    return salida

if archivo:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        tmp.write(archivo.read())
        ruta_dxf = tmp.name

    hileras = leer_hileras_dxf(ruta_dxf)

    st.success(f"Archivo cargado correctamente: {archivo.name}")
    st.write("Hileras detectadas:", len(hileras))

    if not hileras:
        st.error("No se detectaron hileras en el DXF.")
    else:
        todos_x = []
        todos_y = []

        for h in hileras:
            for x, y in h["Puntos"]:
                todos_x.append(x)
                todos_y.append(y)

        minx, maxx = min(todos_x), max(todos_x)
        miny, maxy = min(todos_y), max(todos_y)

        ancho_canvas = 1000
        alto_canvas = 700

        escala_x = ancho_canvas / (maxx - minx)
        escala_y = alto_canvas / (maxy - miny)
        escala = min(escala_x, escala_y) * 0.95

        st.subheader("1. Dibuja el polígono del sector a cubicar")

        st.info(
            "Usa la herramienta polígono del canvas. "
            "Dibuja el contorno del sector sobre las hileras y luego revisa la cubicación abajo."
        )

        fig, ax = plt.subplots(figsize=(12, 8))

        for h in hileras:
            pts_canvas = escalar_puntos(h["Puntos"], minx, miny, escala, alto_canvas)
            xs = [p[0] for p in pts_canvas]
            ys = [p[1] for p in pts_canvas]
            ax.plot(xs, ys, linewidth=0.5)

        ax.set_xlim(0, ancho_canvas)
        ax.set_ylim(alto_canvas, 0)
        ax.axis("off")

        fondo = BytesIO()
        fig.savefig(fondo, format="png", bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        fondo.seek(0)

        imagen_fondo = Image.open(fondo)

        canvas_result = st_canvas(
            fill_color="rgba(255, 0, 0, 0.15)",
            stroke_width=3,
            stroke_color="#ff0000",
            background_image=imagen_fondo,
            update_streamlit=True,
            height=alto_canvas,
            width=ancho_canvas,
            drawing_mode="polygon",
            key="canvas"
        )

        if canvas_result.json_data is not None:
            objetos = canvas_result.json_data["objects"]

            if len(objetos) == 0:
                st.warning("Dibuja un polígono para cubicar.")
            else:
                obj = objetos[-1]

                if "path" not in obj:
                    st.warning("Aún no se detecta un polígono válido.")
                else:
                    puntos_canvas = []

                    for item in obj["path"]:
                        if len(item) >= 3 and item[0] in ["M", "L"]:
                            puntos_canvas.append((item[1], item[2]))

                    if len(puntos_canvas) < 3:
                        st.warning("El polígono debe tener al menos 3 puntos.")
                    else:
                        puntos_dxf = desescalar_puntos(
                            puntos_canvas,
                            minx,
                            miny,
                            escala,
                            alto_canvas
                        )

                        poligono = Polygon(puntos_dxf)

                        filas_hileras = []

                        for h in hileras:
                            punto_medio = h["Linea"].interpolate(0.5, normalized=True)

                            if poligono.contains(punto_medio):
                                filas_hileras.append({
                                    "N_hilera": len(filas_hileras) + 1,
                                    "Sector_origen": h["Sector"],
                                    "Largo_m": round(h["Largo_m"], 2)
                                })

                        df_hileras = pd.DataFrame(filas_hileras)

                        if df_hileras.empty:
                            st.warning("No se encontraron hileras dentro del polígono dibujado.")
                        else:
                            total_largo = df_hileras["Largo_m"].sum()
                            largo_con_merma = total_largo * (1 + factor_merma / 100)

                            df_hileras["N_plantas"] = (df_hileras["Largo_m"] / distancia_plantas).round(2)
                            df_hileras["Centrales"] = (df_hileras["Largo_m"] // distancia_centrales).astype(int)
                            df_hileras["Carpas"] = df_hileras["Centrales"] + 1

                            resumen = pd.DataFrame([{
                                "Superficie_aprox_ha": round(poligono.area / 10000, 2),
                                "Perimetro_m": round(poligono.length, 2),
                                "Cantidad_hileras": len(df_hileras),
                                "Largo_total_hileras_m": round(total_largo, 2),
                                "Largo_total_con_merma_m": round(largo_con_merma, 2),
                                "Plantas_estimadas": round(largo_con_merma / distancia_plantas, 2),
                                "Centrales": int(largo_con_merma // distancia_centrales),
                                "Carpas": int(largo_con_merma // distancia_centrales) + len(df_hileras)
                            }])

                            st.subheader("2. Resumen de cubicación")
                            st.dataframe(resumen, use_container_width=True)

                            st.subheader("3. Detalle de hileras dentro del polígono")
                            st.dataframe(df_hileras, use_container_width=True)

                            excel = convertir_excel(resumen, df_hileras)

                            st.download_button(
                                label="📥 Descargar cubicación en Excel",
                                data=excel,
                                file_name="cubicacion_poligono_agrocover.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )