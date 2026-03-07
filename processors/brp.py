"""
Procesador BRP (Bonificación de Reconocimiento Profesional).

Distribuye el BRP según las horas en cada tipo de subvención (SEP, PIE, GENERAL).

Columnas que se dividen:
- Total reconocimiento profesional = Subvención reconocimiento + Transferencia reconocimiento
- Total tramo = Subvención tramo + Transferencia tramo
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from processors.base import BaseProcessor, ProgressCallback, ProcessorError
from config.columns import (
    WEB_SOSTENEDOR_COLUMNS, WEB_CRITICAL_COLUMNS,
    WEB_INFO_COLUMNS, WEB_FRIENDLY_NAMES, normalize_rut
)
from config.escuelas import get_rbd_map, parse_school_name


class BRPProcessor(BaseProcessor):
    """Procesador para distribuir BRP entre tipos de subvención."""
    
    MAX_HORAS = 44
    
    def __init__(self):
        super().__init__()
        self.cols = WEB_SOSTENEDOR_COLUMNS
        self.cols_actual = {}
        self.docentes_revisar = []
        self.column_alerts = []
    
    def process_file(
        self,
        web_sostenedor_path: Path,
        sep_procesado_path: Path,
        pie_procesado_path: Path,
        output_path: Path,
        progress_callback: ProgressCallback,
        month_filter: Optional[str] = None,
    ) -> None:
        """Procesa y distribuye BRP.

        Args:
            month_filter: Mes a filtrar en web sostenedor ('01'-'12'). None = sin filtro.
        """
        try:
            progress_callback(0, "Iniciando distribución BRP...")

            # 1. Cargar archivos
            progress_callback(5, "Cargando archivo MINEDUC...")
            df_web = self._load_web_sostenedor(web_sostenedor_path, month_filter=month_filter)
            
            progress_callback(15, "Cargando archivo SEP procesado...")
            df_sep = self._load_processed_file(sep_procesado_path, 'SEP')
            
            progress_callback(25, "Cargando archivo PIE procesado...")
            df_pie = self._load_processed_file(pie_procesado_path, 'PIE')
            
            # 2. Construir mapa de horas
            progress_callback(35, "Analizando horas por tipo de subvención...")
            horas_por_docente = self._build_hours_map(df_sep, df_pie)
            self._horas_map = horas_por_docente  # Guardar para acceso posterior
            
            # 3. Identificar casos para revisión
            progress_callback(40, "Identificando casos para revisión...")
            ruts_web = set(df_web['RUT_NORM'].unique())
            ruts_procesados = set(horas_por_docente.keys())
            self.docentes_revisar = self._build_revision_list(
                horas_por_docente, ruts_web, ruts_procesados, df_web, df_sep, df_pie
            )
            
            # 4. Identificar multi-establecimiento
            progress_callback(50, "Identificando docentes en múltiples establecimientos...")
            df_web = self._identify_multi_establishment(df_web)
            
            # 5. Distribuir BRP por establecimiento
            progress_callback(60, "Distribuyendo BRP por establecimiento...")
            df_web = self._distribute_by_establishment(df_web)
            
            # 6. Clasificar por tipo de subvención
            progress_callback(75, "Clasificando por SEP/PIE/NORMAL...")
            df_result = self._classify_by_subvencion(df_web, horas_por_docente)
            
            # 7. Estadísticas
            progress_callback(85, "Generando resumen...")
            self._log_statistics(df_result)
            
            # 8. Guardar resultado en UN archivo con dos hojas
            progress_callback(90, "Guardando resultados...")
            self._save_combined_file(df_result, output_path)
            
            progress_callback(100, "¡Distribución BRP completada!")
            
        except Exception as e:
            self.logger.error(f"Error en proceso BRP: {str(e)}", exc_info=True)
            raise
    
    def _save_combined_file(self, df_result: pd.DataFrame, output_path: Path) -> None:
        """Guarda resultado y revisión en UN solo archivo con múltiples hojas."""
        with pd.ExcelWriter(str(output_path), engine='openpyxl') as writer:
            
            # Hoja 1: BRP Distribuido (con nombres)
            df_export = self._prepare_export_dataframe(df_result)
            df_export.to_excel(writer, sheet_name='BRP_DISTRIBUIDO', index=False)
            
            # Hoja 2: Resumen por Establecimiento
            df_resumen = self._create_summary_by_rbd(df_result)
            df_resumen.to_excel(writer, sheet_name='RESUMEN_POR_RBD', index=False)
            
            # Hoja 3: Casos a revisar (si hay)
            if self.docentes_revisar:
                df_revision = pd.DataFrame(self.docentes_revisar)
                
                # Ordenar
                df_revision['_orden'] = df_revision['MOTIVO'].map({
                    'EXCEDE 44 HORAS': 0, 
                    'SIN LIQUIDACIÓN': 1
                })
                df_revision = df_revision.sort_values(['_orden', 'HORAS_TOTAL'], ascending=[True, False])
                df_revision = df_revision.drop('_orden', axis=1)
                
                # Reordenar columnas
                cols_order = ['RUT', 'NOMBRE', 'APELLIDOS', 'TIPO_PAGO', 'MOTIVO', 
                              'HORAS_SEP', 'HORAS_PIE', 'HORAS_SN', 'HORAS_TOTAL', 
                              'EXCESO', 'DETALLE', 'ACCION']
                cols_exist = [c for c in cols_order if c in df_revision.columns]
                df_revision = df_revision[cols_exist + [c for c in df_revision.columns if c not in cols_exist]]
                
                df_revision.to_excel(writer, sheet_name='REVISAR', index=False)
                self.logger.info(f"📋 Hoja REVISAR: {len(df_revision)} casos")
            
            # Hoja 4: Resumen General
            df_general = self._create_general_summary(df_result)
            df_general.to_excel(writer, sheet_name='RESUMEN_GENERAL', index=False)

            # Hoja 5: Multi-Establecimiento (docentes en 2+ escuelas)
            df_multi = self._create_multi_establishment_sheet(df_result)
            if df_multi is not None and not df_multi.empty:
                df_multi.to_excel(writer, sheet_name='MULTI_ESTABLECIMIENTO', index=False)
                self.logger.info(f"📋 Hoja MULTI_ESTABLECIMIENTO: {df_multi['RUT'].nunique()} docentes")

            # Hojas por establecimiento (nombre parseado)
            self._add_per_school_sheets(df_export, writer)

        self.logger.info(f"✅ Archivo guardado: {output_path.name}")

    def _add_per_school_sheets(self, df_export: pd.DataFrame, writer) -> None:
        """Agrega una hoja por cada establecimiento con nombre parseado."""
        col_rbd = self.cols_actual.get('rbd')
        if not col_rbd or col_rbd not in df_export.columns:
            return

        rbd_map = get_rbd_map()
        used_names = set()

        for rbd_val, df_school in df_export.groupby(col_rbd):
            rbd_str = str(int(rbd_val)) if isinstance(rbd_val, float) else str(rbd_val)
            full_name = rbd_map.get(rbd_str, f'RBD_{rbd_str}')
            short_name = parse_school_name(full_name) if full_name != f'RBD_{rbd_str}' else full_name

            # Excel sheet names: max 31 chars, unique
            sheet_name = short_name[:31]
            if sheet_name in used_names:
                sheet_name = f"{short_name[:27]}_{rbd_str}"[:31]
            used_names.add(sheet_name)

            try:
                df_school.to_excel(writer, sheet_name=sheet_name, index=False)
            except Exception as e:
                self.logger.warning(f"No se pudo crear hoja '{sheet_name}': {e}")
    
    def _prepare_export_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepara DataFrame para exportar con nombres y columnas ordenadas."""
        # Columnas a incluir en orden
        col_rbd = self.cols_actual.get('rbd')
        col_rut = self.cols_actual.get('rut')
        col_nombres = self.cols_actual.get('nombres')
        col_ap1 = self.cols_actual.get('apellido1')
        col_ap2 = self.cols_actual.get('apellido2')
        col_horas = self.cols_actual.get('horas_contrato')
        col_tipo_pago = self.cols_actual.get('tipo_pago')
        col_tramo = self.cols_actual.get('tramo')
        
        # Crear columna NOMBRE_COMPLETO
        if col_nombres and col_ap1:
            df['NOMBRE_COMPLETO'] = df.apply(
                lambda r: f"{r.get(col_ap1, '')} {r.get(col_ap2, '')} {r.get(col_nombres, '')}".strip(), 
                axis=1
            )
        
        # Columnas prioritarias al inicio
        cols_inicio = []
        if col_rbd and col_rbd in df.columns:
            cols_inicio.append(col_rbd)
        if col_rut and col_rut in df.columns:
            cols_inicio.append(col_rut)
        if 'NOMBRE_COMPLETO' in df.columns:
            cols_inicio.append('NOMBRE_COMPLETO')
        if col_tipo_pago and col_tipo_pago in df.columns:
            cols_inicio.append(col_tipo_pago)
        if col_tramo and col_tramo in df.columns:
            cols_inicio.append(col_tramo)
        if col_horas and col_horas in df.columns:
            cols_inicio.append(col_horas)

        # Horas por subvención
        for hcol in ['HORAS_SEP', 'HORAS_PIE', 'HORAS_SN']:
            if hcol in df.columns:
                cols_inicio.append(hcol)

        # Columnas MINEDUC originales (visibles al usuario)
        cols_mineduc = [
            'RECONOCIMIENTO_DIST', 'TRAMO_DIST', 'ASIG_PRIOR_DIST',
            'SUBV_RECON_DIST', 'TRANSF_RECON_DIST',
            'SUBV_TRAMO_DIST', 'TRANSF_TRAMO_DIST',
        ]
        cols_mineduc = [c for c in cols_mineduc if c in df.columns]

        # Renombrar columnas MINEDUC para claridad en el export
        rename_map = {
            'RECONOCIMIENTO_DIST': 'MINEDUC_RECONOCIMIENTO',
            'TRAMO_DIST': 'MINEDUC_TRAMO',
            'ASIG_PRIOR_DIST': 'MINEDUC_PRIORITARIOS',
            'SUBV_RECON_DIST': 'MINEDUC_SUBV_RECONOCIMIENTO',
            'TRANSF_RECON_DIST': 'MINEDUC_TRANSF_RECONOCIMIENTO',
            'SUBV_TRAMO_DIST': 'MINEDUC_SUBV_TRAMO',
            'TRANSF_TRAMO_DIST': 'MINEDUC_TRANSF_TRAMO',
        }
        for old_name in cols_mineduc:
            new_name = rename_map.get(old_name, old_name)
            if old_name in df.columns:
                df[new_name] = df[old_name]
        cols_mineduc_renamed = [rename_map.get(c, c) for c in cols_mineduc]

        # Columnas multi-establecimiento
        cols_multi = []
        if 'ES_MULTI' in df.columns:
            df['MULTI_ESTABLECIMIENTO'] = df['ES_MULTI'].map({True: 'SI', False: 'NO'})
            cols_multi = ['MULTI_ESTABLECIMIENTO', 'NUM_ESTABLECIMIENTOS']
            cols_multi = [c for c in cols_multi if c in df.columns]

        # Columnas BRP
        cols_brp = [
            'BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL',
            'TOTAL_DAEM_SEP', 'TOTAL_DAEM_PIE', 'TOTAL_DAEM_NORMAL',
            'TOTAL_CPEIP_SEP', 'TOTAL_CPEIP_PIE', 'TOTAL_CPEIP_NORMAL',
            'BRP_RECONOCIMIENTO_SEP', 'BRP_RECONOCIMIENTO_PIE', 'BRP_RECONOCIMIENTO_NORMAL',
            'BRP_TRAMO_SEP', 'BRP_TRAMO_PIE', 'BRP_TRAMO_NORMAL',
            'DAEM_RECON_SEP', 'DAEM_RECON_PIE', 'DAEM_RECON_NORMAL',
            'CPEIP_RECON_SEP', 'CPEIP_RECON_PIE', 'CPEIP_RECON_NORMAL',
            'DAEM_TRAMO_SEP', 'DAEM_TRAMO_PIE', 'DAEM_TRAMO_NORMAL',
            'CPEIP_TRAMO_SEP', 'CPEIP_TRAMO_PIE', 'CPEIP_TRAMO_NORMAL',
            'CPEIP_PRIOR_SEP', 'CPEIP_PRIOR_PIE', 'CPEIP_PRIOR_NORMAL',
        ]
        cols_brp = [c for c in cols_brp if c in df.columns]

        # Columnas finales (excluyendo las ya agregadas y las internas)
        cols_excluir = set(cols_inicio + cols_mineduc_renamed + cols_multi + cols_brp + [
            'RUT_NORM', 'ES_MULTI', 'TOTAL_HORAS_MINEDUC',
            'HORAS_SEP', 'HORAS_PIE', 'HORAS_SN',
            'RECONOCIMIENTO_DIST', 'TRAMO_DIST',
            'SUBV_RECON_DIST', 'TRANSF_RECON_DIST',
            'SUBV_TRAMO_DIST', 'TRANSF_TRAMO_DIST',
            'ASIG_PRIOR_DIST', 'MULTI_ESTABLECIMIENTO',
            'NUM_ESTABLECIMIENTOS',
        ])
        cols_resto = [c for c in df.columns if c not in cols_excluir]

        # Ordenar: ID + MINEDUC originales + Multi + BRP distribuido + resto
        cols_final = cols_inicio + cols_mineduc_renamed + cols_multi + cols_brp + cols_resto
        cols_final = [c for c in cols_final if c in df.columns]

        return df[cols_final]
    
    def _create_summary_by_rbd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Crea resumen de BRP por establecimiento con desglose DAEM/CPEIP."""
        col_rbd = self.cols_actual.get('rbd')

        if not col_rbd or col_rbd not in df.columns:
            return pd.DataFrame({'Mensaje': ['No se encontró columna RBD']})

        # Agrupar por RBD
        agg_cols = {
            'RUT_NORM': 'nunique',
            'BRP_SEP': 'sum', 'BRP_PIE': 'sum', 'BRP_NORMAL': 'sum', 'BRP_TOTAL': 'sum',
            'TOTAL_DAEM_SEP': 'sum', 'TOTAL_DAEM_PIE': 'sum', 'TOTAL_DAEM_NORMAL': 'sum',
            'TOTAL_CPEIP_SEP': 'sum', 'TOTAL_CPEIP_PIE': 'sum', 'TOTAL_CPEIP_NORMAL': 'sum',
        }
        resumen = df.groupby(col_rbd).agg(agg_cols).reset_index()

        resumen.columns = ['RBD', 'DOCENTES',
                           'BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL',
                           'DAEM_SEP', 'DAEM_PIE', 'DAEM_NORMAL',
                           'CPEIP_SEP', 'CPEIP_PIE', 'CPEIP_NORMAL']

        # Agregar fila de totales
        totales = {col: resumen[col].sum() for col in resumen.columns if col != 'RBD'}
        totales['RBD'] = 'TOTAL'
        resumen = pd.concat([resumen, pd.DataFrame([totales])], ignore_index=True)

        # Calcular porcentajes
        total_brp = resumen.loc[resumen['RBD'] == 'TOTAL', 'BRP_TOTAL'].values[0]
        if total_brp > 0:
            resumen['%_SEP'] = (resumen['BRP_SEP'] / total_brp * 100).round(1)
            resumen['%_PIE'] = (resumen['BRP_PIE'] / total_brp * 100).round(1)
            resumen['%_NORMAL'] = (resumen['BRP_NORMAL'] / total_brp * 100).round(1)

        return resumen
    
    def _create_general_summary(self, df: pd.DataFrame) -> pd.DataFrame:
        """Crea resumen general para dashboard con desglose DAEM/CPEIP."""
        total_docentes = df['RUT_NORM'].nunique()
        col_rbd = self.cols_actual.get('rbd')
        total_rbds = df[col_rbd].nunique() if col_rbd and col_rbd in df.columns else 0

        brp_sep = df['BRP_SEP'].sum()
        brp_pie = df['BRP_PIE'].sum()
        brp_normal = df['BRP_NORMAL'].sum()
        brp_total = brp_sep + brp_pie + brp_normal

        recon_sep = df['BRP_RECONOCIMIENTO_SEP'].sum()
        recon_pie = df['BRP_RECONOCIMIENTO_PIE'].sum()
        recon_normal = df['BRP_RECONOCIMIENTO_NORMAL'].sum()

        tramo_sep = df['BRP_TRAMO_SEP'].sum()
        tramo_pie = df['BRP_TRAMO_PIE'].sum()
        tramo_normal = df['BRP_TRAMO_NORMAL'].sum()

        daem_sep = df['TOTAL_DAEM_SEP'].sum()
        daem_pie = df['TOTAL_DAEM_PIE'].sum()
        daem_normal = df['TOTAL_DAEM_NORMAL'].sum()
        daem_total = daem_sep + daem_pie + daem_normal

        cpeip_sep = df['TOTAL_CPEIP_SEP'].sum()
        cpeip_pie = df['TOTAL_CPEIP_PIE'].sum()
        cpeip_normal = df['TOTAL_CPEIP_NORMAL'].sum()
        cpeip_total = cpeip_sep + cpeip_pie + cpeip_normal

        prior_sep = df['CPEIP_PRIOR_SEP'].sum()
        prior_pie = df['CPEIP_PRIOR_PIE'].sum()
        prior_normal = df['CPEIP_PRIOR_NORMAL'].sum()

        pct_sep = round(100 * brp_sep / brp_total, 1) if brp_total > 0 else 0
        pct_pie = round(100 * brp_pie / brp_total, 1) if brp_total > 0 else 0
        pct_normal = round(100 * brp_normal / brp_total, 1) if brp_total > 0 else 0

        resumen = [
            {'CONCEPTO': 'ESTADÍSTICAS GENERALES', 'VALOR': None},
            {'CONCEPTO': 'Total Docentes', 'VALOR': total_docentes},
            {'CONCEPTO': 'Total Establecimientos', 'VALOR': total_rbds},
            {'CONCEPTO': 'Casos a Revisar', 'VALOR': len(self.docentes_revisar)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'DISTRIBUCIÓN BRP TOTAL', 'VALOR': None},
            {'CONCEPTO': 'BRP SEP', 'VALOR': int(brp_sep)},
            {'CONCEPTO': 'BRP PIE', 'VALOR': int(brp_pie)},
            {'CONCEPTO': 'BRP NORMAL', 'VALOR': int(brp_normal)},
            {'CONCEPTO': 'BRP TOTAL', 'VALOR': int(brp_total)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'PORCENTAJES', 'VALOR': None},
            {'CONCEPTO': '% SEP', 'VALOR': pct_sep},
            {'CONCEPTO': '% PIE', 'VALOR': pct_pie},
            {'CONCEPTO': '% NORMAL', 'VALOR': pct_normal},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'DAEM (Subvención)', 'VALOR': None},
            {'CONCEPTO': 'DAEM SEP', 'VALOR': int(daem_sep)},
            {'CONCEPTO': 'DAEM PIE', 'VALOR': int(daem_pie)},
            {'CONCEPTO': 'DAEM NORMAL', 'VALOR': int(daem_normal)},
            {'CONCEPTO': 'DAEM TOTAL', 'VALOR': int(daem_total)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'CPEIP (Transferencia)', 'VALOR': None},
            {'CONCEPTO': 'CPEIP SEP', 'VALOR': int(cpeip_sep)},
            {'CONCEPTO': 'CPEIP PIE', 'VALOR': int(cpeip_pie)},
            {'CONCEPTO': 'CPEIP NORMAL', 'VALOR': int(cpeip_normal)},
            {'CONCEPTO': 'CPEIP TOTAL', 'VALOR': int(cpeip_total)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'DETALLE RECONOCIMIENTO', 'VALOR': None},
            {'CONCEPTO': 'Reconocimiento SEP', 'VALOR': int(recon_sep)},
            {'CONCEPTO': 'Reconocimiento PIE', 'VALOR': int(recon_pie)},
            {'CONCEPTO': 'Reconocimiento NORMAL', 'VALOR': int(recon_normal)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'DETALLE TRAMO', 'VALOR': None},
            {'CONCEPTO': 'Tramo SEP', 'VALOR': int(tramo_sep)},
            {'CONCEPTO': 'Tramo PIE', 'VALOR': int(tramo_pie)},
            {'CONCEPTO': 'Tramo NORMAL', 'VALOR': int(tramo_normal)},
            {'CONCEPTO': '', 'VALOR': None},
            {'CONCEPTO': 'DETALLE ALUMNOS PRIORITARIOS (CPEIP)', 'VALOR': None},
            {'CONCEPTO': 'Prioritarios SEP', 'VALOR': int(prior_sep)},
            {'CONCEPTO': 'Prioritarios PIE', 'VALOR': int(prior_pie)},
            {'CONCEPTO': 'Prioritarios NORMAL', 'VALOR': int(prior_normal)},
        ]

        return pd.DataFrame(resumen)
    
    def _load_web_sostenedor(self, path: Path, month_filter: Optional[str] = None) -> pd.DataFrame:
        """Carga y valida el archivo web_sostenedor (CSV o Excel).

        Args:
            month_filter: Mes a filtrar ('01'-'12'). None = sin filtro.
        """
        self.validate_file(path)

        if self.is_csv(path):
            try:
                df = pd.read_csv(str(path), encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(str(path), encoding='latin-1')
            # Si la primera columna no parece ser RBD, probar con header=1
            if 'Rbd' not in str(df.columns[0]) and 'RBD' not in str(df.columns[0]).upper():
                try:
                    df = pd.read_csv(str(path), encoding='utf-8', header=1)
                except UnicodeDecodeError:
                    df = pd.read_csv(str(path), encoding='latin-1', header=1)
        else:
            try:
                xlsx = pd.ExcelFile(str(path), engine='openpyxl')
            except TypeError:
                # openpyxl puede fallar con estilos corruptos; usar calamine
                xlsx = pd.ExcelFile(str(path), engine='calamine')
            sheet_name = xlsx.sheet_names[0]

            # Intentar leer con header en fila 0, si falla probar fila 1
            df = pd.read_excel(xlsx, sheet_name=sheet_name, header=0)

            # Si la primera columna no parece ser RBD, probar con header=1
            if 'Rbd' not in str(df.columns[0]) and 'RBD' not in str(df.columns[0]).upper():
                df = pd.read_excel(xlsx, sheet_name=sheet_name, header=1)

        df.columns = df.columns.str.strip()

        # Filtrar por mes si se especifica
        if month_filter:
            mes_col = next((c for c in df.columns if c.strip().lower() == 'mes'), None)
            if mes_col:
                from config.columns import MESES_NUM_TO_NAME, normalize_month_value
                month_name = MESES_NUM_TO_NAME.get(month_filter, '')
                # Normalizar cada valor de la columna Mes a número y comparar
                mask = df[mes_col].apply(
                    lambda v: normalize_month_value(v) == month_filter
                )
                df = df[mask]
                if df.empty:
                    raise ProcessorError(
                        f"No hay datos para {month_name or month_filter} en web sostenedor"
                    )
                df = df.reset_index(drop=True)
                self.logger.info(
                    f"Filtrado web sostenedor: {len(df)} filas para "
                    f"{month_name or month_filter}"
                )

        # Buscar columnas de forma flexible
        def find_col(target):
            target_lower = target.lower().strip()
            for col in df.columns:
                if col.lower().strip() == target_lower:
                    return col
                if target_lower in col.lower():
                    return col
            return None
        
        # Mapear columnas
        self.cols_actual = {
            'rbd': find_col('rbd') or find_col('establecimiento'),
            'rut': find_col('rut (docente)') or find_col('rut'),
            'horas_contrato': find_col('horas de contrato') or find_col('horas'),
            'nombres': find_col('nombres (docente)') or find_col('nombres'),
            'apellido1': find_col('primer apellido') or find_col('apellido'),
            'apellido2': find_col('segundo apellido'),
            'tipo_pago': find_col('tipo de pago'),
            'tramo': find_col('tramo'),
            'total_reconocimiento': find_col('total reconocimiento profesional'),
            'total_tramo': find_col('total tramo'),
            'subv_reconocimiento': find_col('total subvención reconocimiento'),
            'transf_reconocimiento': find_col('total transferencia directa reconocimiento'),
            'subv_tramo': find_col('subvención tramo'),
            'transf_tramo': find_col('transferencia directa tramo'),
            'asig_prioritarios': find_col('asignación directa alumnos prioritarios') or find_col('alumnos prioritarios'),
        }
        
        # Verificar mínimas requeridas
        required = ['rbd', 'rut', 'horas_contrato']
        missing = [r for r in required if not self.cols_actual.get(r)]
        if missing:
            raise ProcessorError(f"No se encontraron columnas: {missing}")

        # Generar alertas de columnas
        self.column_alerts = []

        # Columnas críticas faltantes (afectan cálculo)
        for key in WEB_CRITICAL_COLUMNS:
            if not self.cols_actual.get(key):
                nombre_amigable = WEB_FRIENDLY_NAMES.get(key, key)
                self.column_alerts.append({
                    'nivel': 'error',
                    'tipo': 'columna_critica',
                    'columna_key': key,
                    'columna_nombre': nombre_amigable,
                    'mensaje': f"No se encontró '{nombre_amigable}'. Los montos de este concepto serán $0."
                })
                self.logger.warning(f"Columna crítica no encontrada: {nombre_amigable}")

        # Columnas informativas faltantes
        for key in WEB_INFO_COLUMNS:
            if not self.cols_actual.get(key):
                nombre_amigable = WEB_FRIENDLY_NAMES.get(key, key)
                self.column_alerts.append({
                    'nivel': 'warning',
                    'tipo': 'columna_info',
                    'columna_key': key,
                    'columna_nombre': nombre_amigable,
                    'mensaje': f"No se encontró '{nombre_amigable}' (informativa, no afecta cálculo)."
                })

        # Columnas nuevas no reconocidas
        columnas_conocidas = set()
        for v in WEB_SOSTENEDOR_COLUMNS.values():
            columnas_conocidas.add(v.lower().strip())
        for v in self.cols_actual.values():
            if v:
                columnas_conocidas.add(v.lower().strip())

        columnas_archivo = {col.lower().strip() for col in df.columns}
        desconocidas = columnas_archivo - columnas_conocidas - {'rut_norm'}

        if desconocidas:
            nombres_originales = [col for col in df.columns if col.lower().strip() in desconocidas]
            self.column_alerts.append({
                'nivel': 'info',
                'tipo': 'columna_nueva',
                'columna_key': None,
                'columna_nombre': None,
                'columnas_lista': nombres_originales,
                'mensaje': f"Se encontraron {len(nombres_originales)} columnas nuevas: {', '.join(nombres_originales)}"
            })

        # Normalizar RUT
        df['RUT_NORM'] = df[self.cols_actual['rut']].apply(normalize_rut)

        self.logger.info(f"Columnas mapeadas correctamente")
        return df

    def get_column_alerts(self):
        """Retorna las alertas de columnas generadas durante la carga."""
        return self.column_alerts
    
    def _load_processed_file(self, path: Path, tipo: str) -> pd.DataFrame:
        """Carga archivo procesado (SEP o PIE) - CSV o Excel."""
        self.validate_file(path)

        if self.is_csv(path):
            try:
                df = pd.read_csv(str(path), encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(str(path), encoding='latin-1')
        else:
            df = pd.read_excel(str(path), engine='openpyxl')
        
        # Buscar columna RUT
        rut_col = None
        for col in df.columns:
            if 'rut' in str(col).lower():
                rut_col = col
                break
        
        if not rut_col:
            raise ProcessorError(f"Archivo {tipo} no tiene columna de RUT")
        
        df['RUT_NORM'] = df[rut_col].apply(normalize_rut)
        return df
    
    def _build_hours_map(self, df_sep: pd.DataFrame, df_pie: pd.DataFrame) -> Dict:
        """Construye mapa de horas por docente y tipo."""
        horas_map = {}
        
        # Procesar SEP
        for _, row in df_sep.iterrows():
            rut = row.get('RUT_NORM', '')
            if not rut:
                continue
            
            if rut not in horas_map:
                horas_map[rut] = {'SEP': 0, 'PIE': 0, 'SN': 0, 'TOTAL': 0}
            
            sep_hours = float(row.get('SEP', 0) or 0)
            horas_map[rut]['SEP'] += sep_hours
            horas_map[rut]['TOTAL'] += sep_hours
        
        # Procesar PIE
        for _, row in df_pie.iterrows():
            rut = row.get('RUT_NORM', '')
            if not rut:
                continue
            
            if rut not in horas_map:
                horas_map[rut] = {'SEP': 0, 'PIE': 0, 'SN': 0, 'TOTAL': 0}
            
            pie_hours = float(row.get('PIE', 0) or 0)
            sn_hours = float(row.get('SN', 0) or 0)
            
            horas_map[rut]['PIE'] += pie_hours
            horas_map[rut]['SN'] += sn_hours
            horas_map[rut]['TOTAL'] += pie_hours + sn_hours
        
        return horas_map
    
    def _build_revision_list(self, horas_map, ruts_web, ruts_procesados, df_web, df_sep, df_pie) -> List[Dict]:
        """Construye lista de docentes a revisar."""
        revisar = []
        
        # Columnas para obtener info de web_sostenedor
        col_nombres = self.cols_actual.get('nombres')
        col_ap1 = self.cols_actual.get('apellido1')
        col_ap2 = self.cols_actual.get('apellido2')
        col_tipo_pago = self.cols_actual.get('tipo_pago')
        col_horas = self.cols_actual.get('horas_contrato')
        
        def get_docente_info(rut):
            """Obtiene info del docente desde web_sostenedor o archivos procesados."""
            # Primero buscar en web_sostenedor
            doc = df_web[df_web['RUT_NORM'] == rut]
            if len(doc) > 0:
                row = doc.iloc[0]
                nombre = str(row.get(col_nombres, '')) if col_nombres and col_nombres in df_web.columns else ''
                ap1 = str(row.get(col_ap1, '')) if col_ap1 and col_ap1 in df_web.columns else ''
                ap2 = str(row.get(col_ap2, '')) if col_ap2 and col_ap2 in df_web.columns else ''
                apellidos = f"{ap1} {ap2}".strip()
                tipo_pago = str(row.get(col_tipo_pago, '')) if col_tipo_pago and col_tipo_pago in df_web.columns else ''
            else:
                # Buscar en archivos procesados (tienen columna 'nombre' con nombre completo)
                nombre = ''
                apellidos = ''
                tipo_pago = ''
                
                # Buscar en SEP
                doc_sep = df_sep[df_sep['RUT_NORM'] == rut]
                if len(doc_sep) > 0 and 'nombre' in df_sep.columns:
                    nombre_completo = str(doc_sep.iloc[0].get('nombre', ''))
                    if nombre_completo and nombre_completo != 'nan':
                        # El nombre viene como "APELLIDO1 APELLIDO2 NOMBRES"
                        partes = nombre_completo.split()
                        if len(partes) >= 3:
                            apellidos = f"{partes[0]} {partes[1]}"
                            nombre = ' '.join(partes[2:])
                        else:
                            nombre = nombre_completo
                
                # Si no encontró en SEP, buscar en PIE
                if not nombre:
                    doc_pie = df_pie[df_pie['RUT_NORM'] == rut]
                    if len(doc_pie) > 0 and 'nombre' in df_pie.columns:
                        nombre_completo = str(doc_pie.iloc[0].get('nombre', ''))
                        if nombre_completo and nombre_completo != 'nan':
                            partes = nombre_completo.split()
                            if len(partes) >= 3:
                                apellidos = f"{partes[0]} {partes[1]}"
                                nombre = ' '.join(partes[2:])
                            else:
                                nombre = nombre_completo
            
            # Limpiar 'nan'
            nombre = '' if nombre == 'nan' else nombre
            apellidos = '' if apellidos == 'nan' or apellidos == 'nan nan' else apellidos
            tipo_pago = '' if tipo_pago == 'nan' else tipo_pago
            return nombre, apellidos, tipo_pago
        
        # 1. Docentes que exceden 44 horas
        for rut, horas in horas_map.items():
            total = horas['TOTAL']
            if total > self.MAX_HORAS:
                nombre, apellidos, tipo_pago = get_docente_info(rut)
                # Si no hay tipo_pago pero excede horas, probablemente sea reemplazo
                if not tipo_pago and total > self.MAX_HORAS:
                    tipo_pago = '(No en MINEDUC - posible reemplazo)'
                
                revisar.append({
                    'RUT': rut,
                    'NOMBRE': nombre,
                    'APELLIDOS': apellidos,
                    'TIPO_PAGO': tipo_pago,
                    'MOTIVO': 'EXCEDE 44 HORAS',
                    'HORAS_SEP': horas['SEP'],
                    'HORAS_PIE': horas['PIE'],
                    'HORAS_SN': horas['SN'],
                    'HORAS_TOTAL': total,
                    'EXCESO': total - self.MAX_HORAS,
                    'DETALLE': f"SEP:{horas['SEP']:.0f} + PIE:{horas['PIE']:.0f} + SN:{horas['SN']:.0f} = {total:.0f} hrs",
                    'ACCION': 'Verificar si es reemplazante o error'
                })
        
        # 2. Docentes sin liquidación (en MINEDUC pero no en SEP/PIE)
        sin_match = ruts_web - ruts_procesados
        for rut in sin_match:
            doc = df_web[df_web['RUT_NORM'] == rut]
            if len(doc) == 0:
                continue
            
            row = doc.iloc[0]
            nombre = str(row.get(col_nombres, '')) if col_nombres and col_nombres in df_web.columns else ''
            ap1 = str(row.get(col_ap1, '')) if col_ap1 and col_ap1 in df_web.columns else ''
            ap2 = str(row.get(col_ap2, '')) if col_ap2 and col_ap2 in df_web.columns else ''
            apellidos = f"{ap1} {ap2}".strip()
            tipo_pago = str(row.get(col_tipo_pago, '')) if col_tipo_pago and col_tipo_pago in df_web.columns else ''
            horas_contrato = row.get(col_horas, 0) if col_horas and col_horas in df_web.columns else 0
            
            # Limpiar 'nan'
            nombre = '' if nombre == 'nan' else nombre
            apellidos = '' if apellidos == 'nan' or apellidos == 'nan nan' else apellidos
            tipo_pago = '' if tipo_pago == 'nan' else tipo_pago
            
            revisar.append({
                'RUT': rut,
                'NOMBRE': nombre,
                'APELLIDOS': apellidos,
                'TIPO_PAGO': tipo_pago,
                'MOTIVO': 'SIN LIQUIDACIÓN',
                'HORAS_SEP': 0,
                'HORAS_PIE': 0,
                'HORAS_SN': 0,
                'HORAS_TOTAL': 0,
                'HORAS_CONTRATO_MINEDUC': horas_contrato,
                'DETALLE': 'En MINEDUC pero no en archivos SEP/PIE',
                'ACCION': 'Verificar licencia, nuevo ingreso, o falta en liquidaciones'
            })
        
        # Log
        exceden = len([r for r in revisar if r['MOTIVO'] == 'EXCEDE 44 HORAS'])
        sin_liq = len([r for r in revisar if r['MOTIVO'] == 'SIN LIQUIDACIÓN'])
        reemplazos = len([r for r in revisar if 'reemplazo' in r.get('TIPO_PAGO', '').lower()])
        
        if exceden > 0:
            self.logger.warning(f"⚠️ {exceden} docentes exceden 44 horas")
        if reemplazos > 0:
            self.logger.info(f"ℹ️ {reemplazos} de ellos son tipo 'Reemplazo'")
        if sin_liq > 0:
            self.logger.warning(f"⚠️ {sin_liq} docentes sin liquidación SEP/PIE")
        
        return revisar
    
    def _identify_multi_establishment(self, df: pd.DataFrame) -> pd.DataFrame:
        """Identifica docentes en múltiples establecimientos."""
        col_horas = self.cols_actual['horas_contrato']
        col_rbd = self.cols_actual['rbd']
        
        stats = df.groupby('RUT_NORM').agg({
            col_rbd: 'nunique',
            col_horas: 'sum'
        }).reset_index()
        stats.columns = ['RUT_NORM', 'NUM_ESTABLECIMIENTOS', 'TOTAL_HORAS_MINEDUC']
        
        df = df.merge(stats, on='RUT_NORM', how='left')
        df['ES_MULTI'] = df['NUM_ESTABLECIMIENTOS'] > 1
        
        return df
    
    def _distribute_by_establishment(self, df: pd.DataFrame) -> pd.DataFrame:
        """Copia montos por establecimiento (MINEDUC ya los trae separados por fila/RBD)."""
        # Los montos en el archivo MINEDUC ya vienen correctamente asignados
        # por establecimiento (cada fila = un docente en un RBD).
        # NO se debe multiplicar por proporción de horas, eso causaría doble prorrateo.

        col_total_recon = self.cols_actual.get('total_reconocimiento')
        col_total_tramo = self.cols_actual.get('total_tramo')

        if col_total_recon and col_total_recon in df.columns:
            df['RECONOCIMIENTO_DIST'] = df[col_total_recon].fillna(0).round(0)
        else:
            df['RECONOCIMIENTO_DIST'] = 0

        if col_total_tramo and col_total_tramo in df.columns:
            df['TRAMO_DIST'] = df[col_total_tramo].fillna(0).round(0)
        else:
            df['TRAMO_DIST'] = 0

        # Sub-componentes DAEM/CPEIP
        for dist_col, src_key in [
            ('SUBV_RECON_DIST', 'subv_reconocimiento'),
            ('TRANSF_RECON_DIST', 'transf_reconocimiento'),
            ('SUBV_TRAMO_DIST', 'subv_tramo'),
            ('TRANSF_TRAMO_DIST', 'transf_tramo'),
            ('ASIG_PRIOR_DIST', 'asig_prioritarios'),
        ]:
            col = self.cols_actual.get(src_key)
            if col and col in df.columns:
                df[dist_col] = df[col].fillna(0).round(0)
            else:
                df[dist_col] = 0

        return df
    
    def _classify_by_subvencion(self, df: pd.DataFrame, horas_map: Dict) -> pd.DataFrame:
        """Clasifica BRP por tipo de subvención (SEP/PIE/NORMAL) y pagador (DAEM/CPEIP)."""

        # Columnas BRP totales
        brp_cols = [
            'BRP_RECONOCIMIENTO_SEP', 'BRP_RECONOCIMIENTO_PIE', 'BRP_RECONOCIMIENTO_NORMAL',
            'BRP_TRAMO_SEP', 'BRP_TRAMO_PIE', 'BRP_TRAMO_NORMAL',
        ]

        # Columnas desglose DAEM/CPEIP
        daem_cpeip_cols = [
            'DAEM_RECON_SEP', 'DAEM_RECON_PIE', 'DAEM_RECON_NORMAL',
            'CPEIP_RECON_SEP', 'CPEIP_RECON_PIE', 'CPEIP_RECON_NORMAL',
            'DAEM_TRAMO_SEP', 'DAEM_TRAMO_PIE', 'DAEM_TRAMO_NORMAL',
            'CPEIP_TRAMO_SEP', 'CPEIP_TRAMO_PIE', 'CPEIP_TRAMO_NORMAL',
            'CPEIP_PRIOR_SEP', 'CPEIP_PRIOR_PIE', 'CPEIP_PRIOR_NORMAL',
        ]

        for col in brp_cols + daem_cpeip_cols:
            df[col] = 0.0

        for idx, row in df.iterrows():
            rut = row.get('RUT_NORM', '')
            recon_dist = row.get('RECONOCIMIENTO_DIST', 0) or 0
            tramo_dist = row.get('TRAMO_DIST', 0) or 0
            subv_recon = row.get('SUBV_RECON_DIST', 0) or 0
            transf_recon = row.get('TRANSF_RECON_DIST', 0) or 0
            subv_tramo = row.get('SUBV_TRAMO_DIST', 0) or 0
            transf_tramo = row.get('TRANSF_TRAMO_DIST', 0) or 0
            asig_prior = row.get('ASIG_PRIOR_DIST', 0) or 0

            if not rut:
                continue

            # Obtener proporción de horas
            horas = horas_map.get(rut, {'SEP': 0, 'PIE': 0, 'SN': 0, 'TOTAL': 0})
            total_horas = horas['TOTAL']

            # Exportar horas por subvención
            df.at[idx, 'HORAS_SEP'] = horas['SEP']
            df.at[idx, 'HORAS_PIE'] = horas['PIE']
            df.at[idx, 'HORAS_SN'] = horas['SN']

            if total_horas == 0:
                # Sin info de horas, todo va a NORMAL
                df.at[idx, 'BRP_RECONOCIMIENTO_NORMAL'] = recon_dist
                df.at[idx, 'BRP_TRAMO_NORMAL'] = tramo_dist
                df.at[idx, 'DAEM_RECON_NORMAL'] = subv_recon
                df.at[idx, 'CPEIP_RECON_NORMAL'] = transf_recon
                df.at[idx, 'DAEM_TRAMO_NORMAL'] = subv_tramo
                df.at[idx, 'CPEIP_TRAMO_NORMAL'] = transf_tramo
                df.at[idx, 'CPEIP_PRIOR_NORMAL'] = asig_prior
                continue

            # Calcular proporciones
            prop_sep = horas['SEP'] / total_horas
            prop_pie = horas['PIE'] / total_horas

            # Distribuir con resto exacto: SEP y PIE se redondean,
            # NORMAL = total - SEP - PIE (garantiza suma exacta)
            def split3(total_val):
                v_sep = round(total_val * prop_sep)
                v_pie = round(total_val * prop_pie)
                v_sn = total_val - v_sep - v_pie
                return v_sep, v_pie, v_sn

            # DAEM Reconocimiento (subvención) — distribuir por horas
            s, p, n = split3(subv_recon)
            df.at[idx, 'DAEM_RECON_SEP'] = s
            df.at[idx, 'DAEM_RECON_PIE'] = p
            df.at[idx, 'DAEM_RECON_NORMAL'] = n

            # CPEIP Reconocimiento (transferencia) — 100% Normal
            df.at[idx, 'CPEIP_RECON_SEP'] = 0
            df.at[idx, 'CPEIP_RECON_PIE'] = 0
            df.at[idx, 'CPEIP_RECON_NORMAL'] = transf_recon

            # BRP Reconocimiento total = DAEM + CPEIP
            df.at[idx, 'BRP_RECONOCIMIENTO_SEP'] = s
            df.at[idx, 'BRP_RECONOCIMIENTO_PIE'] = p
            df.at[idx, 'BRP_RECONOCIMIENTO_NORMAL'] = n + transf_recon

            # DAEM Tramo (subvención) — distribuir por horas
            s, p, n = split3(subv_tramo)
            df.at[idx, 'DAEM_TRAMO_SEP'] = s
            df.at[idx, 'DAEM_TRAMO_PIE'] = p
            df.at[idx, 'DAEM_TRAMO_NORMAL'] = n

            # CPEIP Tramo (transferencia) — 100% Normal
            df.at[idx, 'CPEIP_TRAMO_SEP'] = 0
            df.at[idx, 'CPEIP_TRAMO_PIE'] = 0
            df.at[idx, 'CPEIP_TRAMO_NORMAL'] = transf_tramo

            # BRP Tramo total = DAEM + CPEIP
            df.at[idx, 'BRP_TRAMO_SEP'] = s
            df.at[idx, 'BRP_TRAMO_PIE'] = p
            df.at[idx, 'BRP_TRAMO_NORMAL'] = n + transf_tramo

            # CPEIP Alumnos Prioritarios — 100% Normal
            df.at[idx, 'CPEIP_PRIOR_SEP'] = 0
            df.at[idx, 'CPEIP_PRIOR_PIE'] = 0
            df.at[idx, 'CPEIP_PRIOR_NORMAL'] = asig_prior

        # Totales DAEM por subvención
        df['TOTAL_DAEM_SEP'] = df['DAEM_RECON_SEP'] + df['DAEM_TRAMO_SEP']
        df['TOTAL_DAEM_PIE'] = df['DAEM_RECON_PIE'] + df['DAEM_TRAMO_PIE']
        df['TOTAL_DAEM_NORMAL'] = df['DAEM_RECON_NORMAL'] + df['DAEM_TRAMO_NORMAL']

        # Totales CPEIP por subvención
        df['TOTAL_CPEIP_SEP'] = df['CPEIP_RECON_SEP'] + df['CPEIP_TRAMO_SEP'] + df['CPEIP_PRIOR_SEP']
        df['TOTAL_CPEIP_PIE'] = df['CPEIP_RECON_PIE'] + df['CPEIP_TRAMO_PIE'] + df['CPEIP_PRIOR_PIE']
        df['TOTAL_CPEIP_NORMAL'] = df['CPEIP_RECON_NORMAL'] + df['CPEIP_TRAMO_NORMAL'] + df['CPEIP_PRIOR_NORMAL']

        # Totales BRP por tipo (DAEM + CPEIP)
        df['BRP_SEP'] = df['TOTAL_DAEM_SEP'] + df['TOTAL_CPEIP_SEP']
        df['BRP_PIE'] = df['TOTAL_DAEM_PIE'] + df['TOTAL_CPEIP_PIE']
        df['BRP_NORMAL'] = df['TOTAL_DAEM_NORMAL'] + df['TOTAL_CPEIP_NORMAL']
        df['BRP_TOTAL'] = df['BRP_SEP'] + df['BRP_PIE'] + df['BRP_NORMAL']

        # Convertir a enteros
        int_cols = brp_cols + daem_cpeip_cols + [
            'TOTAL_DAEM_SEP', 'TOTAL_DAEM_PIE', 'TOTAL_DAEM_NORMAL',
            'TOTAL_CPEIP_SEP', 'TOTAL_CPEIP_PIE', 'TOTAL_CPEIP_NORMAL',
            'BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL',
        ]
        for col in int_cols:
            df[col] = df[col].fillna(0).astype(int)

        return df
    
    def _create_multi_establishment_sheet(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Crea hoja con desglose de docentes que trabajan en 2+ establecimientos."""
        if 'ES_MULTI' not in df.columns:
            return None

        df_multi = df[df['ES_MULTI'] == True].copy()
        if df_multi.empty:
            return None

        col_rbd = self.cols_actual.get('rbd')
        col_rut = self.cols_actual.get('rut')
        col_nombres = self.cols_actual.get('nombres')
        col_ap1 = self.cols_actual.get('apellido1')
        col_ap2 = self.cols_actual.get('apellido2')
        col_horas = self.cols_actual.get('horas_contrato')
        col_tramo = self.cols_actual.get('tramo')

        rows = []
        for rut in df_multi['RUT_NORM'].unique():
            filas_docente = df_multi[df_multi['RUT_NORM'] == rut]

            # Info del docente
            first = filas_docente.iloc[0]
            nombre = ''
            if col_nombres and col_nombres in filas_docente.columns:
                ap1 = str(first.get(col_ap1, '')) if col_ap1 else ''
                ap2 = str(first.get(col_ap2, '')) if col_ap2 else ''
                nombres = str(first.get(col_nombres, ''))
                nombre = f"{ap1} {ap2} {nombres}".strip()
                nombre = nombre.replace('nan', '').strip()

            tramo = str(first.get(col_tramo, '')) if col_tramo and col_tramo in filas_docente.columns else ''
            if tramo == 'nan':
                tramo = ''

            total_reconocimiento = 0
            total_tramo = 0
            total_prioritarios = 0
            total_brp = 0

            for _, fila in filas_docente.iterrows():
                rbd_val = fila.get(col_rbd, '') if col_rbd else ''
                horas_val = fila.get(col_horas, 0) if col_horas else 0
                recon = fila.get('RECONOCIMIENTO_DIST', 0) or 0
                tramo_val = fila.get('TRAMO_DIST', 0) or 0
                prior = fila.get('ASIG_PRIOR_DIST', 0) or 0
                brp_t = fila.get('BRP_TOTAL', 0) or 0

                total_reconocimiento += recon
                total_tramo += tramo_val
                total_prioritarios += prior
                total_brp += brp_t

                rows.append({
                    'RUT': rut,
                    'NOMBRE': nombre,
                    'TRAMO': tramo,
                    'RBD': rbd_val,
                    'HORAS_CONTRATO': horas_val,
                    'RECONOCIMIENTO_MINEDUC': int(recon),
                    'TRAMO_MINEDUC': int(tramo_val),
                    'PRIORITARIOS_MINEDUC': int(prior),
                    'BRP_SEP': int(fila.get('BRP_SEP', 0) or 0),
                    'BRP_PIE': int(fila.get('BRP_PIE', 0) or 0),
                    'BRP_NORMAL': int(fila.get('BRP_NORMAL', 0) or 0),
                    'BRP_TOTAL': int(brp_t),
                    'TIPO_FILA': 'DETALLE',
                })

            # Fila de total por docente
            rows.append({
                'RUT': rut,
                'NOMBRE': nombre,
                'TRAMO': tramo,
                'RBD': 'TOTAL',
                'HORAS_CONTRATO': filas_docente[col_horas].sum() if col_horas and col_horas in filas_docente.columns else 0,
                'RECONOCIMIENTO_MINEDUC': int(total_reconocimiento),
                'TRAMO_MINEDUC': int(total_tramo),
                'PRIORITARIOS_MINEDUC': int(total_prioritarios),
                'BRP_SEP': int(filas_docente['BRP_SEP'].sum()),
                'BRP_PIE': int(filas_docente['BRP_PIE'].sum()),
                'BRP_NORMAL': int(filas_docente['BRP_NORMAL'].sum()),
                'BRP_TOTAL': int(total_brp),
                'TIPO_FILA': 'TOTAL_DOCENTE',
            })

        result = pd.DataFrame(rows)
        # Ordenar por RUT y luego TIPO_FILA
        result = result.sort_values(['RUT', 'TIPO_FILA'], ascending=[True, True])
        return result

    @staticmethod
    def detect_web_months(path: Path) -> Optional[List[str]]:
        """Detecta meses únicos en un archivo web sostenedor.

        Returns:
            Lista de month numbers ordenados (ej: ['01', '03']), o None si no tiene columna Mes.
        """
        try:
            from config.columns import normalize_month_value

            ext = path.suffix.lower()
            if ext == '.csv':
                try:
                    df = pd.read_csv(str(path), encoding='utf-8', nrows=50000)
                except UnicodeDecodeError:
                    df = pd.read_csv(str(path), encoding='latin-1', nrows=50000)
            else:
                df = pd.read_excel(str(path), engine='openpyxl')

            df.columns = df.columns.str.strip()
            mes_col = next((c for c in df.columns if c.strip().lower() == 'mes'), None)
            if not mes_col:
                return None

            raw_values = df[mes_col].dropna().unique()
            month_nums = set()
            for val in raw_values:
                num = normalize_month_value(val)
                if num:
                    month_nums.add(num)
            return sorted(month_nums) if month_nums else None
        except Exception:
            return None

    def _log_statistics(self, df: pd.DataFrame) -> None:
        """Genera estadísticas."""
        total_docentes = df['RUT_NORM'].nunique()

        brp_sep = df['BRP_SEP'].sum()
        brp_pie = df['BRP_PIE'].sum()
        brp_normal = df['BRP_NORMAL'].sum()
        brp_total = brp_sep + brp_pie + brp_normal

        recon_total = df['RECONOCIMIENTO_DIST'].sum() if 'RECONOCIMIENTO_DIST' in df.columns else 0
        tramo_total = df['TRAMO_DIST'].sum() if 'TRAMO_DIST' in df.columns else 0

        daem_total = df['TOTAL_DAEM_SEP'].sum() + df['TOTAL_DAEM_PIE'].sum() + df['TOTAL_DAEM_NORMAL'].sum()
        cpeip_total = df['TOTAL_CPEIP_SEP'].sum() + df['TOTAL_CPEIP_PIE'].sum() + df['TOTAL_CPEIP_NORMAL'].sum()

        self.logger.info("=" * 60)
        self.logger.info("RESUMEN DE DISTRIBUCIÓN BRP")
        self.logger.info("=" * 60)
        self.logger.info(f"Total docentes: {total_docentes}")
        self.logger.info("-" * 60)
        self.logger.info(f"Reconocimiento Profesional: ${recon_total:,.0f}")
        self.logger.info(f"Tramo:                      ${tramo_total:,.0f}")
        self.logger.info("-" * 60)
        self.logger.info(f"DAEM (Subvención):          ${daem_total:,.0f}")
        self.logger.info(f"CPEIP (Transferencia):      ${cpeip_total:,.0f}")
        self.logger.info("-" * 60)
        if brp_total > 0:
            self.logger.info(f"→ SEP:    ${brp_sep:,.0f} ({100*brp_sep/brp_total:.1f}%)")
            self.logger.info(f"→ PIE:    ${brp_pie:,.0f} ({100*brp_pie/brp_total:.1f}%)")
            self.logger.info(f"→ NORMAL: ${brp_normal:,.0f} ({100*brp_normal/brp_total:.1f}%)")
        self.logger.info("=" * 60)
