"""
Procesador para remuneraciones PIE (Programa de Integración Escolar).
Maneja tanto horas PIE como SN (Subvención Normal).

Idea general del prorrateo:
  Cada docente puede tener horas asignadas a PIE y a SN. Los haberes que vienen
  en la hoja TOTAL (sueldo, asignaciones, beneficios) deben repartirse según la
  proporción de horas de cada tipo. El "valor por hora" se obtiene dividiendo el
  monto total del docente por su total de horas, y luego se multiplica por las
  horas PIE o SN de la fila.

Dos tratamientos distintos de columnas (ver config.columns):
  - SPECIAL_SALARY_COLUMNS: se SEPARAN en dos columnas nuevas, '<col> PIE' y
    '<col> SN' (ojo: con ESPACIO, no guion bajo). Permite ver el desglose.
  - SALARY_BENEFIT_COLUMNS: se COMBINAN (PIE + SN) en una sola columna '<col>_nuevo'.

Además se registran alertas cuando faltan columnas especiales esperadas
(ver _record_missing_special_columns en la clase base).
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
        """Procesa archivo de remuneraciones para PIE.

        Lee dos hojas del Excel de entrada:
          - 'HORAS': horas PIE/SN por docente y establecimiento.
          - 'TOTAL': haberes/montos totales por docente a prorratear.
        Escribe el resultado prorrateado en output_path.
        """
        try:
            progress_callback(0, "Iniciando proceso PIE...")

            # Cargar datos
            progress_callback(5, "Cargando datos...")
            # Se leen columnas 0-4 y 6-9 (se descarta la columna 5 a propósito;
            # el rango 6..10 es exclusivo en el extremo superior => 6,7,8,9).
            df_horas = self.load_excel_with_retry(
                input_path,
                'HORAS',
                usecols=list(range(0, 5)) + list(range(6, 10))  # Columnas específicas
            )
            df_total = self.load_excel_with_retry(input_path, 'TOTAL')

            # Normalizar Rut: la hoja TOTAL puede traer 'rut' en minúscula;
            # se unifica a 'Rut' para poder hacer merge con la hoja HORAS.
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
        """Lógica principal de procesamiento PIE.

        Flujo: (1) calcular total de horas por docente, (2) unir con los montos
        de la hoja TOTAL, (3) prorratear columnas especiales (separadas PIE/SN)
        y columnas de beneficios (combinadas), (4) validar y limpiar.
        """

        # Nombres reales de las columnas de horas PIE y SN (vienen de config).
        pie_col = self.config.PIE_HOURS_COL
        sn_col = self.config.SN_HOURS_COL

        # IDs de fila para poder rastrear/depurar tras los merges.
        df_horas['ID_Horas'] = df_horas.index
        df_total['ID_Total'] = df_total.index

        # Total de horas por fila (PIE + SN). df.get(sn_col, 0) evita error si
        # la hoja no trae columna SN.
        df_horas['TOTAL HORAS'] = df_horas[pie_col] + df_horas.get(sn_col, 0)
        # Descartar filas sin horas: no aportan al prorrateo y evitan dividir por 0.
        df_horas = df_horas[df_horas['TOTAL HORAS'] != 0].copy()

        # Columnas de horas a sumar (SN solo si existe en el archivo).
        hours_cols = [pie_col]
        if sn_col in df_horas.columns:
            hours_cols.append(sn_col)

        # Sumar todas las horas de un mismo docente (puede estar en varias filas/
        # establecimientos) para obtener el denominador del prorrateo.
        horas_agrupadas = df_horas.groupby(['Rut', 'Nombre'])[hours_cols].sum().reset_index()
        horas_agrupadas['TOTAL HORAS POR DOCENTE'] = horas_agrupadas[hours_cols].sum(axis=1)

        # Devolver el total por docente a cada fila original.
        df_horas = df_horas.merge(
            horas_agrupadas[['Rut', 'Nombre', 'TOTAL HORAS POR DOCENTE']],
            on=['Rut', 'Nombre'],
            how='left'
        )
        # 'TOTAL HORAS' era auxiliar por fila; ya no se necesita.
        df_horas = df_horas.drop('TOTAL HORAS', axis=1, errors='ignore')

        progress_callback(30, "Combinando datos...")

        # Unir montos (df_total) con horas (df_horas) por Rut. left join sobre
        # df_total para conservar a todos los docentes con montos.
        datos = pd.merge(df_total, df_horas, on=['Rut'], how='left').reset_index(drop=True)

        # Docentes sin horas tras el merge quedan con NaN; se rellenan con 0 para
        # que el prorrateo no falle (valor_por_hora quedará 0).
        fill_values = {
            pie_col: 0,
            'TOTAL HORAS POR DOCENTE': 0
        }
        if sn_col in datos.columns:
            fill_values[sn_col] = 0
        datos = datos.fillna(fill_values)

        progress_callback(50, "Calculando salarios proporcionales...")

        # Columnas especiales: se separan en '<col> PIE' y '<col> SN'.
        datos = self._process_special_columns(datos, pie_col, sn_col)

        progress_callback(60, "Procesando columnas de salarios y beneficios...")

        # Columnas de beneficio: se combinan (PIE + SN) en '<col>_nuevo'.
        datos = self._process_salary_columns(datos, pie_col, sn_col)

        progress_callback(75, "Validando horas...")

        # Validación de horas definida en la clase base.
        datos = self.validate_hours(datos)

        # Limpieza final: NaN -> 0 e infinitos (de divisiones) -> 0.
        datos = datos.fillna(0)
        datos = datos.replace([np.inf, -np.inf], 0)

        if 'Nombre' in datos.columns:
            datos = datos.sort_values(['Rut', 'Nombre'])

        # Quitar columnas de ID internas antes de exportar.
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

        Para cada columna especial disponible genera dos columnas nuevas:
          '<col> PIE' = valor_por_hora * horas_PIE
          '<col> SN'  = valor_por_hora * horas_SN   (solo si existe SN)
        donde valor_por_hora = monto_total_docente / total_horas_docente.
        Nota: los nombres nuevos usan ESPACIO como separador, no guion bajo.
        """
        # El merge previo puede duplicar nombres de columna; se conserva la
        # primera aparición para evitar ambigüedad al indexar df[col].
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep='first')]

        # Solo procesar las columnas especiales que realmente existen en el archivo.
        available = get_available_columns(df, SPECIAL_SALARY_COLUMNS)
        # Registrar alerta por las columnas especiales esperadas que faltan.
        self._record_missing_special_columns(SPECIAL_SALARY_COLUMNS, available)

        for col in available:
            # Valor por hora del docente para esta columna de haber.
            valor_por_hora = df[col] / df['TOTAL HORAS POR DOCENTE']
            # Docentes con 0 horas producen inf/NaN; se neutralizan a 0.
            valor_por_hora = valor_por_hora.replace([np.inf, -np.inf, np.nan], 0)

            # Monto correspondiente a las horas PIE (redondeado a entero de pesos).
            df[f'{col} PIE'] = (valor_por_hora * df[pie_col]).round().fillna(0).astype(int)

            # Monto correspondiente a las horas SN, solo si la columna SN existe.
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

        A diferencia de las columnas especiales (que se separan), aquí el monto
        de PIE y SN se COMBINA en una sola columna '<col>_nuevo' (con guion bajo):
          '<col>_nuevo' = valor_por_hora * (horas_PIE + horas_SN)
        """
        # El merge previo puede duplicar nombres de columna; conservar la primera.
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep='first')]

        # Horas de la fila que reciben el beneficio combinado (PIE + SN).
        df['SUMA POR FILA'] = df[pie_col]
        if sn_col in df.columns:
            df['SUMA POR FILA'] += df[sn_col]

        # Solo procesar las columnas de beneficio presentes en el archivo.
        available = get_available_columns(df, SALARY_BENEFIT_COLUMNS)

        for col in available:
            # Valor por hora del docente para esta columna de beneficio.
            valor_por_hora = df[col] / df['TOTAL HORAS POR DOCENTE']
            # Neutralizar inf/NaN de docentes con 0 horas.
            valor_por_hora = valor_por_hora.replace([np.inf, -np.inf, np.nan], 0)

            # Monto combinado PIE+SN de la fila, en pesos enteros.
            df[f'{col}_nuevo'] = (valor_por_hora * df['SUMA POR FILA']).round().fillna(0).astype(int)

        return df
