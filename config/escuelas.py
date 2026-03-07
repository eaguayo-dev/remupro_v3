"""
Utilidades para matching de escuelas/establecimientos por nombre y RBD.

Carga config/escuelas.json y provee funciones para resolver
nombres de ubicación a establecimientos conocidos.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ESCUELAS_CACHE: Optional[List[Dict[str, str]]] = None
_RBD_MAP_CACHE: Optional[Dict[str, str]] = None


def load_escuelas() -> List[Dict[str, str]]:
    """Carga config/escuelas.json y cachea en memoria."""
    global _ESCUELAS_CACHE
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
    """Retorna mapa rbd (sin DV) -> nombre establecimiento."""
    global _RBD_MAP_CACHE
    if _RBD_MAP_CACHE is not None:
        return _RBD_MAP_CACHE

    escuelas = load_escuelas()
    _RBD_MAP_CACHE = {}
    for esc in escuelas:
        rbd_dv = esc.get("rbd-dv", "")
        rbd = rbd_dv.split("-")[0] if "-" in rbd_dv else rbd_dv
        _RBD_MAP_CACHE[rbd] = esc.get("establecimiento", "")
    return _RBD_MAP_CACHE


def _normalize_school_name(name: str) -> str:
    """Normaliza nombre: uppercase, strip sufijos RBD/Nº/G Nº."""
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
    """Quita TODOS los espacios y artículos para manejar typos y variaciones."""
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

    # Caso especial: DAEM / DEM
    ubi_upper = ubi.upper()
    if 'EDUCACION' in ubi_upper or 'EDUCACIÓN' in ubi_upper or 'DAEM' in ubi_upper:
        return ("DAEM", "DEM")

    escuelas = load_escuelas()
    ubi_norm = _normalize_school_name(ubi)
    ubi_nospace = _normalize_for_comparison(ubi)

    # Match exacto normalizado
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_norm = _normalize_school_name(esc_name)
        if ubi_norm == esc_norm:
            return (esc_name, esc.get("rbd-dv", ""))

    # Match sin espacios (typos)
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_nospace = _normalize_for_comparison(esc_name)
        if ubi_nospace == esc_nospace:
            return (esc_name, esc.get("rbd-dv", ""))

    # Match por contencion (nombre escuela contenido en ubicacion)
    for esc in escuelas:
        esc_name = esc.get("establecimiento", "")
        esc_norm = _normalize_school_name(esc_name)
        if esc_norm and esc_norm in ubi_norm:
            return (esc_name, esc.get("rbd-dv", ""))

    return None
