"""
Procesador para remuneraciones SEP (Subvención Escolar Preferencial).

La SEP es una subvención chilena adicional para alumnos prioritarios. El flujo
de este procesador es:
1. Leer dos hojas del Excel: HORAS (detalle de horas por docente/colegio) y
   TOTAL (montos salariales completos del docente).
2. Sumar las horas SEP por docente para obtener su total de horas.
3. Combinar (merge) ambas hojas por Rut.
4. Prorratear cada monto salarial según las horas SEP:
       valor_SEP = (monto_total / horas_totales) * horas_SEP
   generando columnas con sufijo '_SEP'.
"""

from pathlib import Path
import pandas as pd

from processors.base import BaseProcessor, ProgressCallback
from config.columns import (
    SALARY_BENEFIT_COLUMNS,
    SPECIAL_SALARY_COLUMNS,
    get_available_columns
)


class SEPProcessor(BaseProcessor):
    """
    Procesador especializado para remuneraciones SEP.
    
    Calcula el prorrateo de salarios y beneficios según las horas SEP
    asignadas a cada docente por establecimiento.
    """
    
    def process_file(
        self, 
        input_path: Path, 
        output_path: Path, 
        progress_callback: ProgressCallback
    ) -> None:
        """
        Procesa archivo de remuneraciones para SEP.

        Orquesta el flujo completo (cargar -> validar -> procesar -> guardar) y
        reporta el avance a la UI mediante progress_callback. Cualquier excepción
        se loguea y se re-lanza para que la capa superior la muestre al usuario.
        """
        try:
            progress_callback(0, "Iniciando proceso SEP...")

            # Cargar datos: hojas HORAS y TOTAL del archivo de entrada.
            progress_callback(5, "Cargando datos...")
            df_horas, df_total = self.load_sheets(input_path)

            # Validar columnas requeridas (faltar una es error fatal). En HORAS
            # se exige además la columna de horas SEP, indispensable para prorratear.
            self.validate_columns(
                df_horas,
                self.config.REQUIRED_HORAS | {self.config.SEP_HOURS_COL},
                'HORAS'
            )
            self.validate_columns(df_total, self.config.REQUIRED_TOTAL, 'TOTAL')
            
            progress_callback(20, "Calculando horas por docente...")
            
            # Procesar datos
            result = self._process_data(df_horas, df_total, progress_callback)
            
            # Guardar
            progress_callback(90, "Guardando resultados...")
            self.safe_save(result, output_path)
            
            progress_callback(100, "¡Proceso SEP completado!")
            
        except Exception as e:
            self.logger.error(f"Error en proceso SEP: {str(e)}", exc_info=True)
            raise
    
    def _process_data(
        self, 
        df_horas: pd.DataFrame, 
        df_total: pd.DataFrame,
        progress_callback: ProgressCallback
    ) -> pd.DataFrame:
        """
        Lógica principal de procesamiento SEP.

        Recibe las hojas HORAS y TOTAL ya validadas y devuelve el DataFrame final
        con las columnas de salario prorrateadas por horas SEP (sufijo '_SEP').
        """

        # IDs de trazabilidad para poder rastrear el origen de cada fila tras el
        # merge (útil para depurar). Se eliminan antes de guardar.
        df_horas['ID_Horas'] = df_horas.index
        df_total['ID_Total'] = df_total.index

        # Sumar las horas SEP por docente (agrupa por RUT/Nombre) y obtener
        # 'TOTAL HORAS POR DOCENTE', el denominador del prorrateo.
        df_horas = self.calculate_total_hours_by_teacher(
            df_horas,
            hours_columns=[self.config.SEP_HOURS_COL]
        )

        progress_callback(30, "Combinando datos...")

        # Merge por Rut: se parte de df_total (todos los docentes con montos) y
        # se le adjuntan las horas. left join para no perder docentes sin horas.
        datos = pd.merge(df_total, df_horas, on=['Rut'], how='left').reset_index(drop=True)

        # Docentes sin horas SEP quedan con NaN tras el left join; se ponen en 0
        # para que el prorrateo dé 0 (y no NaN) en lugar de fallar.
        datos = datos.fillna({
            self.config.SEP_HOURS_COL: 0,
            'TOTAL HORAS POR DOCENTE': 0
        })

        progress_callback(50, "Calculando salarios proporcionales...")

        # Se prorratean tanto las columnas especiales (deben venir siempre) como
        # los beneficios opcionales. prorate_columns genera las '<col>_SEP' y
        # registra alertas por las columnas especiales que falten.
        all_salary_columns = SPECIAL_SALARY_COLUMNS + SALARY_BENEFIT_COLUMNS
        datos = self.prorate_columns(
            datos,
            columns=all_salary_columns,
            hours_column=self.config.SEP_HOURS_COL,
            total_hours_column='TOTAL HORAS POR DOCENTE',
            output_suffix='_SEP'
        )

        progress_callback(70, "Validando horas...")

        # Marcar (no eliminar) docentes que exceden el máximo de horas.
        datos = self.validate_hours(datos)

        # Quitar las columnas auxiliares de trazabilidad antes de exportar.
        for col in ['ID_Horas', 'ID_Total']:
            if col in datos.columns:
                datos = datos.drop(col, axis=1)

        # Ordenar el resultado para facilitar su revisión (por Rut y, si existe,
        # por Nombre).
        if 'Nombre' in datos.columns:
            datos = datos.sort_values(['Rut', 'Nombre'])
        else:
            datos = datos.sort_values('Rut')

        return datos
