"""
Configuración centralizada de columnas para procesamiento de remuneraciones.
Esto evita duplicación y facilita el mantenimiento.

QUÉ HACE ESTE MÓDULO:
    - Define los NOMBRES EXACTOS de las columnas tal como aparecen en las
      planillas Excel de liquidaciones y en el archivo web del MINEDUC. Estos
      nombres deben coincidir carácter por carácter con la planilla; si el
      colegio/DAEM cambia un título de columna, hay que actualizarlo aquí.
    - Clasifica las columnas en dos grandes grupos:
        * SPECIAL_SALARY_COLUMNS: montos que siempre están presentes y que se
          prorratean; en contratos PIE el monto se divide en las partes PIE/SN.
        * SALARY_BENEFIT_COLUMNS: haberes/descuentos opcionales (pueden faltar).
    - Provee utilidades para normalizar/formatear RUT chileno, detectar el mes y
      el tipo de archivo a partir del nombre, y limpiar encabezados de columnas.

POR QUÉ IMPORTA:
    Es la única fuente de verdad de los nombres de columna. Centralizar aquí
    evita que cada procesador tenga su propia lista y se desincronicen.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, FrozenSet, Optional

import pandas as pd

@dataclass(frozen=True)
class ColumnConfig:
    """Configuración inmutable de columnas para procesadores.

    Se usa 'frozen=True' para que estos valores no puedan modificarse en tiempo
    de ejecución: son constantes de negocio (nombres de columna, límite de horas)
    que no deben cambiar durante un procesamiento.
    """

    # Columnas requeridas por hoja
    REQUIRED_HORAS: FrozenSet[str] = frozenset({'Rut', 'Nombre'})
    REQUIRED_TOTAL: FrozenSet[str] = frozenset({'Rut'})
    
    # Columnas de horas específicas por tipo de subvención.
    # 'SN' = horas normales/subvención general; 'Jornada' se usa para EIB.
    SEP_HOURS_COL: str = 'SEP'
    PIE_HOURS_COL: str = 'PIE'
    SN_HOURS_COL: str = 'SN'
    EIB_HOURS_COL: str = 'Jornada'

    # Límite máximo de horas permitidas por contrato docente (jornada completa).
    # Sobre este valor se marca al docente para revisión (excede_horas).
    MAX_HOURS: int = 44


# Columnas especiales que requieren cálculo diferenciado.
# Son montos que SIEMPRE están presentes en la liquidación y que se prorratean.
# En contratos PIE, el monto se reparte entre las partes PIE y SN (normal).
# IMPORTANTE: cada string debe coincidir EXACTAMENTE con el título en la planilla.
SPECIAL_SALARY_COLUMNS: List[str] = [
    'SUELDO BASE',
    'RBMN (SUELDO BASE)',
    'ASIGNACION EXPERIENCIA',
    'Antic SEG.INV.SOB.',
    'SEG.CESANTIA EMP.',
    'MUTUAL',
    'Aporte adicional empleador',
    'Aporte Adicional AFP'
]

# Columnas de salarios y beneficios para prorrateo.
# A diferencia de SPECIAL_SALARY_COLUMNS, estas son OPCIONALES: pueden o no
# aparecer en una liquidación dada (dependen del mes, del docente y del colegio).
# El procesador solo usa las que existan realmente en el DataFrame.
SALARY_BENEFIT_COLUMNS: List[str] = [
    # Asignaciones principales
    'ASIGNACION RESPONSABILIDAD', 'CONDICION DIFICIL', 'COMPLEMENTO DE ZONA',
    '(BRP) Asig. Titulo y M', 'PROF. ENCARGADO LEY.', 'HORAS EXTRAS RETROACT.',
    'ASIGNACION ESPECIAL', 'ASIG.RESP. UTP', 'HORAS EXTRAS DEM', 'RETRO.FAMILIAR',
    'BONO VACACIONES', 'PAGO RETROACTIVO', 'LEY 19464/96', 'BRP RETROAC/REEMPL.',
    'BONIFICACION ESPECIAL', 'INCENTIVO (P.I.E)', 'EXCELENCIA ACADEMICA',
    'ASIG. TITULO ESPECIAL', 'DEVOLUCION DESCUENTO', 'RETROBONO INCENTIVO',
    'ASIG. FAMILIAR CORR.', 'BONO CUMPLIMIENTO METAS', 'ASIG.DIRECTOR.LEY 20501',
    'RESP. INSPECTOR GENERAL', 'COND.DIFICIL.ASIST.EDUCACIÓN',
    'ASIGNACION LEY 20.501/2011 DIR', 'RETROACTIVO BIENIOS',
    'RETROACTIVO PROFESOR ENCARGADO', 'ASIG.RESPONS. 6HRS',
    'RETROCT.ALS.PRIORIT.ASIST.EDUC', 'ART.59 LEY 20.883BONO ASISTEDU',
    'RETROACT.ASIGN.RESPOS.DIRECTIV', 'ALS PRIORIT.ASIST.EDUC.AÑO2022',
    'LEY 21.405 ART.44  ASISTE.EDUC', 'ASIGNACION INDUCCION CPEIP',
    'AJUSTE BONO LEY 20.883ART59  A', 'RESTITUCION LICEN.MEDICA',
    'ART.42 LEY 21.526 ASIST.EDUC', 'ALUMNOS. PRIORITARIOS ASIS. DE',
    'ASIG.Por Tramo de Desarrollo P', 'Rec. Doc. Establ. Als Priorita',
    'Planilla Suplementaria', 'ART.5°TRANS. LEY20.903',
    
    # Totales y descuentos
    'TOTAL HABERES', 'IMPOSICIONES antic', 'SALUD',
    'Imposicion Voluntaria', 'MONTO IMPONIBLE', 'MONTO IMP.DESAHUCIO',
    'IMPUESTO UNICO', 'MONTO TRIBUTABLE', 'DIA NO TRABAJADO',
    'RET. JUDICIAL', 'A.P.V', 'SEGURO DE CESANTIA',
    'HDI CIA. DE SEGUROS', 'HDI CONDUCTORES', 'AGRUPACION CODOCENTE',
    'TEMUCOOP (COOPERATIVA DE AHO', 'COOPAHOCRED.KUMEMOGEN LTDA',
    'CRED. COOPEUCH BIENESTAR', 'PRESTAMO/ACCIONES- COOPEUCH',
    'MUTUAL DE SEGUROS DE CHILE', '1% PROFESORES DE RELIGION',
    'CUOTA BIENESTAR 1%', 'CHILENA CONSOLIDADA - SEGURO', 'ATRASOS',
    'VIDA SECURITY - SEGUROS DE V', 'BIENESTAR CUOTA INCORP. CUO',
    'REINTEGRO', 'CAJA LOS ANDES - SEGUROS Y P', 'CAJA LOS ANDES - AHORRO',
    'COLEGIO PROFESORES 1%', 'APORTE SEG. INV. SOB.', 'REINTEGRO BIENIO',
    '1% ASOC.AGFAE', 'AHORRO AFP', 'RETENCION POR LICEN. MEDICA',
    'BONO DOCENTE', 'SEGURO DE CESANTIA', 'SEGURO FALP',
    'COLEGIO PROFESORES 1% HABER', 'Ajuste IMPOSICIONES'
]

# Columnas del archivo web_sostenedor (MINEDUC).
# Mapea una CLAVE INTERNA corta (usada en el código) al NOMBRE EXACTO de la
# columna tal como viene en el Excel descargado del portal del sostenedor.
# Ejemplo: se usa la clave 'rbd' internamente, pero en el archivo el encabezado
# es 'Rbd (Establecimiento)'. Si el MINEDUC renombra una columna, se actualiza
# aquí el valor (lado derecho), no la clave.
WEB_SOSTENEDOR_COLUMNS: Dict[str, str] = {
    'rbd': 'Rbd (Establecimiento)',
    'rut': 'RUT (Docente)',
    'nombres': 'Nombres (Docente)',
    'apellido1': 'Primer Apellido (Docente)',
    'apellido2': 'Segundo Apellido (Docente)',
    'bienios': 'Bienios',
    'tramo': 'Tramo',
    'carrera': 'Carrera docente',
    'horas_contrato': 'Horas de contrato',
    'dias_trabajados': 'Total días trabajados o descontados',
    'subvencion_titulo': 'Subvención título',
    'transferencia_titulo': 'Transferencia directa título',
    'subvencion_mencion': 'Subvención mención',
    'transferencia_mencion': 'Transferencia directa mención',
    'total_subv_reconocimiento': 'Total subvención reconocimiento profesional',
    'total_transf_reconocimiento': 'Total transferencia directa reconocimiento',
    'total_reconocimiento': 'Total reconocimiento profesional',
    'subvencion_tramo': 'Subvención tramo',
    'transferencia_tramo': 'Transferencia directa tramo',
    'total_tramo': 'Total tramo',
    'asig_prioritarios': 'Asignación directa alumnos prioritarios',
    'total_subvenciones': 'Total subvenciones',
    'total_transferencia': 'Total transferencia directa',
    'porcentaje_prioritarios': 'Porcentaje Alumnos Prioritarios',
    'sep': 'SEP',
    'pie': 'PIE',
    'general': 'GENERAL',
    # Columnas de metadata
    'tipo_pago': 'Tipo de pago',
    'periodo': 'Período',
    'mes': 'Mes',
    'anio': 'Año',
    # Columnas informativas adicionales
    'derecho_tramo': 'Derecho a pago asignación de tramo',
    'derecho_prioritario': 'Derecho a prioritario',
    'desempeno_dificil_total': 'Total Asignación por Desem Dificil',
    'desempeno_dificil_pagar': 'A pagar docente desempeño difícil',
}

# Columnas MINEDUC que afectan cálculo (si faltan → montos $0)
WEB_CRITICAL_COLUMNS = {
    'total_reconocimiento', 'total_tramo',
    'subv_reconocimiento', 'transf_reconocimiento',
    'subv_tramo', 'transf_tramo', 'asig_prioritarios',
}

# Columnas MINEDUC informativas (si faltan → no afectan cálculo)
WEB_INFO_COLUMNS = {
    'nombres', 'apellido1', 'apellido2', 'tipo_pago', 'tramo',
}

# Nombres amigables para mostrar al usuario
WEB_FRIENDLY_NAMES = {
    'total_reconocimiento': 'Total reconocimiento profesional',
    'total_tramo': 'Total tramo',
    'subv_reconocimiento': 'Subvención reconocimiento (DAEM)',
    'transf_reconocimiento': 'Transferencia reconocimiento (CPEIP)',
    'subv_tramo': 'Subvención tramo (DAEM)',
    'transf_tramo': 'Transferencia tramo (CPEIP)',
    'asig_prioritarios': 'Asignación alumnos prioritarios (CPEIP)',
    'nombres': 'Nombres del docente',
    'apellido1': 'Primer apellido',
    'apellido2': 'Segundo apellido',
    'tipo_pago': 'Tipo de pago',
    'tramo': 'Tramo',
}


def get_available_columns(df, column_list: List[str]) -> List[str]:
    """Retorna solo las columnas que existen en el DataFrame.

    Sirve para filtrar listas como SALARY_BENEFIT_COLUMNS (columnas opcionales)
    y quedarse únicamente con las que efectivamente vienen en la planilla,
    evitando KeyError al acceder a columnas ausentes.
    """
    return [col for col in column_list if col in df.columns]


def normalize_rut(rut) -> str:
    """Normaliza un RUT chileno removiendo puntos y guiones.

    Deja el RUT en un formato canónico (sin puntos, sin guion, sin espacios,
    en mayúscula para el dígito verificador 'K'). Se usa como clave para cruzar
    datos entre archivos (liquidaciones vs. web MINEDUC), donde el mismo RUT
    puede venir escrito de formas distintas ('12.345.678-9', '12345678-9', etc.).
    """
    # pd.isna cubre NaN de pandas; el try/except protege ante tipos raros
    # (listas, objetos) que harían fallar a pd.isna con TypeError/ValueError.
    try:
        if rut is None or pd.isna(rut):
            return ''
    except (TypeError, ValueError):
        if rut is None:
            return ''
    return str(rut).strip().upper().replace('.', '').replace('-', '').replace(' ', '')


def format_rut(rut) -> str:
    """Formatea un RUT normalizado con guión: 12345678-9.

    Inverso "amigable" de normalize_rut: se usa para MOSTRAR el RUT al usuario
    (informes, tablas). Separa el dígito verificador (último carácter) del cuerpo.
    """
    rut_str = normalize_rut(rut)
    # Con menos de 2 caracteres no hay cuerpo + DV que separar; se devuelve tal cual.
    if len(rut_str) < 2:
        return rut_str
    return f"{rut_str[:-1]}-{rut_str[-1]}"


# ---------------------------------------------------------------------------
# Limpieza de columnas
# ---------------------------------------------------------------------------

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip espacios en nombres de columna, drop columnas vacías y unnamed."""
    df.columns = df.columns.str.strip()
    # Eliminar columnas tipo 'Unnamed: N'
    unnamed = [c for c in df.columns if re.match(r'^Unnamed:\s*\d+', str(c), re.IGNORECASE)]
    if unnamed:
        df = df.drop(columns=unnamed)
    # Eliminar columnas completamente vacías (sin nombre)
    df = df.loc[:, df.columns.astype(bool)]
    return df


