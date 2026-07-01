"""
Procesador para remuneraciones PIE (Programa de Integración Escolar).
Maneja tanto horas PIE como SN (Subvención Normal).
"""

from pathlib import Path
import pandas as pd
import numpy as np

from processors.base import BaseProcessor, ProgressCallback
from config.columns import (
    SALARY_BENEFIT_COLUMNS,
    SPECIAL_SALARY_COLUMNS,
    get_available_columns
)


class PIEProcessor(BaseProcessor):
    """
    Procesador especializado para remuneraciones PIE y Subvención Normal.
    
    Calcula el prorrateo de salarios y beneficios según las horas PIE y SN
    asignadas a cada docente por establecimiento.
    """
    
    def process_file(
        self, 
        input_path: Path, 
        output_path: Path, 
        progress_callback: ProgressCallback
    ) -> None:
        """Procesa archivo de remuneraciones para PIE."""
        try:
            progress_callback(0, "Iniciando proceso PIE...")
            
            # Cargar datos
            progress_callback(5, "Cargando datos...")
            df_horas = self.load_excel_with_retry(
                input_path, 
                'HORAS',
                usecols=list(range(0, 5)) + list(range(6, 10))  # Columnas específicas
            )
            df_total = self.load_excel_with_retry(input_path, 'TOTAL')
            
            # Normalizar Rut
            if 'rut' in df_total.columns:
                df_total = df_total.rename(columns={'rut': 'Rut'})
            
            progress_callback(10, "Calculando horas por docente...")
            
            # Procesar datos
            result = self._process_data(df_horas, df_total, progress_callback)
            
            # Guardar
            progress_callback(90, "Guardando resultados...")
            self.safe_save(result, output_path)
            
            progress_callback(100, "¡Proceso PIE completado!")
            
        except Exception as e:
            self.logger.error(f"Error en proceso PIE: {str(e)}", exc_info=True)
            raise
    
    def _process_data(
        self, 
        df_horas: pd.DataFrame, 
        df_total: pd.DataFrame,
        progress_callback: ProgressCallback
    ) -> pd.DataFrame:
        """Lógica principal de procesamiento PIE."""
        
        pie_col = self.config.PIE_HOURS_COL
        sn_col = self.config.SN_HOURS_COL
        
        # Agregar IDs
        df_horas['ID_Horas'] = df_horas.index
        df_total['ID_Total'] = df_total.index
        
        # Calcular total de horas (PIE + SN)
        df_horas['TOTAL HORAS'] = df_horas[pie_col] + df_horas.get(sn_col, 0)
        df_horas = df_horas[df_horas['TOTAL HORAS'] != 0].copy()
        
        # Agrupar por docente
        hours_cols = [pie_col]
        if sn_col in df_horas.columns:
            hours_cols.append(sn_col)
        
        horas_agrupadas = df_horas.groupby(['Rut', 'Nombre'])[hours_cols].sum().reset_index()
        horas_agrupadas['TOTAL HORAS POR DOCENTE'] = horas_agrupadas[hours_cols].sum(axis=1)
        
        # Merge para agregar total
        df_horas = df_horas.merge(
            horas_agrupadas[['Rut', 'Nombre', 'TOTAL HORAS POR DOCENTE']],
            on=['Rut', 'Nombre'],
            how='left'
        )
        df_horas = df_horas.drop('TOTAL HORAS', axis=1, errors='ignore')
        
        progress_callback(30, "Combinando datos...")
        
        # Combinar con df_total
        datos = pd.merge(df_total, df_horas, on=['Rut'], how='left').reset_index(drop=True)
        
        # Rellenar valores faltantes
        fill_values = {
            pie_col: 0,
            'TOTAL HORAS POR DOCENTE': 0
        }
        if sn_col in datos.columns:
            fill_values[sn_col] = 0
        datos = datos.fillna(fill_values)
        
        progress_callback(50, "Calculando salarios proporcionales...")
        
        # Procesar columnas especiales con sufijo PIE y SN
        datos = self._process_special_columns(datos, pie_col, sn_col)
        
        progress_callback(60, "Procesando columnas de salarios y beneficios...")
        
        # Procesar columnas de salario con cálculo combinado
        datos = self._process_salary_columns(datos, pie_col, sn_col)
        
        progress_callback(75, "Validando horas...")
        
        # Validar horas
        datos = self.validate_hours(datos)
        
        # Limpiar y ordenar
        datos = datos.fillna(0)
        datos = datos.replace([np.inf, -np.inf], 0)
        
        if 'Nombre' in datos.columns:
            datos = datos.sort_values(['Rut', 'Nombre'])
        
        for col in ['ID_Horas', 'ID_Total']:
            if col in datos.columns:
                datos = datos.drop(col, axis=1)
        
        return datos
    
    def _process_special_columns(
        self,
        df: pd.DataFrame,
        pie_col: str,
        sn_col: str
    ) -> pd.DataFrame:
        """
        Procesa columnas especiales creando versiones separadas para PIE y SN.
        """
        # Deduplicar columnas tras merge (puede generar duplicados)
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep='first')]

        available = get_available_columns(df, SPECIAL_SALARY_COLUMNS)
        self._record_missing_special_columns(SPECIAL_SALARY_COLUMNS, available)

        for col in available:
            valor_por_hora = df[col] / df['TOTAL HORAS POR DOCENTE']
            valor_por_hora = valor_por_hora.replace([np.inf, -np.inf, np.nan], 0)
            
            # Columna PIE
            df[f'{col} PIE'] = (valor_por_hora * df[pie_col]).round().fillna(0).astype(int)
            
            # Columna SN si existe
            if sn_col in df.columns:
                df[f'{col} SN'] = (valor_por_hora * df[sn_col]).round().fillna(0).astype(int)
        
        return df
    
    def _process_salary_columns(
        self,
        df: pd.DataFrame,
        pie_col: str,
        sn_col: str
    ) -> pd.DataFrame:
        """
        Procesa columnas de salario con suma de PIE + SN.
        """
        # Deduplicar columnas tras merge (puede generar duplicados)
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep='first')]

        # Crear columna de suma de horas por fila
        df['SUMA POR FILA'] = df[pie_col]
        if sn_col in df.columns:
            df['SUMA POR FILA'] += df[sn_col]
        
        available = get_available_columns(df, SALARY_BENEFIT_COLUMNS)
        
        for col in available:
            valor_por_hora = df[col] / df['TOTAL HORAS POR DOCENTE']
            valor_por_hora = valor_por_hora.replace([np.inf, -np.inf, np.nan], 0)
            
            df[f'{col}_nuevo'] = (valor_por_hora * df['SUMA POR FILA']).round().fillna(0).astype(int)
        
        return df
