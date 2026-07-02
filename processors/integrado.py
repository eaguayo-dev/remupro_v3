"""
Procesador Integrado - Procesa SEP, PIE y BRP en un solo flujo.

Orquesta los procesadores individuales y genera auditoría completa.

A diferencia del Lote Anual (que corre 12 meses), este procesa UN período: toma los
archivos brutos SEP y PIE, los procesa, distribuye el BRP cruzándolos con el web
sostenedor, y va registrando cada paso en un AuditLog para trazabilidad.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List

import pandas as pd

from processors.base import BaseProcessor, ProgressCallback
from processors.sep import SEPProcessor
from processors.pie import PIEProcessor
from processors.brp import BRPProcessor
from reports.audit_log import AuditLog


class IntegradoProcessor(BaseProcessor):
    """
    Procesador que integra SEP, PIE y BRP en un solo flujo.

    Toma archivos brutos y genera resultado completo con auditoría.
    """

    def __init__(self):
        super().__init__()
        # Sub-procesadores reutilizables para cada etapa del flujo integrado.
        self.sep_processor = SEPProcessor()
        self.pie_processor = PIEProcessor()
        self.brp_processor = BRPProcessor()
        # Bitácora de auditoría: acumula eventos (info/warning/error) de todo el proceso.
        self.audit = AuditLog()

    def process_file(
        self,
        input_path: Path,
        output_path: Path,
        progress_callback: ProgressCallback
    ) -> None:
        """Implementación requerida por BaseProcessor (no usada directamente)."""
        raise NotImplementedError("Usar process_all() en su lugar")

    def process_all(
        self,
        sep_bruto_path: Path,
        pie_bruto_path: Path,
        web_sostenedor_path: Path,
        output_path: Path,
        progress_callback: ProgressCallback,
        keep_intermediates: bool = False,
        month_filter: Optional[str] = None,
        tipo_pago_filter: Optional[list] = None,
    ) -> Tuple[pd.DataFrame, AuditLog]:
        """
        Procesa todos los archivos en un solo flujo.

        Args:
            sep_bruto_path: Ruta al archivo SEP bruto
            pie_bruto_path: Ruta al archivo PIE/Normal bruto
            web_sostenedor_path: Ruta al archivo web_sostenedor
            output_path: Ruta para guardar el resultado
            progress_callback: Función para reportar progreso
            keep_intermediates: Si True, no elimina archivos intermedios

        Returns:
            Tupla (DataFrame resultado, AuditLog)
        """
        self.audit.start()
        # Lista de archivos intermedios (SEP/PIE procesados) que hay que limpiar al final.
        _temp_files_to_cleanup = []
        self._intermediate_paths: List[Path] = []

        # El flujo avanza en etapas y cada tramo reporta un rango de la barra de progreso.
        try:
            # 1. Validar archivos de entrada (0-5%)
            progress_callback(0, "Validando archivos de entrada...")
            self._validate_inputs(sep_bruto_path, pie_bruto_path, web_sostenedor_path)
            progress_callback(5, "Archivos validados correctamente")

            # 2. Procesar SEP bruto (5-30%). El lambda mapea el progreso interno del
            #    sub-procesador (0-100) al tramo 5-30 de la barra global.
            progress_callback(5, "Procesando archivo SEP...")
            sep_procesado_path = self._process_sep(
                sep_bruto_path,
                lambda v, m: progress_callback(5 + int(v * 0.25), m)
            )
            _temp_files_to_cleanup.append(sep_procesado_path)
            self.audit.info(
                AuditLog.TIPO_ARCHIVO,
                f"Archivo SEP procesado: {sep_bruto_path.name}"
            )

            # 3. Procesar PIE bruto (30-55%)
            progress_callback(30, "Procesando archivo PIE/Normal...")
            pie_procesado_path = self._process_pie(
                pie_bruto_path,
                lambda v, m: progress_callback(30 + int(v * 0.25), m)
            )
            _temp_files_to_cleanup.append(pie_procesado_path)
            self.audit.info(
                AuditLog.TIPO_ARCHIVO,
                f"Archivo PIE procesado: {pie_bruto_path.name}"
            )

            # 4. Distribuir BRP (55-90%): cruza web + SEP + PIE procesados y reparte el
            #    bono por subvención. Aquí se produce el DataFrame de resultado.
            progress_callback(55, "Distribuyendo BRP...")
            df_result = self._process_brp(
                web_sostenedor_path,
                sep_procesado_path,
                pie_procesado_path,
                output_path,
                lambda v, m: progress_callback(55 + int(v * 0.35), m),
                month_filter=month_filter,
                tipo_pago_filter=tipo_pago_filter,
            )

            # 5. Identificar docentes EIB (90-95%)
            progress_callback(90, "Identificando docentes EIB...")
            self._identify_eib_teachers(df_result)

            # 6. Detectar valores inusuales (95-98%)
            progress_callback(95, "Analizando valores inusuales...")
            self._detect_unusual_values(df_result)

            # 7. Consolidar auditoría (98-100%)
            progress_callback(98, "Consolidando auditoría...")
            self._consolidate_audit()

            self.audit.end()
            progress_callback(100, "Procesamiento integrado completado")

            return df_result, self.audit

        except Exception as e:
            # Cualquier fallo se deja registrado en la auditoría antes de propagarlo.
            self.audit.error(
                AuditLog.TIPO_PROCESO,
                f"Error en procesamiento integrado: {str(e)}"
            )
            self.audit.end()
            raise
        finally:
            # keep_intermediates=True: conservar los SEP/PIE procesados (p. ej. para
            # depurar o reutilizarlos) exponiéndolos vía get_intermediate_paths().
            if keep_intermediates:
                self._intermediate_paths = list(_temp_files_to_cleanup)
            else:
                # Por defecto se borran: son temporales con datos sensibles de sueldos.
                for tmp_path in _temp_files_to_cleanup:
                    try:
                        if tmp_path and tmp_path.exists():
                            os.unlink(tmp_path)
                    except OSError:
                        pass

    def _validate_inputs(
        self,
        sep_path: Path,
        pie_path: Path,
        web_path: Path
    ) -> None:
        """Valida los archivos de entrada; corta el proceso si alguno es inválido."""
        # Se valida cada archivo requerido y se deja constancia en la auditoría.
        for path, nombre in [(sep_path, 'SEP'), (pie_path, 'PIE'), (web_path, 'MINEDUC')]:
            try:
                self.validate_file(path)
                self.audit.info(
                    AuditLog.TIPO_VALIDACION,
                    f"Archivo {nombre} válido: {path.name}"
                )
            except Exception as e:
                self.audit.error(
                    AuditLog.TIPO_VALIDACION,
                    f"Error validando archivo {nombre}: {str(e)}",
                    archivo=str(path)
                )
                raise

    def _process_sep(
        self,
        input_path: Path,
        progress_callback: ProgressCallback
    ) -> Path:
        """Procesa el SEP bruto y devuelve la ruta del archivo procesado (temporal)."""
        # Archivo temporal de salida; delete=False para poder leerlo en etapas siguientes.
        tmp = tempfile.NamedTemporaryFile(suffix='_sep_procesado.xlsx', delete=False)
        output_path = Path(tmp.name)
        tmp.close()

        try:
            self.sep_processor.process_file(input_path, output_path, progress_callback)
            return output_path
        except Exception as e:
            self.audit.error(
                AuditLog.TIPO_PROCESO,
                f"Error procesando SEP: {str(e)}"
            )
            raise

    def _process_pie(
        self,
        input_path: Path,
        progress_callback: ProgressCallback
    ) -> Path:
        """Procesa el PIE bruto y devuelve la ruta del archivo procesado (temporal)."""
        # Mismo patrón que _process_sep: temporal de salida que se reutiliza más adelante.
        tmp = tempfile.NamedTemporaryFile(suffix='_pie_procesado.xlsx', delete=False)
        output_path = Path(tmp.name)
        tmp.close()

        try:
            self.pie_processor.process_file(input_path, output_path, progress_callback)
            return output_path
        except Exception as e:
            self.audit.error(
                AuditLog.TIPO_PROCESO,
                f"Error procesando PIE: {str(e)}"
            )
            raise

    def _process_brp(
        self,
        web_path: Path,
        sep_path: Path,
        pie_path: Path,
        output_path: Path,
        progress_callback: ProgressCallback,
        month_filter: Optional[str] = None,
        tipo_pago_filter: Optional[list] = None,
    ) -> pd.DataFrame:
        """Procesa distribución BRP."""
        try:
            self.brp_processor.process_file(
                web_sostenedor_path=web_path,
                sep_procesado_path=sep_path,
                pie_procesado_path=pie_path,
                output_path=output_path,
                progress_callback=progress_callback,
                month_filter=month_filter,
                tipo_pago_filter=tipo_pago_filter,
            )

            # Leer la hoja con la distribución del BRP calculada por el sub-procesador.
            df = pd.read_excel(output_path, sheet_name='BRP_DISTRIBUIDO', engine='openpyxl')

            # Registrar el total distribuido en la auditoría.
            brp_total = df['BRP_TOTAL'].sum() if 'BRP_TOTAL' in df.columns else 0
            self.audit.info(
                AuditLog.TIPO_PROCESO,
                f"BRP distribuido exitosamente. Total: ${brp_total:,.0f}",
                brp_total=brp_total
            )

            # Trasladar a la auditoría los casos que el BRP marcó para revisión manual
            # (docentes que superan 44 hrs o que no tienen liquidación).
            if self.brp_processor.docentes_revisar:
                for caso in self.brp_processor.docentes_revisar:
                    if caso.get('MOTIVO') == 'EXCEDE 44 HORAS':
                        self.audit.warning(
                            AuditLog.TIPO_EXCEDE_HORAS,
                            f"Docente excede 44 horas: {caso.get('NOMBRE', '')} "
                            f"({caso.get('RUT', '')}) - {caso.get('HORAS_TOTAL', 0)} hrs",
                            rut=caso.get('RUT'),
                            horas=caso.get('HORAS_TOTAL')
                        )
                    elif caso.get('MOTIVO') == 'SIN LIQUIDACIÓN':
                        self.audit.warning(
                            AuditLog.TIPO_SIN_LIQUIDACION,
                            f"Docente sin liquidación: {caso.get('NOMBRE', '')} "
                            f"({caso.get('RUT', '')})",
                            rut=caso.get('RUT')
                        )

            # Propagar al audit log las alertas de columnas del BRP (p. ej. columnas de
            # salario faltantes): nivel 'error' → warning; el resto → info.
            for alert in self.brp_processor.get_column_alerts():
                if alert['nivel'] == 'error':
                    self.audit.warning(AuditLog.TIPO_COLUMNA_FALTANTE, alert['mensaje'])
                else:
                    self.audit.info(AuditLog.TIPO_COLUMNA_FALTANTE, alert['mensaje'])

            return df

        except Exception as e:
            self.audit.error(
                AuditLog.TIPO_PROCESO,
                f"Error distribuyendo BRP: {str(e)}"
            )
            raise

    def _identify_eib_teachers(self, df: pd.DataFrame) -> None:
        """
        Identifica docentes con BRP = 0 (posibles EIB).

        Un BRP en $0 suele indicar un docente EIB (Educación Intercultural Bilingüe),
        que se paga por otra vía; se listan en la auditoría para revisión posterior.
        """
        if 'BRP_TOTAL' not in df.columns:
            return

        # BRP_TOTAL == 0 → candidato a EIB.
        df_eib = df[df['BRP_TOTAL'] == 0]

        if df_eib.empty:
            self.audit.info(
                AuditLog.TIPO_DOCENTE_EIB,
                "No se detectaron docentes con BRP $0"
            )
            return

        self.audit.warning(
            AuditLog.TIPO_DOCENTE_EIB,
            f"Se identificaron {len(df_eib)} docentes con BRP $0 (posibles EIB)",
            cantidad=len(df_eib)
        )

        # Ubicar la columna de RUT: preferir 'RUT_NORM'; si no, la primera que lo tenga.
        rut_col = 'RUT_NORM' if 'RUT_NORM' in df.columns else None
        if not rut_col:
            for col in df.columns:
                if 'rut' in col.lower():
                    rut_col = col
                    break

        # Ubicar la columna de nombre de forma flexible.
        nombre_col = None
        for col in df.columns:
            if 'nombre' in col.lower():
                nombre_col = col
                break

        # Registrar cada docente EIB
        for _, row in df_eib.iterrows():
            rut = row.get(rut_col, '') if rut_col else ''
            nombre = row.get(nombre_col, '') if nombre_col else ''

            self.audit.info(
                AuditLog.TIPO_DOCENTE_EIB,
                f"Docente EIB: {nombre} ({rut})",
                rut=rut,
                nombre=nombre
            )

    def _detect_unusual_values(self, df: pd.DataFrame) -> None:
        """Detecta valores inusuales (negativos y outliers) y los reporta a la auditoría."""
        # Montos negativos: no deberían existir en un BRP; se avisa por columna.
        for col in ['BRP_SEP', 'BRP_PIE', 'BRP_NORMAL', 'BRP_TOTAL']:
            if col in df.columns:
                negativos = df[df[col] < 0]
                if not negativos.empty:
                    self.audit.warning(
                        AuditLog.TIPO_VALOR_INUSUAL,
                        f"Se encontraron {len(negativos)} valores negativos en {col}",
                        columna=col,
                        cantidad=len(negativos)
                    )

        # Outliers: montos anormalmente altos, usando el criterio media + 3 desviaciones
        # estándar (regla estadística habitual para señalar valores atípicos).
        if 'BRP_TOTAL' in df.columns:
            media = df['BRP_TOTAL'].mean()
            std = df['BRP_TOTAL'].std()
            umbral = media + 3 * std

            outliers = df[df['BRP_TOTAL'] > umbral]
            if not outliers.empty:
                self.audit.warning(
                    AuditLog.TIPO_VALOR_INUSUAL,
                    f"Se detectaron {len(outliers)} montos BRP inusualmente altos "
                    f"(>{umbral:,.0f})",
                    umbral=umbral,
                    cantidad=len(outliers)
                )

    def _consolidate_audit(self) -> None:
        """Consolida y resume la auditoría."""
        summary = self.audit.get_summary()

        self.audit.info(
            AuditLog.TIPO_PROCESO,
            f"Auditoría: {summary.get('total', 0)} eventos, "
            f"{summary.get('errores', 0)} errores, "
            f"{summary.get('advertencias', 0)} advertencias"
        )

    def get_horas_map(self) -> Dict[str, Dict[str, float]]:
        """
        Obtiene el mapa de horas por docente del procesador BRP.

        Returns:
            Diccionario con horas por tipo de subvención por RUT
        """
        # Expone el mapa de horas del BRP tras procesar, para persistirlo en la BD.
        # getattr con default {} evita fallar si el BRP aún no lo calculó.
        return getattr(self.brp_processor, '_horas_map', {})

    def get_docentes_revisar(self) -> list:
        """Obtiene la lista de docentes a revisar."""
        return self.brp_processor.docentes_revisar

    def get_intermediate_paths(self) -> List[Path]:
        """Retorna paths de archivos intermedios (SEP procesado, PIE procesado)."""
        return getattr(self, '_intermediate_paths', [])
