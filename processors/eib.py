"""
Procesador para remuneraciones EIB (Educación Intercultural Bilingüe).

Los docentes EIB tienen BRP=$0 (no aparecen en web sostenedor).
El archivo EIB tiene 'Hoja1' con datos salariales y columna 'Jornada' con horas.
Es 100% EIB: ratio=1.0, no requiere prorrateo entre subvenciones.
"""

from pathlib import Path
import pandas as pd

from processors.base import BaseProcessor, ProgressCallback
from config.columns import (
    SALARY_BENEFIT_COLUMNS,
    SPECIAL_SALARY_COLUMNS,
)


class EIBProcessor(BaseProcessor):
    """
    Procesador especializado para remuneraciones EIB.

    A diferencia de SEP/PIE, EIB no tiene hojas HORAS+TOTAL separadas.
    Usa una sola hoja ('Hoja1') con columna 'Jornada' para las horas.
    Todo es 100% EIB (ratio=1.0).
    """

    def process_file(
        self,
        input_path: Path,
        output_path: Path,
        progress_callback: ProgressCallback,
    ) -> None:
        """
        Procesa archivo de remuneraciones EIB.

        Flujo (más simple que SEP porque no hay merge de hojas): cargar la única
        hoja -> normalizar -> prorratear -> validar -> guardar, reportando avance
        por progress_callback. Las excepciones se loguean y se re-lanzan.
        """
        try:
            progress_callback(0, "Iniciando proceso EIB...")

            # Cargar datos: EIB usa una sola hoja (no HORAS+TOTAL como SEP/PIE).
            progress_callback(5, "Cargando datos...")
            self.validate_file(input_path)
            df = self._load_eib_sheet(input_path)

            progress_callback(20, "Normalizando datos...")

            # Normalizar columna Rut (EIB puede usar 'rut' minúscula) para que
            # el ordenamiento y demás lógica encuentren siempre 'Rut'.
            if 'rut' in df.columns and 'Rut' not in df.columns:
                df = df.rename(columns={'rut': 'Rut'})

            # Validar columna de horas: sin la jornada no hay prorrateo posible,
            # por eso su ausencia es un error fatal (se listan las columnas
            # disponibles para ayudar a diagnosticar el archivo).
            hours_col = self.config.EIB_HOURS_COL
            if hours_col not in df.columns:
                raise ValueError(
                    f"No se encontró la columna '{hours_col}' en el archivo EIB. "
                    f"Columnas disponibles: {list(df.columns)}"
                )

            # Asegurar valores numéricos en la columna de horas: texto o vacíos
            # se convierten a 0 (coerce -> NaN -> 0) para evitar errores de tipo.
            df[hours_col] = pd.to_numeric(df[hours_col], errors='coerce').fillna(0)

            # Clave de EIB: es 100% EIB, así que el total de horas del docente es
            # su propia jornada. El ratio del prorrateo es 1.0 (jornada/jornada),
            # de modo que cada monto '_EIB' termina siendo el monto completo.
            df['TOTAL HORAS POR DOCENTE'] = df[hours_col]

            progress_callback(40, "Calculando salarios proporcionales...")

            # Prorratear (aquí, con ratio 1.0, equivale a copiar el monto) tanto
            # las columnas especiales como los beneficios opcionales; genera las
            # columnas '<col>_EIB' y alerta por especiales faltantes.
            all_salary_columns = SPECIAL_SALARY_COLUMNS + SALARY_BENEFIT_COLUMNS
            df = self.prorate_columns(
                df,
                columns=all_salary_columns,
                hours_column=hours_col,
                total_hours_column='TOTAL HORAS POR DOCENTE',
                output_suffix='_EIB',
            )

            progress_callback(70, "Validando horas...")
            df = self.validate_hours(df)

            # Ordenar el resultado para revisión (por Rut y Nombre si existen).
            if 'Rut' in df.columns and 'Nombre' in df.columns:
                df = df.sort_values(['Rut', 'Nombre'])
            elif 'Rut' in df.columns:
                df = df.sort_values('Rut')

            progress_callback(90, "Guardando resultados...")
            self.safe_save(df, output_path)

            progress_callback(100, "Proceso EIB completado!")

        except Exception as e:
            self.logger.error(f"Error en proceso EIB: {str(e)}", exc_info=True)
            raise

    def _load_eib_sheet(self, file_path: Path) -> pd.DataFrame:
        """
        Carga la hoja del archivo EIB (CSV o Excel).

        Para Excel se intenta primero la hoja convencional 'Hoja1'; si no existe,
        se cae de forma tolerante a la primera hoja del libro, sea cual sea su
        nombre (los archivos EIB no siempre respetan la convención).
        """
        # Los archivos CSV tienen una sola tabla: se cargan directamente.
        if self.is_csv(file_path):
            return self.load_datafile(file_path)
        try:
            return self.load_excel_with_retry(file_path, 'Hoja1')
        except (ValueError, KeyError):
            # 'Hoja1' no existe: usar la primera hoja disponible como respaldo.
            self.logger.info("Hoja 'Hoja1' no encontrada, usando primera hoja")
            with pd.ExcelFile(str(file_path), engine='openpyxl') as xlsx:
                if not xlsx.sheet_names:
                    raise ValueError("El archivo no contiene hojas")
                first_sheet = xlsx.sheet_names[0]
            return self.load_excel_with_retry(file_path, first_sheet)
