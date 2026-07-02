"""
Procesador de Lote Anual - Procesa 12 meses de SEP+PIE+BRP(+EIB) de golpe.

Recibe ~48 archivos (4 por mes: web, sep, pie, eib opcional),
los clasifica por mes y tipo, y genera un Excel consolidado anual.
"""

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from config.columns import (
    classify_contract,
    detect_month_from_filename,
    detect_file_type,
    normalize_rut,
    MESES_NUM_TO_NAME,
)
from processors.sep import SEPProcessor
from processors.pie import PIEProcessor
from processors.eib import EIBProcessor
from processors.brp import BRPProcessor


@dataclass
class MonthlyFileSet:
    """Conjunto de archivos para un mes."""
    month: str  # '01'-'12'
    month_name: str
    sep: Optional[Tuple[str, Path]] = None   # (filename, path)
    pie: Optional[Tuple[str, Path]] = None
    eib: Optional[Tuple[str, Path]] = None
    web: Optional[Tuple[str, Path]] = None
    pre_processed: bool = False  # True = sep/pie ya son archivos procesados sintéticos


class AnualBatchProcessor:
    """Procesador de lote anual: 12 meses de SEP+PIE+BRP+EIB."""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        # Avisos de clasificación de archivos (mes/tipo no detectado, etc.) para la UI.
        self.classification_warnings: List[str] = []

    def classify_files(
        self, files: List[Tuple[str, Path]]
    ) -> Dict[str, MonthlyFileSet]:
        """
        Clasifica archivos por mes y tipo.

        Un web sostenedor sin mes detectado se trata como 'shared_web':
        se asigna automáticamente a todos los meses que no tengan web propio.

        Si un archivo no es reconocido por nombre, se intenta detectar como:
        1. Archivo de horas reales (columnas Mes + Rut + SEP + PIE + SN)
        2. Archivo anual consolidado (columnas Periodo + Tipo_de_Contrato)

        Args:
            files: Lista de (filename, path) tuplas.

        Returns:
            Dict con keys '01'-'12', valores MonthlyFileSet.
        """
        monthly: Dict[str, MonthlyFileSet] = {}
        shared_web: Optional[Tuple[str, Path]] = None
        unclassified: List[Tuple[str, Path]] = []
        self._anual_file = None
        self._horas_file: Optional[Tuple[str, Path]] = None
        self.classification_warnings = []

        for filename, path in files:
            month = detect_month_from_filename(filename)
            ftype = detect_file_type(filename)

            if not ftype:
                unclassified.append((filename, path))
                continue

            # Web sin mes detectado → web compartido
            if ftype == 'web' and not month:
                shared_web = (filename, path)
                self.logger.info(f"Web compartido detectado: {filename}")
                continue

            if not month:
                msg = (
                    f"`{filename}` (tipo {ftype.upper()}) no tiene un mes reconocible "
                    f"en el nombre; NO se incluirá en el consolidado. "
                    f"Renómbralo incluyendo el mes (ej: {ftype}_enero_2026.xlsx)."
                )
                self.classification_warnings.append(msg)
                self.logger.warning(f"No se pudo clasificar mes: {filename} (tipo={ftype})")
                continue

            if month not in monthly:
                month_name = MESES_NUM_TO_NAME.get(month, month)
                monthly[month] = MonthlyFileSet(
                    month=month, month_name=month_name
                )

            ms = monthly[month]
            entry = (filename, path)
            if ftype == 'sep':
                ms.sep = entry
            elif ftype == 'pie':
                ms.pie = entry
            elif ftype == 'eib':
                ms.eib = entry
            elif ftype == 'web':
                ms.web = entry

        # Clasificar archivos no reconocidos: horas reales, anual consolidado
        remaining_unclassified: List[Tuple[str, Path]] = []
        for filename, path in unclassified:
            if self._is_horas_file(path):
                self.logger.info(f"Archivo de horas por subvención detectado: {filename}")
                self._horas_file = (filename, path)
            elif self._looks_like_horas_missing_mes(path):
                msg = (
                    f"`{filename}` parece un archivo de horas (tiene RUT/SEP/PIE/SN) "
                    f"pero le falta la columna **Mes**; sin ella no se pueden asignar "
                    f"las horas a cada mes y se usarán horas estimadas por tipo de "
                    f"contrato. Agrega una columna 'Mes'."
                )
                self.classification_warnings.append(msg)
                self.logger.warning(f"Archivo de horas sin columna Mes: {filename}")
            else:
                remaining_unclassified.append((filename, path))

        # Intentar detectar archivo anual consolidado
        for filename, path in remaining_unclassified:
            if self._is_anual_consolidado(path):
                self.logger.info(f"Archivo anual consolidado detectado: {filename}")
                self._anual_file = (filename, path)
                horas_path = self._horas_file[1] if self._horas_file else None
                anual_months = self._split_anual_file(path, horas_path=horas_path)
                for month_num, ms in anual_months.items():
                    if month_num not in monthly:
                        monthly[month_num] = ms
                    else:
                        # No sobreescribir archivos SEP/PIE ya asignados por nombre
                        existing = monthly[month_num]
                        if not existing.sep:
                            existing.sep = ms.sep
                            existing.pre_processed = ms.pre_processed
                        if not existing.pie:
                            existing.pie = ms.pie
                            existing.pre_processed = ms.pre_processed
                break  # Solo un archivo anual
            else:
                self.classification_warnings.append(
                    f"`{filename}` no se pudo clasificar (ni tipo SEP/PIE/EIB/WEB por "
                    f"nombre, ni archivo de horas, ni anual consolidado); se ignorará."
                )
                self.logger.warning(
                    f"No se pudo clasificar tipo: {filename}"
                )

        # Asignar web compartido a meses que no tengan web propio
        if shared_web:
            for month_num, ms in monthly.items():
                if not ms.web:
                    ms.web = shared_web
                    self.logger.info(
                        f"Web compartido asignado a {ms.month_name}"
                    )

        self._shared_web = shared_web
        return monthly

    def _is_horas_file(self, path: Path) -> bool:
        """Detecta si un archivo es de horas por subvención (Mes + Rut + SEP + PIE + SN)."""
        try:
            ext = path.suffix.lower()
            if ext == '.csv':
                df = pd.read_csv(str(path), nrows=5, encoding='latin-1')
            else:
                df = pd.read_excel(str(path), nrows=5, engine='openpyxl')
            cols_lower = {str(c).lower().strip() for c in df.columns}
            has_mes = any('mes' == c for c in cols_lower)
            has_rut = any('rut' in c for c in cols_lower)
            has_sep = any(c == 'sep' for c in cols_lower)
            has_pie = any(c == 'pie' for c in cols_lower)
            has_sn = any(c == 'sn' for c in cols_lower)
            return has_mes and has_rut and has_sep and has_pie and has_sn
        except Exception as e:
            self.logger.debug(f"Error detectando archivo de horas: {e}")
            return False

    def _looks_like_horas_missing_mes(self, path: Path) -> bool:
        """
        Detecta un archivo que parece de horas (RUT + SEP + PIE + SN) pero SIN 'Mes'.

        Sirve para avisar al usuario: sin la columna Mes no se pueden asignar las
        horas reales a cada mes y el sistema cae a horas estimadas.
        """
        try:
            ext = path.suffix.lower()
            if ext == '.csv':
                df = pd.read_csv(str(path), nrows=5, encoding='latin-1')
            else:
                df = pd.read_excel(str(path), nrows=5, engine='openpyxl')
            cols_lower = {str(c).lower().strip() for c in df.columns}
            has_mes = any('mes' == c for c in cols_lower)
            has_rut = any('rut' in c for c in cols_lower)
            has_sep = any(c == 'sep' for c in cols_lower)
            has_pie = any(c == 'pie' for c in cols_lower)
            has_sn = any(c == 'sn' for c in cols_lower)
            return has_rut and has_sep and has_pie and has_sn and not has_mes
        except Exception as e:
            self.logger.debug(f"Error detectando archivo de horas sin Mes: {e}")
            return False

    def _is_anual_consolidado(self, path: Path) -> bool:
        """Detecta si un archivo es un anual consolidado (Periodo + Tipo_de_Contrato)."""
        try:
            ext = path.suffix.lower()
            if ext == '.csv':
                df = pd.read_csv(str(path), nrows=5, encoding='latin-1')
            else:
                df = pd.read_excel(str(path), nrows=5, engine='openpyxl')
            cols_lower = {str(c).lower().strip() for c in df.columns}
            has_periodo = any('periodo' in c for c in cols_lower)
            has_tipo = any('tipo' in c and 'contrato' in c for c in cols_lower)
            return has_periodo and has_tipo
        except Exception as e:
            self.logger.debug(f"Error detectando anual consolidado: {e}")
            return False

    def _load_horas_reales(self, horas_path: Path) -> pd.DataFrame:
        """
        Carga archivo de horas reales por subvención.

        Retorna DataFrame con columnas normalizadas: _rut_norm, _mes, SEP, PIE, SN.
        """
        ext = horas_path.suffix.lower()
        if ext == '.csv':
            df_h = pd.read_csv(str(horas_path), encoding='latin-1')
        else:
            df_h = pd.read_excel(str(horas_path), engine='openpyxl')

        # Mapear columnas case-insensitive
        h_col_map = {}
        for col in df_h.columns:
            cl = str(col).lower().strip()
            if cl == 'mes' and 'mes' not in h_col_map:
                h_col_map['mes'] = col
            if 'rut' in cl and 'rut' not in h_col_map:
                h_col_map['rut'] = col
            if cl == 'sep':
                h_col_map['sep'] = col
            if cl == 'pie':
                h_col_map['pie'] = col
            if cl == 'sn':
                h_col_map['sn'] = col
            if 'nombre' in cl and 'nombre' not in h_col_map:
                h_col_map['nombre'] = col

        df_h['_rut_norm'] = df_h[h_col_map['rut']].apply(normalize_rut)

        # Normalizar mes a número (1-12)
        mes_col = h_col_map['mes']
        df_h['_mes'] = self._normalize_mes_column(df_h[mes_col])

        # Columnas de horas numéricas
        for key in ['sep', 'pie', 'sn']:
            df_h[key.upper()] = pd.to_numeric(df_h[h_col_map[key]], errors='coerce').fillna(0)

        # Nombre si existe
        if 'nombre' in h_col_map:
            df_h['_nombre'] = df_h[h_col_map['nombre']]
        else:
            df_h['_nombre'] = ''

        return df_h[['_rut_norm', '_mes', '_nombre', 'SEP', 'PIE', 'SN']].copy()

    def _normalize_mes_column(self, series: pd.Series) -> pd.Series:
        """Normaliza columna Mes a número (1-12). Acepta nombres, abreviaciones, o números."""
        meses_text = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
            'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
            'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
            'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4,
            'may': 5, 'jun': 6, 'jul': 7, 'ago': 8,
            'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12,
        }

        def parse_mes(val):
            if pd.isna(val):
                return None
            s = str(val).strip().lower()
            if s in meses_text:
                return meses_text[s]
            try:
                n = int(float(s))
                if 1 <= n <= 12:
                    return n
            except (ValueError, TypeError):
                pass
            return None

        return series.apply(parse_mes)

    def _split_anual_file(
        self, path: Path, horas_path: Optional[Path] = None
    ) -> Dict[str, MonthlyFileSet]:
        """
        Divide un archivo anual consolidado en archivos procesados sintéticos por mes.

        Si horas_path se proporciona, usa las horas reales por subvención de ese archivo
        en vez de inferir la distribución desde Tipo_de_Contrato.

        Returns:
            Dict de month_num → MonthlyFileSet con pre_processed=True.
        """
        ext = path.suffix.lower()
        if ext == '.csv':
            df = pd.read_csv(str(path), encoding='latin-1')
        else:
            df = pd.read_excel(str(path), engine='openpyxl')

        # Encontrar columnas clave (case-insensitive, str() por si hay int)
        col_map = {}
        for col in df.columns:
            cl = str(col).lower().strip()
            if 'periodo' in cl and 'periodo' not in col_map:
                col_map['periodo'] = col
            if 'tipo' in cl and 'contrato' in cl:
                col_map['tipo_contrato'] = col
            if cl == 'jornada' or cl == 'horas':
                col_map['jornada'] = col
            if 'rut' in cl and 'rut' not in col_map:
                col_map['rut'] = col
            if 'nombre' in cl and 'nombre' not in col_map:
                col_map['nombre'] = col

        if 'periodo' not in col_map or 'tipo_contrato' not in col_map:
            raise ValueError("Archivo anual no tiene columnas Periodo y Tipo_de_Contrato")
        if 'rut' not in col_map:
            raise ValueError("Archivo anual no tiene columna de RUT")

        rut_col = col_map['rut']
        nombre_col = col_map.get('nombre')

        # Normalizar RUT
        df['_rut_norm'] = df[rut_col].apply(normalize_rut)

        # Extraer mes del periodo
        periodo_col = col_map['periodo']
        df['_mes'] = self._extract_month_from_periodo(df[periodo_col])

        # Cargar horas reales si están disponibles
        horas_df = None
        if horas_path:
            try:
                horas_df = self._load_horas_reales(horas_path)
                self.logger.info(
                    f"Horas reales cargadas: {len(horas_df)} filas, "
                    f"{horas_df['_rut_norm'].nunique()} RUTs"
                )
            except Exception as e:
                self.logger.warning(
                    f"Error cargando horas reales, usando fallback por contrato: {e}"
                )
                horas_df = None

        result: Dict[str, MonthlyFileSet] = {}
        tmp_files_created: List[Path] = []
        all_horas_detail: List[pd.DataFrame] = []

        for mes_num in sorted(df['_mes'].dropna().unique()):
            mes_str = f"{int(mes_num):02d}"
            df_mes = df[df['_mes'] == mes_num].copy()

            if df_mes.empty:
                continue

            if horas_df is not None:
                # Usar horas reales: filtrar por mes y agrupar por RUT
                horas_mes = horas_df[horas_df['_mes'] == mes_num].copy()
                if not horas_mes.empty:
                    pivot_wide = horas_mes.groupby('_rut_norm').agg({
                        'SEP': 'sum', 'PIE': 'sum', 'SN': 'sum', '_nombre': 'first'
                    }).reset_index()
                    pivot_wide['NORMAL'] = pivot_wide['SN']
                    pivot_wide['EIB'] = 0
                    pivot_wide = pivot_wide.rename(columns={
                        '_rut_norm': 'Rut', '_nombre': 'Nombre'
                    })
                else:
                    # Sin datos de horas para este mes, fallback por contrato
                    pivot_wide = self._pivot_by_contract(df_mes, col_map)
            else:
                # Fallback: clasificación por Tipo_de_Contrato
                pivot_wide = self._pivot_by_contract(df_mes, col_map)

            # Asegurar columnas mínimas
            for col_name in ['SEP', 'PIE', 'NORMAL', 'SN', 'EIB']:
                if col_name not in pivot_wide.columns:
                    pivot_wide[col_name] = 0

            # Asegurar SN = NORMAL si falta
            if pivot_wide['SN'].sum() == 0 and pivot_wide['NORMAL'].sum() > 0:
                pivot_wide['SN'] = pivot_wide['NORMAL']

            # Guardar detalle de horas para verificación
            month_name = MESES_NUM_TO_NAME.get(mes_str, mes_str)
            has_nombre = 'Nombre' in pivot_wide.columns
            detail = pivot_wide[['Rut'] + (['Nombre'] if has_nombre else []) + ['SEP', 'PIE', 'NORMAL', 'EIB']].copy()
            detail['TOTAL'] = detail['SEP'] + detail['PIE'] + detail['NORMAL'] + detail['EIB']
            detail['MES'] = month_name
            detail['MES_NUM'] = mes_str
            all_horas_detail.append(detail)

            # Crear archivo SEP sintético: Rut, Nombre, SEP
            df_sep = pivot_wide[['Rut'] + (['Nombre'] if has_nombre else []) + ['SEP']].copy()

            # Crear archivo PIE sintético: Rut, Nombre, PIE, SN
            df_pie = pivot_wide[['Rut'] + (['Nombre'] if has_nombre else []) + ['PIE', 'SN']].copy()

            sep_path = self._make_temp()
            pie_path = self._make_temp()
            tmp_files_created.extend([sep_path, pie_path])

            df_sep.to_excel(str(sep_path), index=False, engine='openpyxl')
            df_pie.to_excel(str(pie_path), index=False, engine='openpyxl')

            result[mes_str] = MonthlyFileSet(
                month=mes_str,
                month_name=month_name,
                sep=('anual_sep_' + mes_str + '.xlsx', sep_path),
                pie=('anual_pie_' + mes_str + '.xlsx', pie_path),
                pre_processed=True,
            )

        # Guardar referencia para limpieza y detalle de horas
        self._anual_tmp_files = tmp_files_created
        self._anual_horas_detail = all_horas_detail
        return result

    def _pivot_by_contract(
        self, df_mes: pd.DataFrame, col_map: Dict
    ) -> pd.DataFrame:
        """
        Fallback: agrupa horas por RUT usando Tipo_de_Contrato para clasificar.

        Returns:
            DataFrame con columnas Rut, Nombre, SEP, PIE, NORMAL, SN, EIB.
        """
        jornada_col = col_map.get('jornada')
        nombre_col = col_map.get('nombre')

        if jornada_col:
            df_mes['_jornada'] = pd.to_numeric(
                df_mes[jornada_col], errors='coerce'
            ).fillna(0)
        else:
            df_mes['_jornada'] = 1

        df_mes['_tipo'] = df_mes[col_map['tipo_contrato']].apply(classify_contract)

        pivot = df_mes.groupby(['_rut_norm', '_tipo'])['_jornada'].sum().reset_index()
        pivot_wide = pivot.pivot_table(
            index='_rut_norm', columns='_tipo', values='_jornada', fill_value=0
        ).reset_index()

        for col_name in ['SEP', 'PIE', 'NORMAL', 'EIB']:
            if col_name not in pivot_wide.columns:
                pivot_wide[col_name] = 0

        # Obtener nombre
        nombres = df_mes.groupby('_rut_norm').first().reset_index()
        nombre_series = nombres[['_rut_norm']]
        if nombre_col:
            nombre_series = nombres[['_rut_norm', nombre_col]].rename(
                columns={nombre_col: 'Nombre'}
            )

        pivot_wide = pivot_wide.merge(nombre_series, on='_rut_norm', how='left')
        pivot_wide = pivot_wide.rename(columns={'_rut_norm': 'Rut'})
        pivot_wide['SN'] = pivot_wide['NORMAL']

        return pivot_wide

    def _extract_month_from_periodo(self, series: pd.Series) -> pd.Series:
        """Extrae número de mes (1-12) de una columna Periodo."""
        result = pd.Series(index=series.index, dtype='float64')

        for idx, val in series.items():
            if pd.isna(val):
                result.at[idx] = None
                continue
            # Si es datetime
            if hasattr(val, 'month'):
                result.at[idx] = val.month
                continue
            # Si es string, intentar parsear
            s = str(val).strip()
            # Formato YYYY-MM-DD o similar
            try:
                dt = pd.to_datetime(s, dayfirst=True)
                result.at[idx] = dt.month
                continue
            except (ValueError, TypeError):
                pass
            # Formato numérico (1-12)
            try:
                n = int(float(s))
                if 1 <= n <= 12:
                    result.at[idx] = n
                    continue
            except (ValueError, TypeError):
                pass
            result.at[idx] = None

        return result

    def validate_monthly_sets(
        self, monthly: Dict[str, MonthlyFileSet]
    ) -> List[str]:
        """
        Valida que cada mes tenga los archivos requeridos (SEP, PIE, WEB).

        Returns:
            Lista de errores. Vacía si todo está OK.
        """
        errors = []
        for month_num in sorted(monthly.keys()):
            ms = monthly[month_num]
            missing = []
            if not ms.sep:
                missing.append('SEP')
            if not ms.pie:
                missing.append('PIE')
            if not ms.web:
                missing.append('WEB')
            if missing:
                errors.append(
                    f"{ms.month_name} ({month_num}): faltan {', '.join(missing)}"
                )
        return errors

    def process_all(
        self,
        monthly_sets: Dict[str, MonthlyFileSet],
        output_path: Path,
        progress_callback: Callable[[int, str], None],
    ) -> Dict:
        """
        Procesa todos los meses y genera Excel consolidado.

        Returns:
            Dict con estadísticas del procesamiento.
        """
        if not monthly_sets:
            raise ValueError("No hay meses para procesar")

        all_brp = []
        all_eib = []
        all_revisar = []
        all_sep = []
        all_pie = []
        month_summaries = []
        total_months = len(monthly_sets)
        processed = 0

        def noop_progress(val, msg):
            pass

        for month_num in sorted(monthly_sets.keys()):
            ms = monthly_sets[month_num]
            pct_base = int((processed / total_months) * 90)
            progress_callback(
                pct_base, f"Procesando {ms.month_name}..."
            )

            tmp_files = []
            try:
                if ms.pre_processed:
                    # Archivos ya son procesados sintéticos (desde archivo anual)
                    sep_out = ms.sep[1]
                    pie_out = ms.pie[1]
                else:
                    # 1. Procesar SEP
                    sep_out = self._make_temp()
                    tmp_files.append(sep_out)
                    sep_proc = SEPProcessor()
                    sep_proc.process_file(ms.sep[1], sep_out, noop_progress)

                    # 2. Procesar PIE
                    pie_out = self._make_temp()
                    tmp_files.append(pie_out)
                    pie_proc = PIEProcessor()
                    pie_proc.process_file(ms.pie[1], pie_out, noop_progress)

                    # Capturar sábanas SEP/PIE detalladas
                    df_sep_detail = pd.read_excel(sep_out, engine='openpyxl')
                    df_sep_detail['MES'] = ms.month_name
                    df_sep_detail['MES_NUM'] = month_num
                    all_sep.append(df_sep_detail)

                    df_pie_detail = pd.read_excel(pie_out, engine='openpyxl')
                    df_pie_detail['MES'] = ms.month_name
                    df_pie_detail['MES_NUM'] = month_num
                    all_pie.append(df_pie_detail)

                # 3. Procesar EIB (opcional)
                eib_df = None
                if ms.eib:
                    eib_out = self._make_temp()
                    tmp_files.append(eib_out)
                    eib_proc = EIBProcessor()
                    eib_proc.process_file(ms.eib[1], eib_out, noop_progress)
                    eib_df = pd.read_excel(eib_out, engine='openpyxl')
                    eib_df['MES'] = ms.month_name
                    eib_df['MES_NUM'] = month_num
                    all_eib.append(eib_df)

                # 4. Procesar BRP
                brp_out = self._make_temp()
                tmp_files.append(brp_out)
                brp_proc = BRPProcessor()
                brp_proc.process_file(
                    web_sostenedor_path=ms.web[1],
                    sep_procesado_path=sep_out,
                    pie_procesado_path=pie_out,
                    output_path=brp_out,
                    progress_callback=noop_progress,
                    month_filter=month_num,
                )

                # 5. Leer BRP_DISTRIBUIDO
                brp_df = pd.read_excel(
                    brp_out, sheet_name='BRP_DISTRIBUIDO', engine='openpyxl'
                )
                brp_df['MES'] = ms.month_name
                brp_df['MES_NUM'] = month_num
                all_brp.append(brp_df)

                # 6. Leer REVISAR si existe
                try:
                    df_rev = pd.read_excel(
                        brp_out, sheet_name='REVISAR', engine='openpyxl'
                    )
                    if not df_rev.empty:
                        df_rev['MES'] = ms.month_name
                        df_rev['MES_NUM'] = month_num
                        all_revisar.append(df_rev)
                except Exception:
                    pass

                # 7. Resumen del mes (con DAEM/CPEIP)
                def _col_sum(col):
                    return brp_df[col].sum() if col in brp_df.columns else 0

                rut_col_name = 'RUT (Docente)' if 'RUT (Docente)' in brp_df.columns else 'RUT_NORM'
                rbd_col_name = next((c for c in brp_df.columns if 'rbd' in c.lower()), None)

                summary = {
                    'MES': ms.month_name,
                    'MES_NUM': month_num,
                    'BRP_SEP': _col_sum('BRP_SEP'),
                    'BRP_PIE': _col_sum('BRP_PIE'),
                    'BRP_NORMAL': _col_sum('BRP_NORMAL'),
                    'BRP_TOTAL': _col_sum('BRP_TOTAL'),
                    'DAEM_SEP': _col_sum('TOTAL_DAEM_SEP'),
                    'DAEM_PIE': _col_sum('TOTAL_DAEM_PIE'),
                    'DAEM_NORMAL': _col_sum('TOTAL_DAEM_NORMAL'),
                    'CPEIP_SEP': _col_sum('TOTAL_CPEIP_SEP'),
                    'CPEIP_PIE': _col_sum('TOTAL_CPEIP_PIE'),
                    'CPEIP_NORMAL': _col_sum('TOTAL_CPEIP_NORMAL'),
                    'RECON_SEP': _col_sum('BRP_RECONOCIMIENTO_SEP'),
                    'RECON_PIE': _col_sum('BRP_RECONOCIMIENTO_PIE'),
                    'RECON_NORMAL': _col_sum('BRP_RECONOCIMIENTO_NORMAL'),
                    'TRAMO_SEP': _col_sum('BRP_TRAMO_SEP'),
                    'TRAMO_PIE': _col_sum('BRP_TRAMO_PIE'),
                    'TRAMO_NORMAL': _col_sum('BRP_TRAMO_NORMAL'),
                    'PRIOR_SEP': _col_sum('CPEIP_PRIOR_SEP'),
                    'PRIOR_PIE': _col_sum('CPEIP_PRIOR_PIE'),
                    'PRIOR_NORMAL': _col_sum('CPEIP_PRIOR_NORMAL'),
                    'DOCENTES_BRP': brp_df[rut_col_name].nunique() if rut_col_name in brp_df.columns else len(brp_df),
                    'ESTABLECIMIENTOS': brp_df[rbd_col_name].nunique() if rbd_col_name and rbd_col_name in brp_df.columns else 0,
                    'COSTO_EIB': int(eib_df['TOTAL HABERES_EIB'].sum()) if eib_df is not None and 'TOTAL HABERES_EIB' in eib_df.columns else 0,
                    'DOCENTES_EIB': len(eib_df) if eib_df is not None else 0,
                    'CON_EIB': ms.eib is not None,
                }
                summary['DAEM_TOTAL'] = summary['DAEM_SEP'] + summary['DAEM_PIE'] + summary['DAEM_NORMAL']
                summary['CPEIP_TOTAL'] = summary['CPEIP_SEP'] + summary['CPEIP_PIE'] + summary['CPEIP_NORMAL']
                month_summaries.append(summary)

            except Exception as e:
                self.logger.error(
                    f"Error procesando {ms.month_name}: {e}", exc_info=True
                )
                month_summaries.append({
                    'MES': ms.month_name,
                    'MES_NUM': month_num,
                    'BRP_SEP': 0, 'BRP_PIE': 0, 'BRP_NORMAL': 0,
                    'BRP_TOTAL': 0, 'DOCENTES_BRP': 0,
                    'COSTO_EIB': 0, 'DOCENTES_EIB': 0,
                    'CON_EIB': False,
                    'ERROR': str(e),
                })
            finally:
                for tf in tmp_files:
                    self._cleanup(tf)

            processed += 1

        progress_callback(92, "Generando resumen anual...")

        # Generar Excel consolidado
        anual_horas = getattr(self, '_anual_horas_detail', [])
        self._write_output(
            output_path, all_brp, all_eib, month_summaries, anual_horas,
            all_revisar, all_sep, all_pie,
        )

        # Limpiar archivos temporales del anual consolidado
        for tf in getattr(self, '_anual_tmp_files', []):
            self._cleanup(tf)

        progress_callback(100, "Lote anual completado!")

        # Estadísticas de retorno
        df_summary = pd.DataFrame(month_summaries)
        return {
            'meses_procesados': len([s for s in month_summaries if 'ERROR' not in s]),
            'meses_error': len([s for s in month_summaries if 'ERROR' in s]),
            'brp_total_anual': int(df_summary['BRP_TOTAL'].sum()),
            'eib_total_anual': int(df_summary['COSTO_EIB'].sum()),
            'summaries': month_summaries,
            'tiene_detalle_sep_pie': len(all_sep) > 0,
        }

    def _write_output(
        self,
        output_path: Path,
        all_brp: List[pd.DataFrame],
        all_eib: List[pd.DataFrame],
        month_summaries: List[Dict],
        anual_horas: Optional[List[pd.DataFrame]] = None,
        all_revisar: Optional[List[pd.DataFrame]] = None,
        all_sep: Optional[List[pd.DataFrame]] = None,
        all_pie: Optional[List[pd.DataFrame]] = None,
    ) -> None:
        """Escribe Excel multi-hoja con resultados anuales."""
        df_summary = pd.DataFrame(month_summaries)

        with pd.ExcelWriter(str(output_path), engine='openpyxl') as writer:
            # Hoja 1: RESUMEN_ANUAL
            resumen_rows = []
            brp_sep = int(df_summary['BRP_SEP'].sum())
            brp_pie = int(df_summary['BRP_PIE'].sum())
            brp_normal = int(df_summary['BRP_NORMAL'].sum())
            brp_total = brp_sep + brp_pie + brp_normal
            eib_total = int(df_summary['COSTO_EIB'].sum())

            daem_total = int(df_summary['DAEM_TOTAL'].sum()) if 'DAEM_TOTAL' in df_summary.columns else 0
            cpeip_total = int(df_summary['CPEIP_TOTAL'].sum()) if 'CPEIP_TOTAL' in df_summary.columns else 0

            resumen_rows.append({'CONCEPTO': 'BRP SEP', 'MONTO': brp_sep})
            resumen_rows.append({'CONCEPTO': 'BRP PIE', 'MONTO': brp_pie})
            resumen_rows.append({'CONCEPTO': 'BRP NORMAL', 'MONTO': brp_normal})
            resumen_rows.append({'CONCEPTO': 'BRP TOTAL', 'MONTO': brp_total})
            resumen_rows.append({'CONCEPTO': '', 'MONTO': None})
            resumen_rows.append({'CONCEPTO': 'DAEM (Subvención)', 'MONTO': daem_total})
            resumen_rows.append({'CONCEPTO': 'CPEIP (Transferencia)', 'MONTO': cpeip_total})
            resumen_rows.append({'CONCEPTO': '', 'MONTO': None})
            resumen_rows.append({'CONCEPTO': 'COSTO EIB TOTAL', 'MONTO': eib_total})
            resumen_rows.append({'CONCEPTO': 'GRAN TOTAL', 'MONTO': brp_total + eib_total})
            pd.DataFrame(resumen_rows).to_excel(
                writer, sheet_name='RESUMEN_ANUAL', index=False
            )

            # Hoja 2: POR_MES
            cols_mes = [
                'MES', 'DOCENTES_BRP', 'ESTABLECIMIENTOS',
                'BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL',
                'DAEM_TOTAL', 'CPEIP_TOTAL',
                'COSTO_EIB', 'DOCENTES_EIB',
            ]
            cols_exist = [c for c in cols_mes if c in df_summary.columns]
            df_por_mes = df_summary[cols_exist].copy()
            # Agregar fila de totales
            totals = {c: df_por_mes[c].sum() for c in cols_exist if c != 'MES'}
            totals['MES'] = 'TOTAL'
            df_por_mes = pd.concat(
                [df_por_mes, pd.DataFrame([totals])], ignore_index=True
            )
            df_por_mes.to_excel(writer, sheet_name='POR_MES', index=False)

            # Hoja 3: POR_RBD (totales anuales por establecimiento)
            if all_brp:
                df_all_brp = pd.concat(all_brp, ignore_index=True)
                rbd_col = None
                for c in df_all_brp.columns:
                    if 'rbd' in c.lower():
                        rbd_col = c
                        break
                if rbd_col:
                    brp_agg_cols = {}
                    for c in ['BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL']:
                        if c in df_all_brp.columns:
                            brp_agg_cols[c] = 'sum'
                    if brp_agg_cols:
                        df_rbd = df_all_brp.groupby(rbd_col).agg(brp_agg_cols).reset_index()
                        df_rbd = df_rbd.rename(columns={rbd_col: 'RBD'})
                        df_rbd.to_excel(writer, sheet_name='POR_RBD', index=False)

                # Hoja 4: DETALLE_BRP
                df_all_brp.to_excel(
                    writer, sheet_name='DETALLE_BRP', index=False
                )

            # Hoja 5: DETALLE_EIB
            if all_eib:
                df_all_eib = pd.concat(all_eib, ignore_index=True)
                df_all_eib.to_excel(
                    writer, sheet_name='DETALLE_EIB', index=False
                )

            # Hoja 6: REVISAR (docentes a revisar consolidado)
            if all_revisar:
                df_all_rev = pd.concat(all_revisar, ignore_index=True)
                df_all_rev.to_excel(
                    writer, sheet_name='REVISAR', index=False
                )

            # Hoja 7: DETALLE_SEP (sábana SEP con columnas _SEP)
            if all_sep:
                df_all_sep = pd.concat(all_sep, ignore_index=True)
                df_all_sep.to_excel(
                    writer, sheet_name='DETALLE_SEP', index=False
                )

            # Hoja 8: DETALLE_PIE (sábana PIE+Normal con columnas PIE/SN/_nuevo)
            if all_pie:
                df_all_pie = pd.concat(all_pie, ignore_index=True)
                df_all_pie.to_excel(
                    writer, sheet_name='DETALLE_PIE', index=False
                )

            # Hojas de verificación: división de horas por subvención
            if anual_horas:
                df_horas = pd.concat(anual_horas, ignore_index=True)
                base_cols = ['MES', 'MES_NUM', 'Rut']
                if 'Nombre' in df_horas.columns:
                    base_cols.insert(3, 'Nombre')
                df_horas = df_horas.sort_values(['MES_NUM', 'Rut'])

                # HORAS_SEP: solo docentes con horas SEP > 0
                df_h_sep = df_horas[df_horas['SEP'] > 0][base_cols + ['SEP']].copy()
                if not df_h_sep.empty:
                    df_h_sep.to_excel(writer, sheet_name='HORAS_SEP', index=False)

                # HORAS_PIE: solo docentes con horas PIE > 0
                df_h_pie = df_horas[df_horas['PIE'] > 0][base_cols + ['PIE']].copy()
                if not df_h_pie.empty:
                    df_h_pie.to_excel(writer, sheet_name='HORAS_PIE', index=False)

                # HORAS_NORMAL: solo docentes con horas NORMAL > 0
                df_h_normal = df_horas[df_horas['NORMAL'] > 0][base_cols + ['NORMAL']].copy()
                if not df_h_normal.empty:
                    df_h_normal.to_excel(writer, sheet_name='HORAS_NORMAL', index=False)

                # HORAS_EIB: solo docentes con horas EIB > 0
                df_h_eib = df_horas[df_horas['EIB'] > 0][base_cols + ['EIB']].copy()
                if not df_h_eib.empty:
                    df_h_eib.to_excel(writer, sheet_name='HORAS_EIB', index=False)

                # HORAS_COMPLETO: vista completa con todas las columnas
                df_horas[base_cols + ['SEP', 'PIE', 'NORMAL', 'EIB', 'TOTAL']].to_excel(
                    writer, sheet_name='HORAS_COMPLETO', index=False
                )

    def _make_temp(self) -> Path:
        """Crea un archivo temporal."""
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        tmp.close()
        return Path(tmp.name)

    def _cleanup(self, path: Path) -> None:
        """Elimina archivo temporal."""
        try:
            if path and path.exists():
                os.unlink(path)
        except OSError:
            pass
