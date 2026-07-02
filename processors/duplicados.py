"""
Procesador para consolidar registros duplicados de docentes.

Cuando un mismo docente aparece en varias filas (identificadas por una columna clave),
este procesador suma sus columnas numéricas y deja una sola fila consolidada, evitando
que un docente se pague o cuente más de una vez.
"""

import logging
from pathlib import Path
from typing import List
import pandas as pd

from processors.base import BaseProcessor, ProgressCallback, ProcessorError


class DuplicadosProcessor(BaseProcessor):
    """
    Procesador para consolidar registros duplicados.
    
    Identifica registros duplicados basados en una columna clave,
    suma los valores numéricos de las columnas especificadas, y
    elimina las filas duplicadas manteniendo solo la primera.
    """
    
    def __init__(self, duplicate_column: str = 'DUPLICADOS'):
        super().__init__()
        # Columna clave que define qué filas son "el mismo registro" (por defecto 'DUPLICADOS').
        self.duplicate_column = duplicate_column
    
    def process_file(
        self,
        input_path1: Path,
        input_path2: Path,
        output_path: Path,
        progress_callback: ProgressCallback
    ) -> None:
        """
        Procesa duplicados entre dos archivos.
        
        Args:
            input_path1: Archivo principal con datos
            input_path2: Archivo complementario (usado para validación/cruce)
            output_path: Donde guardar el resultado consolidado
            progress_callback: Función para reportar progreso
        """
        try:
            progress_callback(0, "Iniciando proceso de duplicados...")

            # Cargar el archivo principal (sobre el que se consolida).
            progress_callback(10, "Cargando primer archivo...")
            self.validate_file(input_path1)
            df = self._load_excel_safe(input_path1)

            # Cargar el archivo complementario (queda disponible para cruce/validación).
            progress_callback(20, "Cargando segundo archivo...")
            self.validate_file(input_path2)
            df_extra = self._load_excel_safe(input_path2)

            progress_callback(30, "Detectando duplicados...")

            # Sin la columna clave no se puede identificar duplicados: se aborta con error claro.
            if self.duplicate_column not in df.columns:
                raise ProcessorError(
                    f"La columna '{self.duplicate_column}' no existe en el archivo. "
                    "Verifique la estructura del archivo."
                )
            
            # Consolidar duplicados (suma de columnas y eliminación de filas repetidas).
            df = self._process_duplicates(df, progress_callback)

            progress_callback(80, "Guardando resultado final...")
            self.safe_save(df, output_path)
            
            progress_callback(100, f"¡Proceso completado! Archivo guardado en {output_path}")
            
        except Exception as e:
            self.logger.error(f"Error en DuplicadosProcessor: {str(e)}", exc_info=True)
            raise
    
    def _load_excel_safe(self, path: Path) -> pd.DataFrame:
        """Carga la hoja 'Hoja1' del Excel con reintentos ante errores transitorios."""
        return self.load_excel_with_retry(path, sheet_name='Hoja1')
    
    def _process_duplicates(
        self, 
        df: pd.DataFrame, 
        progress_callback: ProgressCallback
    ) -> pd.DataFrame:
        """
        Procesa y consolida registros duplicados.

        Estrategia: detectar las filas con clave repetida, sumar sus columnas numéricas,
        volcar esa suma sobre las filas afectadas y luego quedarse con la primera
        ocurrencia de cada clave. Si no hay duplicados, se devuelve el df solo ordenado.
        """
        dup_col = self.duplicate_column

        # keep=False marca TODAS las filas cuya clave aparece más de una vez.
        duplicados_mask = df.duplicated(subset=[dup_col], keep=False)
        df_duplicados = df[duplicados_mask]

        # Sin duplicados no hay nada que consolidar: se devuelve el df solo ordenado.
        if df_duplicados.empty:
            self.logger.info("No se encontraron registros duplicados")
            progress_callback(50, "No se encontraron duplicados")
            return df.sort_values(by=dup_col)

        num_duplicados = len(df_duplicados)
        self.logger.info(f"Se encontraron {num_duplicados} registros duplicados")
        progress_callback(40, f"Procesando {num_duplicados} duplicados...")
        
        # Definir qué columnas se suman (montos), dejando fuera las de identificación.
        columnas_suma = self._get_sum_columns(df)

        # Sumar por clave: total de cada columna para cada grupo de duplicados.
        progress_callback(50, "Calculando sumas...")
        try:
            df_suma = df_duplicados.groupby(dup_col)[columnas_suma].sum().reset_index()
        except Exception as e:
            self.logger.error(f"Error al agrupar duplicados: {str(e)}")
            raise ProcessorError(f"Error al procesar duplicados: {str(e)}")

        # Volcar la suma a TODAS las filas de cada clave (luego se dejará solo una).
        progress_callback(60, "Actualizando registros...")
        for _, row in df_suma.iterrows():
            mask = df[dup_col] == row[dup_col]
            df.loc[mask, columnas_suma] = row[columnas_suma].values

        # Quedarse con la primera fila de cada clave; el resto ya no aporta (suma repetida).
        progress_callback(70, "Eliminando duplicados adicionales...")
        num_antes = len(df)
        df = df.drop_duplicates(subset=[dup_col], keep='first')
        num_despues = len(df)

        eliminados = num_antes - num_despues
        self.logger.info(f"Se eliminaron {eliminados} filas duplicadas")

        # Devolver ordenado por la clave para una salida estable.
        return df.sort_values(by=dup_col)
    
    def _get_sum_columns(self, df: pd.DataFrame) -> List[str]:
        """
        Determina las columnas a sumar.

        Por defecto usa columnas desde la posición 16 (índice 0).
        Si hay menos columnas, usa todas las numéricas.

        Nota: el corte en la columna 17 asume el layout habitual del archivo, donde las
        primeras 16 columnas son datos identificatorios y de ahí en adelante van los montos.
        """
        # Caso normal: los montos van desde la columna 17 en adelante (índice 16).
        if len(df.columns) >= 17:
            columnas_suma = list(df.columns[16:])
        else:
            # Layout inesperado (archivo más corto): caer a todas las columnas numéricas.
            self.logger.warning(
                f"El archivo tiene {len(df.columns)} columnas (menos de 17). "
                "Se usarán todas las columnas numéricas."
            )
            columnas_suma = df.select_dtypes(include=['number']).columns.tolist()

            # La clave de duplicados no debe sumarse aunque sea numérica: se excluye.
            if self.duplicate_column in columnas_suma:
                columnas_suma.remove(self.duplicate_column)
        
        return columnas_suma
