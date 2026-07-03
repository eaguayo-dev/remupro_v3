"""
Procesador "Remuneraciones por Mes" (REM).

Qué hace (y por qué es distinto al BRP):
    Cruza planillas de SUELDO — una por mes y por fuente (tipo SEP o tipo SN+PIE),
    cada una en una sola hoja 'Hoja1' — con un ÚNICO archivo de HORAS por
    subvención (que trae una columna 'Mes'). Con eso prorratea cada componente de
    sueldo según las horas SEP / PIE / SN de cada persona en ese mes.

    NO usa datos MINEDUC ni calcula BRP. Reproduce el cálculo del REM antiguo
    (REM_procesado_final.xlsx): hojas SEP / PIE / SN con los sueldos prorrateados,
    más una hoja HORAS consolidada por mes.

Mapeo de entradas (confirmado con el usuario):
    - Archivo 'N SEP.xlsx'   → prorrateo por horas SEP        → hoja SEP.
    - Archivo 'N SNPIE.xlsx' → prorrateo por horas PIE y SN   → hojas PIE y SN.
    - Archivo único de horas → HORAS por persona/mes (col 'Mes' = número de mes).

El prorrateo es: componente * (horas_subvención / horas_totales). El denominador
(horas totales) sale de la columna 'TOTAL HORAS' del archivo de horas.

Cuidados de nómina (punto flotante):
    - Redondeo MEDIO ARRIBA (medio lejos de cero, estilo Excel), no el bancario de
      NumPy. Ver _round_half_up.
    - Al dividir un componente entre PIE y SN, las dos partes se RECONCILIAN para
      sumar exactamente el total a repartir (sin perder/ganar $1 por redondeo).
      Ver _prorratear_snpie.
"""

from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

from config.columns import (
    SPECIAL_SALARY_COLUMNS,
    SALARY_BENEFIT_COLUMNS,
    normalize_rut,
    get_available_columns,
    MESES_NUM_TO_NAME,
)

# Nombres de columnas de horas en el archivo único de horas por subvención.
H_SEP, H_PIE, H_SN, H_TOTAL = 'SEP', 'PIE', 'SN', 'TOTAL HORAS'
# Columnas de identidad que se conservan de la planilla de sueldo.
ID_COLS = ['nombre', 'rut', 'Ubicación', 'Tipo_de_Contrato']
# Todas las columnas de montos que se prorratean (solo se usan las que existan).
_MONTO_COLS = SPECIAL_SALARY_COLUMNS + SALARY_BENEFIT_COLUMNS

# Epsilon minúsculo para contrarrestar el error de representación de punto
# flotante en valores que caen justo en .5 (ej. 124.5 guardado como 124.4999999).
_EPS = 1e-9


def _round_half_up(x) -> np.ndarray:
    """Redondea a entero MEDIO ARRIBA (medio lejos de cero), como Excel.

    NumPy usa redondeo bancario (0.5 al par: 2500.5→2500). Aquí forzamos que el
    .5 SUBA (2500.5→2501), que es lo que espera finanzas y lo que hace Excel.
    """
    x = np.asarray(x, dtype='float64')
    return np.sign(x) * np.floor(np.abs(x) + 0.5 + _EPS)


def cargar_horas(path: Path) -> pd.DataFrame:
    """Carga el archivo único de horas. Devuelve _rut, _mes, SEP, PIE, SN, TOTAL HORAS, Nombre."""
    df = pd.read_excel(path, sheet_name=0, engine='openpyxl')
    if 'Rut' not in df.columns:
        rut_col = next((c for c in df.columns if str(c).strip().lower() == 'rut'), None)
        if rut_col:
            df = df.rename(columns={rut_col: 'Rut'})
    df = df[df['Rut'].notna()].copy()
    df['_rut'] = df['Rut'].map(normalize_rut)
    df['_mes'] = pd.to_numeric(df.get('Mes'), errors='coerce')
    for c in (H_SEP, H_PIE, H_SN, H_TOTAL):
        df[c] = pd.to_numeric(df.get(c, 0), errors='coerce').fillna(0)
    return df


