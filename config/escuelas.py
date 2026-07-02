"""
Utilidades para matching de escuelas/establecimientos por nombre y RBD.

Carga config/escuelas.json y provee funciones para resolver
nombres de ubicación a establecimientos conocidos.

PROBLEMA QUE RESUELVE:
    En las liquidaciones la "ubicación" del docente viene como texto libre
    ("ESCUELA STA. MARIA RBD 1234-5", "Liceo Gregorio Urrutia G Nº 20", etc.),
    con abreviaturas, sufijos de RBD, números y typos. Este módulo normaliza
    esos nombres y los intenta calzar contra el catálogo oficial de
    establecimientos (escuelas.json), para poder asignar el RBD correcto.

CACHÉ:
    escuelas.json se lee una sola vez y se mantiene en memoria
    (_ESCUELAS_CACHE / _RBD_MAP_CACHE) porque el matching se llama muchas veces
    por procesamiento y releer el archivo cada vez sería innecesariamente lento.

RBD:
    El RBD (Rol Base de Datos) es el código único de cada establecimiento. En el
    JSON viene como 'rbd-dv' (código + dígito verificador, ej. '1234-5'); el mapa
    de get_rbd_map() indexa por el RBD SIN dígito verificador.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ESCUELAS_CACHE: Optional[List[Dict[str, str]]] = None
_RBD_MAP_CACHE: Optional[Dict[str, str]] = None


def load_escuelas() -> List[Dict[str, str]]:
    """Carga config/escuelas.json y cachea en memoria.

    Devuelve la lista de establecimientos (cada uno un dict con al menos
    'establecimiento' y 'rbd-dv'). Si el archivo no existe, cachea y retorna una
    lista vacía en vez de fallar, de modo que el matching simplemente no calce.
    """
    global _ESCUELAS_CACHE
    # Si ya está en caché, se reutiliza (evita releer el JSON en cada llamada).
    if _ESCUELAS_CACHE is not None:
        return _ESCUELAS_CACHE

    json_path = Path(__file__).parent / "escuelas.json"
    if not json_path.exists():
        _ESCUELAS_CACHE = []
        return _ESCUELAS_CACHE

    with open(json_path, "r", encoding="utf-8") as f:
        _ESCUELAS_CACHE = json.load(f)
    return _ESCUELAS_CACHE


def get_rbd_map() -> Dict[str, str]:
    """Retorna mapa rbd (sin DV) -> nombre establecimiento.

    Construye (y cachea) un diccionario para buscar el nombre de un colegio a
    partir de su RBD. Se indexa por el RBD SIN dígito verificador porque en las
    liquidaciones el RBD suele venir sin él.
    """
    global _RBD_MAP_CACHE
    if _RBD_MAP_CACHE is not None:
        return _RBD_MAP_CACHE

    escuelas = load_escuelas()
    _RBD_MAP_CACHE = {}
    for esc in escuelas:
        rbd_dv = esc.get("rbd-dv", "")
        # 'rbd-dv' viene como 'codigo-dv' (ej. '1234-5'); nos quedamos con el código.
        rbd = rbd_dv.split("-")[0] if "-" in rbd_dv else rbd_dv
        _RBD_MAP_CACHE[rbd] = esc.get("establecimiento", "")
    return _RBD_MAP_CACHE


def _normalize_school_name(name: str) -> str:
    """Normaliza nombre: uppercase, strip sufijos RBD/Nº/G Nº.

    Limpieza "suave": mantiene los espacios entre palabras pero elimina el ruido
    que impide comparar nombres (abreviaturas, puntos, y los sufijos numéricos
    tipo 'RBD 1234-5', 'Nº 20', 'G Nº 20', 'F 100'). Es el paso previo a comparar
    la ubicación de la liquidación contra el nombre del catálogo.
    """
    name = str(name).upper().strip()
    # Expandir abreviaciones
    name = re.sub(r'\bSTA\.?\b', 'SANTA', name)
    # Quitar puntos sueltos
    name = name.replace('.', '')
    # Quitar palabras descriptivas que no están en escuelas.json
    name = re.sub(r'\bESPECIAL\b\s*', '', name)
    # Quitar sufijo RBD XXXX-X
    name = re.sub(r'\s*RBD\s*\d+[-]?\d*\s*$', '', name)
    # Quitar sufijo Nº NNN o N° NNN
    name = re.sub(r'\s*(?:Nº|N°|NRO\.?)\s*\d+\s*$', '', name, flags=re.IGNORECASE)
    # Quitar sufijo G Nº NNN
    name = re.sub(r'\s*G\s*(?:Nº|N°)\s*\d+\s*$', '', name, flags=re.IGNORECASE)
    # Quitar sufijo G N°NNN (sin espacio)
    name = re.sub(r'\s*G\s*N°?\s*\d+\s*$', '', name, flags=re.IGNORECASE)
    # Quitar sufijo F NNN
    name = re.sub(r'\s*F\s+\d+\s*$', '', name)
    return name.strip()


def _normalize_for_comparison(name: str) -> str:
    """Quita TODOS los espacios y artículos para manejar typos y variaciones.

    Limpieza "agresiva" sobre _normalize_school_name: además elimina artículos
    (EL/LA/LOS/LAS), letras sueltas y TODOS los espacios. Así 'DAME LA MANO' y
    'DAMELAMANO' colapsan a la misma cadena, tolerando espaciados y typos que
    de otro modo impedirían el match.
    """
    n = _normalize_school_name(name)
    # Quitar artículos (con word boundaries ANTES de quitar espacios)
    n = re.sub(r'\b(EL|LA|LOS|LAS)\b', '', n)
    # Quitar letras sueltas (G, F) que quedan de sufijos tipo "G Nº"
    n = re.sub(r'\b[A-Z]\b', '', n)
    n = n.replace(' ', '')
    return n


def parse_school_name(full_name: str) -> str:
    """Extrae nombre corto de escuela, removiendo prefijos como ESCUELA, LICEO, etc.

    Ejemplos:
        'ESCUELA RUCATRARO ALTO' → 'RUCATRARO ALTO'
        'LICEO GREGORIO URRUTIA' → 'GREGORIO URRUTIA'
        'ESCUELA DAME LA MANO' → 'DAME LA MANO'
    """
    name = str(full_name).strip().upper()
    prefixes = ['ESCUELA BASICA', 'ESCUELA ESPECIAL', 'ESCUELA', 'LICEO', 'COLEGIO', 'JARDIN INFANTIL']
    for prefix in prefixes:
        if name.startswith(prefix + ' '):
            name = name[len(prefix):].strip()
            break
    return name


def match_ubicacion(ubicacion: str) -> Optional[Tuple[str, str]]:
    """
    Matchea una ubicación (de liquidación) a un establecimiento conocido.

    Args:
        ubicacion: Nombre de ubicación del archivo de liquidaciones

    Returns:
        (establecimiento, rbd-dv) o None si no matchea
    """
    if not ubicacion or not str(ubicacion).strip():
        return None

    ubi = str(ubicacion).strip()

    # Caso especial: personal del DAEM/DEM (Departamento de Educación Municipal),
    # que no pertenece a un colegio sino a la administración central. Se le asigna
    # un pseudo-establecimiento fijo ("DAEM", "DEM").
    ubi_upper = ubi.upper()
    if 'EDUCACION' in ubi_upper or 'EDUCACIÓN' in ubi_upper or 'DAEM' in ubi_upper:
        return ("DAEM", "DEM")

    escuelas = load_escuelas()
    ubi_norm = _normalize_school_name(ubi)
    ubi_nospace = _normalize_for_comparison(ubi)

    # Estrategia en cascada, de la más estricta a la más laxa. Se devuelve el
    # primer match que aparezca, por lo que el ORDEN de los intentos importa.

    # 1) Match exacto tras normalización suave (mismo nombre, mismo espaciado).
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_norm = _normalize_school_name(esc_name)
        if ubi_norm == esc_norm:
            return (esc_name, esc.get("rbd-dv", ""))

    # 2) Match sin espacios ni artículos (tolera typos y espaciados distintos).
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_nospace = _normalize_for_comparison(esc_name)
        if ubi_nospace == esc_nospace:
            return (esc_name, esc.get("rbd-dv", ""))

    # 3) Match por contención: el nombre del catálogo aparece DENTRO de la
    #    ubicación (ej. la ubicación trae texto extra que no supimos limpiar).
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_norm = _normalize_school_name(esc_name)
        if esc_norm and esc_norm in ubi_norm:
            return (esc_name, esc.get("rbd-dv", ""))

    return None
