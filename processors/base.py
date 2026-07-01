"""
Clase base para procesadores de remuneraciones.
Contiene la lógica común de validación, carga y guardado de archivos.
"""

import sys
import time
import logging
from pathlib import Path
from typing import Callable, Optional, Tuple, List
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

from config.columns import (
    ColumnConfig,
    SALARY_BENEFIT_COLUMNS,
    SPECIAL_SALARY_COLUMNS,
    get_available_columns,
    clean_columns,
)


# Tipo para callback de progreso
ProgressCallback = Callable[[int, str], None]


class ProcessorError(Exception):
    """Excepción base para errores de procesamiento."""
    pass


class FileValidationError(ProcessorError):
    """Error en validación de archivo."""
    pass


class ColumnMissingError(ProcessorError):
    """Error cuando faltan columnas requeridas."""
    pass


class BaseProcessor(ABC):
    """
    Clase base abstracta para procesadores de remuneraciones.
    Proporciona métodos comunes y define la interfaz que deben implementar los procesadores.
    """
    
    def __init__(self):
        self.config = ColumnConfig()
        self.logger = logging.getLogger(self.__class__.__name__)
        # Alertas de columnas generadas durante el procesamiento (para la UI).
        self.column_alerts: List[dict] = []

    # ==================== VALIDACIÓN ====================
    
    SUPPORTED_FORMATS = ('.xlsx', '.xls', '.csv')

    def validate_file(self, file_path: Path) -> None:
        """Realiza validaciones básicas del archivo."""
        if not file_path.exists():
            raise FileValidationError(f"Archivo no encontrado: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            raise FileValidationError(
                f"Formato de archivo no válido: {suffix}. "
                "Debe ser .xlsx, .xls o .csv"
            )

        if file_path.stat().st_size == 0:
            raise FileValidationError("El archivo está vacío")
    
    def validate_columns(self, df: pd.DataFrame, required: set, sheet_name: str) -> None:
        """Valida que existan las columnas requeridas en el DataFrame."""
        df_columns = set(df.columns)
        missing = required - df_columns
        
        if missing:
            raise ColumnMissingError(
                f"Hoja '{sheet_name}' - Faltan columnas: {', '.join(sorted(missing))}"
            )

    def get_column_alerts(self) -> List[dict]:
        """Retorna las alertas de columnas generadas durante el procesamiento."""
        return self.column_alerts

    def _record_missing_special_columns(
        self, requested: List[str], available: List[str]
    ) -> None:
        """
        Registra una alerta por cada columna ESPECIAL esperada que no se encontró.

        Solo se avisan las columnas de SPECIAL_SALARY_COLUMNS (haberes/aportes que
        deberían venir siempre). Las de SALARY_BENEFIT_COLUMNS se omiten en silencio
        porque legítimamente no aplican a todos los colegios/docentes y avisarlas
        generaría ruido. Evita duplicados si se llama varias veces en un mismo run.
        """
        available_set = set(available)
        for col in requested:
            if col in available_set or col not in SPECIAL_SALARY_COLUMNS:
                continue
            if any(a.get('columna_key') == col for a in self.column_alerts):
                continue
            self.column_alerts.append({
                'nivel': 'warning',
                'tipo': 'columna_salario_faltante',
                'columna_key': col,
                'columna_nombre': col,
                'mensaje': (
                    f"No se encontró la columna especial '{col}'. "
                    f"No se generará su prorrateo en el resultado."
                ),
            })
            self.logger.warning(f"Columna especial no encontrada: {col}")

    # ==================== CARGA DE DATOS ====================
    
    def load_excel_with_retry(
        self, 
        file_path: Path, 
        sheet_name: str,
        max_retries: int = 3,
        delay: float = 1.0,
        **read_kwargs
    ) -> pd.DataFrame:
        """
        Carga una hoja de Excel con reintentos en caso de error de permisos.
        
        Args:
            file_path: Ruta al archivo Excel
            sheet_name: Nombre de la hoja a cargar
            max_retries: Número máximo de reintentos
            delay: Segundos de espera entre reintentos
            **read_kwargs: Argumentos adicionales para pd.read_excel
        """
        for attempt in range(max_retries):
            try:
                df = pd.read_excel(
                    str(file_path),
                    sheet_name=sheet_name,
                    engine='openpyxl',
                    **read_kwargs
                )
                return clean_columns(df)
            except PermissionError:
                if attempt == max_retries - 1:
                    self._raise_permission_error(file_path, "lectura")
                self.logger.warning(f"Reintento {attempt + 1} para abrir {file_path}")
                time.sleep(delay)
        
        return pd.DataFrame()  # Nunca debería llegar aquí

    def load_datafile(self, file_path: Path, **read_kwargs) -> pd.DataFrame:
        """
        Carga un archivo de datos (CSV o Excel) y retorna un DataFrame limpio.

        Para CSV intenta UTF-8 primero, luego latin-1.
        Para Excel lee la primera hoja.
        """
        suffix = file_path.suffix.lower()
        if suffix == '.csv':
            try:
                df = pd.read_csv(str(file_path), encoding='utf-8', **read_kwargs)
            except UnicodeDecodeError:
                df = pd.read_csv(str(file_path), encoding='latin-1', **read_kwargs)
            return clean_columns(df)
        # Excel
        return self.load_excel_with_retry(file_path, 0, **read_kwargs)

    @staticmethod
    def is_csv(file_path: Path) -> bool:
        """Retorna True si el archivo es CSV."""
        return file_path.suffix.lower() == '.csv'

    def load_sheets(
        self, 
        file_path: Path
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Carga las hojas HORAS y TOTAL de un archivo.
        
        Returns:
            Tupla (df_horas, df_total)
        """
        self.validate_file(file_path)
        
        df_horas = self.load_excel_with_retry(file_path, 'HORAS')
        df_total = self.load_excel_with_retry(file_path, 'TOTAL')
        
        # Normalizar nombre de columna Rut
        if 'rut' in df_total.columns and 'Rut' not in df_total.columns:
            df_total = df_total.rename(columns={'rut': 'Rut'})
        
        return df_horas, df_total
    
    # ==================== GUARDADO ====================
    
    def safe_save(
        self, 
        data: pd.DataFrame, 
        output_path: Path,
        max_retries: int = 3
    ) -> None:
        """
        Guarda el DataFrame a Excel con reintentos en caso de error de permisos.
        """
        for attempt in range(max_retries):
            try:
                data.to_excel(str(output_path), index=False, engine='openpyxl')
                self.logger.info(f"Archivo guardado exitosamente: {output_path}")
                return
            except PermissionError:
                if attempt == max_retries - 1:
                    self._raise_permission_error(output_path, "escritura")
                self.logger.warning(f"Reintento {attempt + 1} para guardar")
                time.sleep(1)
    
    def _raise_permission_error(self, path: Path, operation: str) -> None:
        """Lanza error de permisos con mensaje apropiado según el SO."""
        if sys.platform == 'win32':
            message = (
                f"Error de permisos en {operation}: El archivo podría estar "
                f"abierto en Excel u otro programa.\nCiérrelo e intente nuevamente.\n"
                f"Archivo: {path}"
            )
        else:
            message = f"Error de permisos en {operation}: {path}"
        raise PermissionError(message)
    
    # ==================== CÁLCULOS COMUNES ====================
    
    def calculate_proportional_value(
        self,
        df: pd.DataFrame,
        value_column: str,
        hours_column: str,
        total_hours_column: str,
        output_suffix: str = ''
    ) -> pd.Series:
        """
        Calcula un valor proporcional basado en horas.
        
        Formula: (valor / total_horas) * horas_asignadas
        
        Args:
            df: DataFrame con los datos
            value_column: Columna con el valor a prorratear
            hours_column: Columna con las horas asignadas
            total_hours_column: Columna con el total de horas del docente
            output_suffix: Sufijo para el nombre de la columna resultante
        
        Returns:
            Serie con el valor calculado
        """
        # Deduplicar columnas si existen duplicados (puede pasar tras merge)
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep='first')]

        # Evitar división por cero
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = df[value_column] / df[total_hours_column]
            ratio = ratio.replace([np.inf, -np.inf, np.nan], 0)

        result = (ratio * df[hours_column]).round().fillna(0).astype(int)
        return result
    
    def prorate_columns(
        self,
        df: pd.DataFrame,
        columns: List[str],
        hours_column: str,
        total_hours_column: str,
        output_suffix: str
    ) -> pd.DataFrame:
        """
        Prorratea múltiples columnas según horas.
        
        Args:
            df: DataFrame con los datos
            columns: Lista de columnas a prorratear
            hours_column: Columna con horas asignadas
            total_hours_column: Columna con total de horas
            output_suffix: Sufijo para columnas resultantes
        
        Returns:
            DataFrame con las nuevas columnas agregadas
        """
        available = get_available_columns(df, columns)
        
        for col in available:
            output_col = f'{col}{output_suffix}'
            df[output_col] = self.calculate_proportional_value(
                df, col, hours_column, total_hours_column
            )
        
        missing = set(columns) - set(available)
        if missing:
            self.logger.debug(f"Columnas no encontradas: {missing}")
        self._record_missing_special_columns(columns, available)

        return df
    
    def validate_hours(
        self, 
        df: pd.DataFrame, 
        hours_column: str = 'TOTAL HORAS POR DOCENTE',
        name_column: str = 'Nombre',
        rut_column: str = 'Rut'
    ) -> pd.DataFrame:
        """
        Valida que las horas no excedan el máximo permitido.
        
        Agrega columna HORAS_VALIDAS y registra advertencias.
        """
        max_hours = self.config.MAX_HOURS
        
        df['HORAS_VALIDAS'] = df[hours_column] <= max_hours
        
        problematicos = df[~df['HORAS_VALIDAS']]
        
        if problematicos.empty:
            self.logger.info(f"Todos los docentes tienen {max_hours} horas o menos")
        else:
            self.logger.warning(
                f"{len(problematicos)} docente(s) exceden las {max_hours} horas"
            )
            for _, row in problematicos.iterrows():
                nombre = row.get(name_column, 'N/A')
                rut = str(row.get(rut_column, 'N/A'))
                # Mask RUT in logs to protect PII - show only last 4 chars
                masked_rut = f"***{rut[-4:]}" if len(rut) > 4 else "***"
                horas = row.get(hours_column, 0)
                self.logger.warning(f"  - {nombre} (RUT: {masked_rut}): {horas} horas")
        
        return df
    
    def calculate_total_hours_by_teacher(
        self,
        df: pd.DataFrame,
        hours_columns: List[str],
        group_columns: List[str] = ['Rut', 'Nombre']
    ) -> pd.DataFrame:
        """
        Calcula el total de horas por docente agrupando por RUT/Nombre.
        
        Args:
            df: DataFrame con datos de horas
            hours_columns: Columnas de horas a sumar
            group_columns: Columnas para agrupar
        
        Returns:
            DataFrame con columna TOTAL HORAS POR DOCENTE agregada
        """
        # Calcular total de horas por fila
        df['_TEMP_TOTAL_HORAS'] = df[hours_columns].sum(axis=1)
        
        # Filtrar filas sin horas
        df = df[df['_TEMP_TOTAL_HORAS'] != 0].copy()
        
        # Agrupar y sumar
        horas_agrupadas = df.groupby(group_columns)[hours_columns].sum().reset_index()
        horas_agrupadas['TOTAL HORAS POR DOCENTE'] = horas_agrupadas[hours_columns].sum(axis=1)
        
        # Merge para agregar total al df original
        df = df.merge(
            horas_agrupadas[group_columns + ['TOTAL HORAS POR DOCENTE']],
            on=group_columns,
            how='left',
            suffixes=('', '_SUMA')
        )
        
        # Limpiar columna temporal
        df = df.drop('_TEMP_TOTAL_HORAS', axis=1, errors='ignore')
        
        return df
    
    # ==================== MÉTODO ABSTRACTO ====================
    
    @abstractmethod
    def process_file(
        self, 
        input_path: Path, 
        output_path: Path, 
        progress_callback: ProgressCallback
    ) -> None:
        """
        Método principal de procesamiento. Debe ser implementado por cada procesador.
        
        Args:
            input_path: Ruta al archivo de entrada
            output_path: Ruta donde guardar el resultado
            progress_callback: Función para reportar progreso (valor, mensaje)
        """
        pass