def meses_disponibles_horas(horas_df: pd.DataFrame) -> list:
    """Lista de meses (int) presentes en el archivo de horas."""
    return sorted(int(m) for m in horas_df['_mes'].dropna().unique())


# Catálogo de columnas de monto NORMALIZADO (sin espacios, en mayúsculas). Los
# nombres en las planillas vienen "sucios" (espacios al inicio, mayúsc/minúsc
# mezcladas), así que el match debe ser normalizado, no exacto.
_CATALOGO_NORM = {c.strip().upper() for c in _MONTO_COLS}


def _columnas_monto(df: pd.DataFrame):
    """Devuelve [(columna_origen, nombre_salida)] de las columnas de monto a prorratear.

    Reglas (para nombres "sucios" con espacios/mayúsculas y columnas repetidas):
    1. Matchea ignorando espacios y mayúsculas/minúsculas.
    2. Descarta columnas de PURO TEXTO (ej. 'salud' = nombre FONASA/ISAPRE, no un
       monto): si al convertir a número queda todo vacío, no se prorratea.
    3. Cuando una columna aparece dos veces con el mismo nombre limpio:
       - Si los VALORES son IGUALES → es un APORTE PATRONAL que se anula (haber y
         descuento iguales, ej. MUTUAL). Se cuenta UNA sola vez, como costo del
         empleador → nombre '<base> (aporte patronal)'.
       - Si los valores son DISTINTOS → son conceptos diferentes; se mantienen
         ambos etiquetados '(haber)' / '(descuento)' según los espacios iniciales.

    Además devuelve una lista de NOTAS en texto plano explicando cada columna
    quitada o colapsada (para transparencia con el usuario).

    Retorna: (salida, notas) — salida = [(columna_origen, nombre_salida), ...].
    """
    notas = []
    matched = [c for c in df.columns if str(c).strip().upper() in _CATALOGO_NORM]

    # Regla 2: separar numéricas de texto. El texto se descarta (no es un monto).
    numericas, texto = [], []
    for c in matched:
        if pd.to_numeric(df[c], errors='coerce').notna().any():
            numericas.append(c)
        else:
            texto.append(c)

    # Agrupar numéricas por nombre limpio, conservando el orden de aparición.
    grupos = {}
    for c in numericas:
        grupos.setdefault(str(c).strip(), []).append(c)

    # Notas de columnas de texto descartadas (ej. 'salud' = FONASA/ISAPRE).
    for c in texto:
        base = str(c).strip()
        # Buscar la columna numérica con el mismo nombre (ignorando mayúsculas).
        match_num = next((b for b in grupos if b.upper() == base.upper()), None)
        if match_num:
            notas.append(
                f"Se quitó '{c}': sumaba 0 (es texto, no un monto). El monto real "
                f"de {match_num} está en la otra columna, que sí se conservó."
            )
        else:
            notas.append(f"Se quitó la columna '{c}': es texto, no un monto.")

    salida = []
    for base, cols in grupos.items():
        if len(cols) == 1:
            salida.append((cols[0], base))
            continue
        vals = [pd.to_numeric(df[c], errors='coerce').fillna(0) for c in cols]
        iguales = all(vals[0].equals(v) for v in vals[1:])
        if iguales:
            # Regla 3a: aporte patronal → una sola columna (costo del empleador).
            salida.append((cols[0], f"{base} (aporte patronal)"))
            notas.append(
                f"'{base}': venía DOS veces con los mismos montos (una como haber y "
                f"otra como descuento). Es un aporte patronal que se anula entre sí, "
                f"así que se contó UNA sola vez como '{base} (aporte patronal)'."
            )
        else:
            # Regla 3b: conceptos distintos → ambos, etiquetados por origen.
            for c in cols:
                con_espacios = str(c) != str(c).lstrip()
                etiqueta = 'descuento' if con_espacios else 'haber'
                salida.append((c, f"{base} ({etiqueta})"))
            notas.append(
                f"'{base}': venía DOS veces con montos DISTINTOS; se mantuvieron "
                f"ambas, etiquetadas como '(haber)' y '(descuento)'."
            )
    return salida, notas


