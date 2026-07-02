"""
Sistema de auditoría para capturar logs durante el procesamiento.
Permite registrar eventos, advertencias y errores de forma estructurada.

Se diferencia del logging tradicional en que los eventos se ACUMULAN EN MEMORIA
(lista de AuditEntry) en lugar de solo escribirse a consola. Así, al terminar el
procesamiento, se pueden filtrar, resumir y volcar al informe Word o a un
DataFrame para que el equipo revise qué pasó (columnas faltantes, valores
inusuales, docentes EIB, etc.).

Niveles: INFO / WARNING / ERROR. Tipos: constantes TIPO_* de la clase AuditLog,
que categorizan el evento (columna_faltante, valor_inusual, docente_eib, ...).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd


@dataclass
class AuditEntry:
    """Entrada individual del log de auditoría (un evento registrado)."""
    timestamp: datetime
    nivel: str  # INFO, WARNING, ERROR
    tipo: str   # columna_faltante, valor_inusual, docente_eib, etc.
    mensaje: str
    # Contexto extra libre del evento (ej: rut, columna, valor). Se aplana en
    # to_dict() junto a los campos fijos para poder llevarlo a un DataFrame.
    datos: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convierte la entrada a diccionario (campos fijos + datos extra)."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'nivel': self.nivel,
            'tipo': self.tipo,
            'mensaje': self.mensaje,
            # Desempaqueta el contexto extra como columnas adicionales.
            **self.datos
        }


class AuditLog:
    """
    Sistema de captura de logs de auditoría.

    Permite registrar eventos durante el procesamiento para
    incluirlos en informes y facilitar la revisión.
    """

    # Tipos de eventos predefinidos. Usar estas constantes (en vez de strings
    # sueltos) para poder filtrar de forma fiable con get_by_tipo().
    TIPO_COLUMNA_FALTANTE = 'columna_faltante'   # falta una columna esperada
    TIPO_VALOR_INUSUAL = 'valor_inusual'         # monto/valor fuera de lo normal
    TIPO_DOCENTE_EIB = 'docente_eib'             # docente sin BRP (posible EIB)
    TIPO_EXCEDE_HORAS = 'excede_horas'           # supera el máximo de horas
    TIPO_SIN_LIQUIDACION = 'sin_liquidacion'     # docente sin liquidación asociada
    TIPO_VALIDACION = 'validacion'               # chequeos de validación
    TIPO_PROCESO = 'proceso'                     # hitos del procesamiento
    TIPO_ARCHIVO = 'archivo'                     # eventos de lectura de archivos

    def __init__(self):
        # Lista acumulativa de eventos en orden de registro.
        self.entries: List[AuditEntry] = []
        # Momento de inicio, fijado por start(); se usa para medir la duración.
        self._start_time: Optional[datetime] = None

    def start(self) -> None:
        """Marca el inicio del procesamiento."""
        self._start_time = datetime.now()
        self.info(self.TIPO_PROCESO, "Inicio del procesamiento")

    def end(self) -> None:
        """Marca el fin del procesamiento."""
        if self._start_time:
            duracion = datetime.now() - self._start_time
            self.info(
                self.TIPO_PROCESO,
                f"Fin del procesamiento. Duración: {duracion.total_seconds():.1f}s"
            )

    def log(
        self,
        nivel: str,
        tipo: str,
        mensaje: str,
        **datos
    ) -> AuditEntry:
        """
        Registra una entrada en el log.

        Args:
            nivel: INFO, WARNING o ERROR
            tipo: Tipo de evento (usar constantes TIPO_*)
            mensaje: Descripción del evento
            **datos: Datos adicionales de contexto

        Returns:
            La entrada creada
        """
        # nivel.upper() normaliza para que 'warning'/'WARNING' sean equivalentes.
        # **datos recoge cualquier contexto extra pasado por palabra clave.
        entry = AuditEntry(
            timestamp=datetime.now(),
            nivel=nivel.upper(),
            tipo=tipo,
            mensaje=mensaje,
            datos=datos
        )
        self.entries.append(entry)
        return entry

    def info(self, tipo: str, mensaje: str, **datos) -> AuditEntry:
        """Registra evento informativo."""
        return self.log('INFO', tipo, mensaje, **datos)

    def warning(self, tipo: str, mensaje: str, **datos) -> AuditEntry:
        """Registra advertencia."""
        return self.log('WARNING', tipo, mensaje, **datos)

    def error(self, tipo: str, mensaje: str, **datos) -> AuditEntry:
        """Registra error."""
        return self.log('ERROR', tipo, mensaje, **datos)

    def get_by_tipo(self, tipo: str) -> List[AuditEntry]:
        """Obtiene entradas filtradas por tipo."""
        return [e for e in self.entries if e.tipo == tipo]

    def get_by_nivel(self, nivel: str) -> List[AuditEntry]:
        """Obtiene entradas filtradas por nivel."""
        return [e for e in self.entries if e.nivel == nivel.upper()]

    def get_warnings(self) -> List[AuditEntry]:
        """Obtiene todas las advertencias."""
        return self.get_by_nivel('WARNING')

    def get_errors(self) -> List[AuditEntry]:
        """Obtiene todos los errores."""
        return self.get_by_nivel('ERROR')

    def get_docentes_eib(self) -> List[AuditEntry]:
        """Obtiene entradas de docentes EIB (posibles)."""
        return self.get_by_tipo(self.TIPO_DOCENTE_EIB)

    def get_valores_inusuales(self) -> List[AuditEntry]:
        """Obtiene entradas de valores inusuales."""
        return self.get_by_tipo(self.TIPO_VALOR_INUSUAL)

    def has_errors(self) -> bool:
        """Verifica si hay errores registrados."""
        return len(self.get_errors()) > 0

    def has_warnings(self) -> bool:
        """Verifica si hay advertencias registradas."""
        return len(self.get_warnings()) > 0

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte el log a DataFrame para análisis."""
        if not self.entries:
            return pd.DataFrame(columns=['timestamp', 'nivel', 'tipo', 'mensaje'])

        records = [e.to_dict() for e in self.entries]
        return pd.DataFrame(records)

    def get_summary(self) -> Dict[str, Any]:
        """Genera resumen estadístico del log."""
        df = self.to_dataframe()
        if df.empty:
            return {
                'total': 0,
                'por_nivel': {},
                'por_tipo': {},
                'errores': 0,
                'advertencias': 0
            }

        return {
            'total': len(self.entries),
            'por_nivel': df['nivel'].value_counts().to_dict(),
            'por_tipo': df['tipo'].value_counts().to_dict(),
            'errores': len(self.get_errors()),
            'advertencias': len(self.get_warnings())
        }

    def merge(self, other: 'AuditLog') -> None:
        """Combina otro AuditLog en este.

        Útil cuando cada archivo/hoja se procesa con su propio log y luego se
        consolidan en uno solo. Tras unir, se reordena por timestamp para que la
        secuencia de eventos quede cronológica pese a venir de distintos orígenes.
        """
        self.entries.extend(other.entries)
        # Reordenar por timestamp
        self.entries.sort(key=lambda e: e.timestamp)

    def clear(self) -> None:
        """Limpia todas las entradas."""
        self.entries.clear()
        self._start_time = None

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)