# ---------------------------------------------------------------------------
# Clasificación de contratos
# ---------------------------------------------------------------------------

_SEP_KEYWORDS = ['SEP']
_PIE_KEYWORDS = ['PIE']
_EIB_KEYWORDS = ['EIB']


def classify_contract(tipocontrato: str) -> str:
    """Clasifica un tipo de contrato en SEP/PIE/EIB/NORMAL.

    Busca palabras clave dentro del texto del tipo de contrato. El orden de
    verificación importa: primero SEP, luego PIE, luego EIB; si no matchea
    ninguna, se asume 'NORMAL' (subvención general).
    """
    tc = str(tipocontrato).upper().strip()
    if any(k in tc for k in _SEP_KEYWORDS):
        return 'SEP'
    if any(k in tc for k in _PIE_KEYWORDS):
        return 'PIE'
    if any(k in tc for k in _EIB_KEYWORDS):
        return 'EIB'
    return 'NORMAL'


# ---------------------------------------------------------------------------
# Meses y periodos
# ---------------------------------------------------------------------------

# Abreviaturas de mes (3 letras) -> número de mes con cero a la izquierda.
MESES_MAP: Dict[str, str] = {
    'ene': '01', 'feb': '02', 'mar': '03', 'abr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'ago': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dic': '12',
}

