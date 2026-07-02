"""
Procesador REM - Calcula horas disponibles por docente desde archivo REM.

El archivo REM (Remuneraciones) contiene líneas de contrato por persona,
con detalle de horas por tipo (Planta, SEP, PIE, Contrata, EIB).
Cada persona puede tener múltiples filas (una por contrato).
"""

import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from config.columns import normalize_rut, clean_columns, classify_contract


def _extract_rbd(departamento: str) -> str:
    """
    Extrae el RBD (rol del establecimiento) del campo departamento.

    El RBD viene escrito de formas muy diversas dentro del texto del departamento,
    así que se prueban varios patrones en orden de confiabilidad hasta encontrar uno.
    Ej: 'ESCUELA X RBD 6710-5' → '6710'.
    """
    dep = str(departamento).strip()

    # Patrón más explícito: "RBD XXXX".
    m = re.search(r'RBD\s*(\d+)', dep, re.IGNORECASE)
    if m:
        return m.group(1)

    # Variantes de "número": "Nº XXX", "N°XXX", "Nro XXX".
    m = re.search(r'(?:Nº|N°|Nro\.?)\s*(\d+)', dep)
    if m:
        return m.group(1)

    # Patrón "F XXX" al final del texto (ej: "DAME LA MANO F 838").
    m = re.search(r'\bF\s+(\d+)\s*$', dep)
    if m:
        return m.group(1)

    # La Dirección de Educación no es un establecimiento: se marca como 'DEM'.
    if 'EDUCACION' in dep.upper() or 'EDUCACIÓN' in dep.upper():
        return 'DEM'

    # Sin coincidencias: se devuelve vacío (RBD desconocido).
    return ''