def _cargar_salarios(salary_path: Path, horas_mes: pd.DataFrame):
    """Carga Hoja1, cruza por RUT con las horas del mes. Devuelve (merged, [(origen, salida)])."""
    sal = pd.read_excel(salary_path, sheet_name='Hoja1', engine='openpyxl')
    sal = sal[sal['rut'].notna() & (sal['rut'].astype(str).str.strip() != '')].copy()
    sal['_rut'] = sal['rut'].map(normalize_rut)
    # Horas agregadas por RUT (por si una persona tiene varias filas en el mes).
    hg = horas_mes.groupby('_rut')[[H_SEP, H_PIE, H_SN, H_TOTAL]].sum()
    merged = sal.merge(hg, on='_rut', how='left')
    for c in (H_SEP, H_PIE, H_SN, H_TOTAL):
        merged[c] = merged[c].fillna(0)
    montos, notas = _columnas_monto(merged)
    return merged, montos, notas


def _base_out(merged: pd.DataFrame) -> pd.DataFrame:
    """Arma las columnas de identidad + horas comunes a cada hoja de salida."""
    out = pd.DataFrame(index=merged.index)
    for idc in ID_COLS:
        if idc in merged.columns:
            out[idc] = merged[idc].values
    out['HORAS_SEP'] = merged[H_SEP].values
    out['HORAS_PIE'] = merged[H_PIE].values
    out['HORAS_SN'] = merged[H_SN].values
    out['HORAS_TOTAL'] = merged[H_TOTAL].values
    return out.reset_index(drop=True)


def _ratio(merged: pd.DataFrame, hours_col: str) -> np.ndarray:
    """Razón de reparto = horas_subvención / horas_totales (0 si no hay horas)."""
    denom = merged[H_TOTAL].replace(0, np.nan)
    return (merged[hours_col] / denom).replace([np.inf, -np.inf, np.nan], 0).values


def _prorratear_sep(salary_path: Path, horas_mes: pd.DataFrame):
    """Planilla SEP → prorrateo por horas SEP (una sola parte, redondeo medio arriba).

    Retorna (out, notas).
    """
    merged, montos, notas = _cargar_salarios(salary_path, horas_mes)
    r = _ratio(merged, H_SEP)
    out = _base_out(merged)
    for src, name in montos:
        v = pd.to_numeric(merged[src], errors='coerce').fillna(0).values
        out[name] = _round_half_up(v * r).astype('int64')
    return out, notas


def _prorratear_snpie(salary_path: Path, horas_mes: pd.DataFrame):
    """Planilla SN+PIE → dos hojas (PIE y SN) RECONCILIADAS.

    Para cada componente, las partes PIE y SN suman EXACTAMENTE el total a
    repartir T = round(componente * (PIE+SN)/total). El residuo de redondeo (a lo
    más $1) se asigna a la parte que quedó más lejos de su entero (resto mayor).
    """
    merged, montos, notas = _cargar_salarios(salary_path, horas_mes)
    r_pie = _ratio(merged, H_PIE)
    r_sn = _ratio(merged, H_SN)
    out_pie = _base_out(merged)
    out_sn = _base_out(merged)
    for src, name in montos:
        v = pd.to_numeric(merged[src], errors='coerce').fillna(0).values
        pie_raw = v * r_pie
        sn_raw = v * r_sn
        # Total a repartir entre PIE y SN (redondeado medio arriba).
        T = _round_half_up(pie_raw + sn_raw)
        # Redondeo independiente de cada parte...
        pie0 = _round_half_up(pie_raw)
        sn0 = _round_half_up(sn_raw)
        # ...y ajuste del residuo para que pie+sn == T (cuadre exacto al peso).
        resid = T - pie0 - sn0
        dist_pie = np.abs(pie_raw - pie0)  # cuánto se redondeó cada parte
        dist_sn = np.abs(sn_raw - sn0)
        dar_a_pie = dist_pie >= dist_sn     # el residuo va a la más redondeada
        pie = pie0 + np.where(dar_a_pie, resid, 0.0)
        sn = sn0 + np.where(dar_a_pie, 0.0, resid)
        out_pie[name] = pie.astype('int64')
        out_sn[name] = sn.astype('int64')
    return out_pie, out_sn, notas