# Nombres completos de mes -> número de mes con cero a la izquierda.
MESES_FULL_MAP: Dict[str, str] = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12',
}

# Inverso: número → nombre
MESES_NUM_TO_NAME: Dict[str, str] = {v: k.capitalize() for k, v in MESES_FULL_MAP.items()}


def parse_periodo(periodo: str) -> Optional[str]:
    """
    Convierte un periodo como 'ene-25' a formato 'YYYY-MM'.

    Se aceptan dos formatos de entrada: 'mmm-YY' (abreviatura de mes + año de
    dos dígitos) y 'YYYY-MM' (ya normalizado, que se devuelve tal cual). El año
    de dos dígitos se interpreta como 20YY.

    Retorna None si no puede parsear.
    """
    periodo = str(periodo).strip().lower()
    # Formato 'mmm-YY' (ej: 'ene-25')
    m = re.match(r'^([a-z]{3})-(\d{2})$', periodo)
    if m:
        mes_str, anio_str = m.group(1), m.group(2)
        mes_num = MESES_MAP.get(mes_str)
        if mes_num:
            anio = 2000 + int(anio_str)
            return f"{anio}-{mes_num}"
    # Formato 'YYYY-MM' (ya normalizado)
    m = re.match(r'^(\d{4})-(\d{2})$', periodo)
    if m:
        return periodo
    return None


