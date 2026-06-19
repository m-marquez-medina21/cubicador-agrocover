# calculos.py — Fórmulas de cubicación de materiales.
# Modificar aquí si cambia el cálculo de Centrales, Carpas, Transversales, Perimetro o Plantas.
# Entrada : DataFrame con Largo_m + parámetros numéricos
# Salida  : mismo DataFrame con columnas calculadas

import pandas as pd


def calcular_hileras(
    df: pd.DataFrame,
    l_minimo: float,
    l_carpa: float,
    d_plantas: float,
    m_hil: float,
    m_trans: float,
    ancho_c: float,
    l_trans: float,
    d_hil: float = 3.0,
) -> pd.DataFrame:
    """Aplica el filtro de largo mínimo y calcula todas las columnas de cubicación."""
    df = df[df["Largo_m"] >= l_minimo].copy()
    factor_h = 1 + m_hil   / 100
    factor_t = 1 + m_trans  / 100

    df["N_plantas"]      = (df["Largo_m"] / d_plantas).round(2)
    df["Centrales"]      = (df["Largo_m"] // l_carpa).astype(int)
    df["Centrales_Adic"] = 2
    df["Carpas"]         = (df["Centrales"] + 1).where(df["Centrales"] > 0, 0)
    df["Uso_C_m2"]       = (df["Largo_m"] * ancho_c).round(2)
    df["Perim_cant"]     = 2
    # Contribución de cada hilera al lado corto del perímetro (lado ancho del campo)
    df["Uso_P_m"]        = round(d_hil * 2 * factor_h, 2)
    df["Trans_cant"]     = df["Centrales"] + 2
    df["Trans_largo"]    = l_trans
    df["Uso_T_m"]        = (df["Trans_cant"] * l_trans * factor_t).round(2)

    return df


def resumen_sectores(df: pd.DataFrame, d_hil: float = 3.0, m_hil: float = 0.0) -> pd.DataFrame:
    """Agrega las métricas de cubicación por sector."""
    factor_h = 1 + m_hil / 100
    res = (
        df.groupby("Sector")
        .agg(
            Hileras         = ("Largo_m",        "count"),
            Largo_total_m   = ("Largo_m",        "sum"),
            Largo_prom_m    = ("Largo_m",        "mean"),
            Largo_min_m     = ("Largo_m",        "min"),
            Largo_max_m     = ("Largo_m",        "max"),
            N_plantas_total = ("N_plantas",      "sum"),
            Centrales_total = ("Centrales",      "sum"),
            Cent_Adic_total = ("Centrales_Adic", "sum"),
            Carpas_total    = ("Carpas",         "sum"),
            Uso_C_total     = ("Uso_C_m2",       "sum"),
            Trans_total     = ("Trans_cant",     "sum"),
            Uso_T_total     = ("Uso_T_m",        "sum"),
        )
        .reset_index()
    )
    # Perímetro = 2 lados largos (hilera más larga × 2) + 2 lados cortos (n_hileras × dist × 2)
    res["Uso_P_total"] = (
        (res["Largo_max_m"] * 2 + res["Hileras"] * d_hil * 2) * factor_h
    ).round(2)
    return res.round(2)