def procesar(
    sep_files: Dict[int, Path],
    snpie_files: Dict[int, Path],
    horas_path: Path,
) -> Dict[str, object]:
    """
    Procesa todos los meses y devuelve las hojas del resultado.

    Args:
        sep_files:   {numero_mes: ruta} de las planillas tipo SEP.
        snpie_files: {numero_mes: ruta} de las planillas tipo SN+PIE.
        horas_path:  ruta al archivo único de horas (con columna 'Mes').

    Returns:
        Dict con DataFrames 'SEP', 'PIE', 'SN', 'HORAS' y la clave '_avisos'
        (lista de advertencias, ej. meses sin horas).
    """
    horas = cargar_horas(horas_path)
    meses_horas = set(meses_disponibles_horas(horas))

    sep_out, pie_out, sn_out = [], [], []
    avisos = []
    notas_columnas = []  # notas de columnas quitadas/colapsadas (dedup al final)

    todos_meses = sorted(set(sep_files) | set(snpie_files))
    for mes in todos_meses:
        nombre_mes = MESES_NUM_TO_NAME.get(f'{mes:02d}', str(mes))
        if mes not in meses_horas:
            avisos.append(
                f"Mes {mes} ({nombre_mes}): no hay horas en el archivo de horas → "
                f"no se puede prorratear, se omite."
            )
            continue
        horas_mes = horas[horas['_mes'] == mes]

        if mes in sep_files:
            df, notas = _prorratear_sep(sep_files[mes], horas_mes)
            notas_columnas.extend(notas)
            df.insert(0, 'MES_NUM', f'{mes:02d}')
            df.insert(0, 'MES', nombre_mes)
            sep_out.append(df)

        if mes in snpie_files:
            dfp, dfs, notas = _prorratear_snpie(snpie_files[mes], horas_mes)
            notas_columnas.extend(notas)
            for d in (dfp, dfs):
                d.insert(0, 'MES_NUM', f'{mes:02d}')
                d.insert(0, 'MES', nombre_mes)
            pie_out.append(dfp)
            sn_out.append(dfs)

    # Dedup de notas conservando el orden (las mismas columnas se repiten por mes).
    notas_columnas = list(dict.fromkeys(notas_columnas))

    def _concat(lst):
        return pd.concat(lst, ignore_index=True) if lst else pd.DataFrame()

    # Hoja HORAS consolidada (una fila por persona/mes) — vista "meses x horas".
    horas_view = horas[['_mes', 'Rut', 'Nombre', H_SEP, H_PIE, H_SN, H_TOTAL]].copy()
    horas_view = horas_view.rename(columns={
        '_mes': 'MES_NUM', H_SEP: 'HORAS_SEP', H_PIE: 'HORAS_PIE',
        H_SN: 'HORAS_SN', H_TOTAL: 'HORAS_TOTAL',
    })
    horas_view['MES_NUM'] = horas_view['MES_NUM'].map(
        lambda m: f'{int(m):02d}' if pd.notna(m) else ''
    )
    horas_view.insert(0, 'MES', horas_view['MES_NUM'].map(
        lambda m: MESES_NUM_TO_NAME.get(m, m)
    ))
    horas_view = horas_view.sort_values(['Rut', 'MES_NUM'])

    return {
        'SEP': _concat(sep_out),
        'PIE': _concat(pie_out),
        'SN': _concat(sn_out),
        'HORAS': horas_view,
        '_avisos': avisos,
        '_notas_columnas': notas_columnas,
    }