def normalize_month_value(value) -> Optional[str]:
    """Normaliza un valor de mes (texto o número) a formato '01'-'12'.

    Acepta: 'Enero', 'ENERO', 'enero', 'ene', 1, '1', '01', etc.
    Retorna: '01'-'12' o None si no reconoce.
    """
    s = str(value).strip().lower()
    if not s or s == 'nan':
        return None
    # Intentar como nombre completo
    if s in MESES_FULL_MAP:
        return MESES_FULL_MAP[s]
    # Intentar como abreviatura
    if s in MESES_MAP:
        return MESES_MAP[s]
    # Intentar como número
    try:
        n = int(float(s))
        if 1 <= n <= 12:
            return f"{n:02d}"
    except (ValueError, OverflowError):
        pass
    return None


def detect_month_from_filename(filename: str) -> Optional[str]:
    """
    Detecta el mes (01-12) a partir del nombre de archivo.

    Orden de prioridad:
      1. Número al INICIO del nombre (convención 'N TIPO.xlsx', ej. '1 SEP.xlsx'
         → enero, '6 SNPIE.xlsx' → junio). Tiene prioridad porque en este dominio
         'SEP'/'PIE'/'SN' del nombre son TIPOS de subvención, no meses.
      2. Nombre completo del mes ('enero', 'febrero', …).
      3. Abreviatura ('ene', 'feb', …) — EXCEPTO 'sep', que aquí significa la
         subvención SEP y no septiembre (para septiembre usar el nombre completo).

    Retorna None si no detecta mes.
    """
    name = str(filename).lower()
    # 1) Número inicial como mes. Solo 1-2 dígitos y en rango 1-12; el (?!\d)
    #    evita capturar un año ('2026 SEP.xlsx' NO es mes 20/2). Si hay un número
    #    inicial fuera de rango, se ignora y se sigue con nombre/abreviatura.
    m = re.match(r'^\s*(\d{1,2})(?!\d)', name)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            return f"{n:02d}"
    # 2) Nombres completos (para evitar falsos positivos con abreviados).
    for mes_name, mes_num in MESES_FULL_MAP.items():
        if mes_name in name:
            return mes_num
    # 3) Abreviados (lookbehind/lookahead para no matchear 'sostenedor' → 'ene').
    #    Se omite 'sep' porque colisiona con el tipo de subvención SEP.
    for mes_abbr, mes_num in MESES_MAP.items():
        if mes_abbr == 'sep':
            continue
        if re.search(r'(?<![a-záéíóúñ])' + mes_abbr + r'(?![a-záéíóúñ])', name):
            return mes_num
    return None