class REMProcessor:
    """Procesador de archivos REM para cálculo de horas disponibles."""

    # Tope legal de horas de contrato por persona; base para calcular horas disponibles.
    MAX_HORAS = 44

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        # Alertas generadas al detectar personas que superan el tope de horas.
        self.alertas_horas: List[Dict] = []

    def process(self, file_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict]]:
        """
        Procesa archivo REM y retorna resumen de horas.

        Args:
            file_path: Ruta al archivo REM (CSV o Excel)

        Returns:
            Tupla (df_resumen_persona, df_detalle, alertas)
            - df_resumen_persona: una fila por RUT con horas totales por tipo
            - df_detalle: todas las filas originales con clasificación
            - alertas: lista de alertas (>44 hrs, etc.)
        """
        # Pipeline: cargar → normalizar (RUT/tipo/RBD) → agregar por persona → alertar.
        df = self._load_file(file_path)
        df = self._normalize(df)
        df_resumen = self._aggregate(df)
        self.alertas_horas = self._check_limits(df_resumen)
        return df_resumen, df, self.alertas_horas

    def _load_file(self, path: Path) -> pd.DataFrame:
        """Carga archivo REM (CSV o Excel)."""
        suffix = path.suffix.lower()
        if suffix == '.csv':
            # Los CSV chilenos suelen venir en latin-1; se intenta utf-8 y se cae a latin-1.
            try:
                df = pd.read_csv(str(path), encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(str(path), encoding='latin-1')
        elif suffix in ('.xlsx', '.xls'):
            df = pd.read_excel(str(path), engine='openpyxl')
        else:
            raise ValueError(f"Formato no soportado: {suffix}. Use CSV o Excel.")

        df = clean_columns(df)

        if df.empty:
            raise ValueError("El archivo REM está vacío.")

        self.logger.info(f"REM cargado: {len(df)} filas, {len(df.columns)} columnas")
        return df

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normaliza el DataFrame REM: crea columnas estándar a partir de las del archivo.

        Los archivos REM traen encabezados variables, así que cada columna clave se
        localiza buscando por nombre y se vuelca a un nombre interno fijo (RUT_NORM,
        TIPO_SUBVENCION, HORAS, etc.). Las columnas obligatorias faltantes cortan el proceso.
        """
        # Columna RUT (obligatoria): se normaliza para poder cruzar y agrupar por persona.
        rut_col = None
        for col in df.columns:
            if col.lower().strip() == 'rut':
                rut_col = col
                break
        if not rut_col:
            raise ValueError("No se encontró columna 'rut' en el archivo REM.")

        df['RUT_NORM'] = df[rut_col].apply(normalize_rut)

        # Columna tipo de contrato (obligatoria): se traduce a tipo de subvención.
        tipo_col = None
        for col in df.columns:
            if 'tipocontrato' in col.lower().replace(' ', ''):
                tipo_col = col
                break
        if not tipo_col:
            raise ValueError("No se encontró columna 'tipocontrato' en el archivo REM.")

        df['TIPO_SUBVENCION'] = df[tipo_col].apply(classify_contract)

        # Columna jornada (obligatoria): son las horas del contrato de esa fila.
        jornada_col = None
        for col in df.columns:
            if col.lower().strip() == 'jornada':
                jornada_col = col
                break
        if not jornada_col:
            raise ValueError("No se encontró columna 'jornada' en el archivo REM.")
        # Horas como entero; valores no numéricos se toman como 0.
        df['HORAS'] = pd.to_numeric(df[jornada_col], errors='coerce').fillna(0).astype(int)

        # Columna nombre (opcional).
        nombre_col = None
        for col in df.columns:
            if col.lower().strip() == 'nombre':
                nombre_col = col
                break
        if nombre_col:
            df['NOMBRE'] = df[nombre_col].astype(str).str.strip()

        # Columna departamento (opcional): de ella se extrae el RBD y el nombre de escuela.
        depto_col = None
        for col in df.columns:
            if 'departamento' in col.lower():
                depto_col = col
                break
        if depto_col:
            df['RBD_REM'] = df[depto_col].apply(_extract_rbd)
            df['ESCUELA_REM'] = df[depto_col].astype(str).str.strip()

        # Columna escalafón (opcional): categoría del cargo.
        esc_col = None
        for col in df.columns:
            if 'escalafon' in col.lower():
                esc_col = col
                break
        if esc_col:
            df['ESCALAFON'] = df[esc_col].astype(str).str.strip()

        return df

    def _aggregate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Agrega las horas por persona y tipo de subvención (una fila por RUT).

        Como cada persona puede tener varias filas (una por contrato), aquí se suman
        sus horas por tipo y se calculan totales, horas disponibles (44 - total) y si
        excede el tope.
        """
        # Pivotar a formato ancho: una fila por RUT con columnas SEP/PIE/NORMAL/EIB.
        pivot = df.groupby(['RUT_NORM', 'TIPO_SUBVENCION'])['HORAS'].sum().unstack(fill_value=0)

        # Garantizar las cuatro columnas de tipo aunque falte alguna en los datos.
        for col in ['SEP', 'PIE', 'NORMAL', 'EIB']:
            if col not in pivot.columns:
                pivot[col] = 0

        pivot = pivot.reset_index()
        # Total de horas y derivados: disponibles (nunca negativo) y bandera de exceso.
        pivot['TOTAL'] = pivot['SEP'] + pivot['PIE'] + pivot['NORMAL'] + pivot['EIB']
        pivot['DISPONIBLE'] = (self.MAX_HORAS - pivot['TOTAL']).clip(lower=0)
        pivot['EXCEDE'] = (pivot['TOTAL'] > self.MAX_HORAS)

        # Adjuntar el nombre (primero por RUT), si venía en el archivo.
        if 'NOMBRE' in df.columns:
            nombres = df.groupby('RUT_NORM')['NOMBRE'].first().reset_index()
            pivot = pivot.merge(nombres, on='RUT_NORM', how='left')

        # Escalafón: una persona puede tener varios; se unen separados por coma.
        if 'ESCALAFON' in df.columns:
            escalafones = df.groupby('RUT_NORM')['ESCALAFON'].apply(
                lambda x: ', '.join(sorted(x.unique()))
            ).reset_index()
            pivot = pivot.merge(escalafones, on='RUT_NORM', how='left')

        # Escuelas: una persona puede trabajar en varias; se unen con ' | '.
        if 'ESCUELA_REM' in df.columns:
            escuelas = df.groupby('RUT_NORM')['ESCUELA_REM'].apply(
                lambda x: ' | '.join(sorted(x.unique()))
            ).reset_index()
            pivot = pivot.merge(escuelas, on='RUT_NORM', how='left')

        # Dejar primero las columnas conocidas en un orden fijo y el resto al final.
        cols_order = ['RUT_NORM', 'NOMBRE', 'ESCALAFON', 'ESCUELA_REM',
                      'SEP', 'PIE', 'NORMAL', 'EIB', 'TOTAL', 'DISPONIBLE', 'EXCEDE']
        cols_exist = [c for c in cols_order if c in pivot.columns]
        cols_rest = [c for c in pivot.columns if c not in cols_exist]
        pivot = pivot[cols_exist + cols_rest]

        self.logger.info(f"REM agregado: {len(pivot)} personas, "
                         f"{pivot['EXCEDE'].sum()} exceden 44 hrs")
        return pivot

    def _check_limits(self, df_resumen: pd.DataFrame) -> List[Dict]:
        """Genera una alerta por cada persona que excede el tope de 44 horas."""
        alertas = []

        # Recorrer solo las filas marcadas como EXCEDE en la agregación.
        exceden = df_resumen[df_resumen['EXCEDE']]
        for _, row in exceden.iterrows():
            alertas.append({
                'tipo': 'excede_44',
                'rut': row['RUT_NORM'],
                'nombre': row.get('NOMBRE', ''),
                'total': int(row['TOTAL']),
                'exceso': int(row['TOTAL'] - self.MAX_HORAS),
                'detalle': (f"SEP:{int(row['SEP'])} + PIE:{int(row['PIE'])} + "
                            f"Normal:{int(row['NORMAL'])} + EIB:{int(row['EIB'])} "
                            f"= {int(row['TOTAL'])} hrs"),
            })

        if alertas:
            self.logger.warning(f"REM: {len(alertas)} personas exceden 44 horas")

        return alertas