def detect_year_from_filename(filename: str) -> Optional[int]:
    """Detecta año (4 dígitos entre 2015-2030) del nombre de archivo."""
    m = re.search(r'(20[12]\d)', str(filename))
    return int(m.group(1)) if m else None


def detect_file_type(filename: str) -> Optional[str]:
    """
    Detecta el tipo de archivo por su nombre.

    Retorna: 'web', 'sep', 'pie', 'eib', o None.
    Nota: 'septiembre' no se confunde con 'sep' gracias a la verificación
    de límite de palabra.
    """
    name = str(filename).lower()
    # Eliminar nombres de meses completos para evitar falsos positivos
    # (ej: 'septiembre' no debe matchear como tipo 'sep')
    clean = name
    for mes in MESES_FULL_MAP:
        clean = clean.replace(mes, '')
    if clean.startswith('web'):
        return 'web'
    # 'sep' / 'sn' como TOKEN (tolera '1 SEP.xlsx', 'sep_enero', 'sep.xlsx'…).
    # El lookbehind/lookahead evita falsos positivos: 'separata' no es SEP y
    # 'snpie' no matchea 'sn' (queda para PIE por contener 'pie').
    if re.search(r'(?<![a-z])sep(?![a-z])', clean):
        return 'sep'
    if re.search(r'(?<![a-z])sn(?![a-z])', clean) or 'pie' in clean or 'normal' in clean:
        return 'pie'
    if 'eib' in clean:
        return 'eib'
    return None
