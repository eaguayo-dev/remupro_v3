"""
Versión de la aplicación y registro de actualizaciones (single source of truth).

¿Por qué existe este archivo?
    Para que TODO lo relacionado a "qué versión es" y "qué cambió y cuándo" viva
    en UN solo lugar. El aviso de "Sistema actualizado" que ve el usuario en la
    app y el archivo CHANGELOG.md se alimentan de aquí. Cuando hagas un cambio
    importante, agregas una entrada nueva ARRIBA de la lista UPDATES y listo:
    el aviso y la versión se actualizan solos.

Cómo agregar una actualización (lo único que necesitas recordar cada mes):
    1. Sube el número de versión en APP_VERSION (ej: "2.6.0" -> "2.7.0").
    2. Agrega un dict NUEVO al PRINCIPIO de la lista UPDATES con:
         - "version": misma que pusiste en APP_VERSION
         - "date":    fecha en formato "YYYY-MM-DD" (año-mes-día)
         - "summary": lista de frases cortas explicando qué cambió
    3. (Opcional) Refleja lo mismo en CHANGELOG.md para el registro humano.

    El aviso en la app usa 'version' + 'date' para saber si es "nuevo". Cuando
    el usuario aprieta "Ya lo vi", el aviso se oculta durante esa sesión; si
    refresca o reabre la página vuelve a aparecer. Al subir la versión aquí, el
    aviso reaparece para todos aunque lo hubieran descartado antes.
"""

from typing import List, Dict

# Versión visible de la app. Súbela cuando publiques cambios relevantes.
APP_VERSION: str = "2.6.0"

# Historial de actualizaciones, de la MÁS NUEVA (arriba) a la más antigua.
# La primera entrada de la lista es la que dispara el aviso "Sistema actualizado".
UPDATES: List[Dict] = [
    {
        "version": "2.6.0",
        "date": "2026-07-02",
        "summary": [
            "Ahora la app avisa cuando falta una columna de salario esperada "
            "(ej. 'Aporte Adicional AFP') en vez de omitirla en silencio.",
            "El Lote Anual avisa si un archivo no tiene el mes en el nombre, si "
            "no se pudo clasificar, o si el archivo de horas no trae columna 'Mes'.",
            "Se reconoce la columna 'Aporte Adicional AFP' en el cálculo especial.",
            "Nuevo aviso de 'Sistema actualizado' con botón para no volver a mostrarlo.",
            "Documentación: comentarios en el código, DEV_GUIDE.md y CHANGELOG.md.",
        ],
    },
]


def latest_update() -> Dict:
    """Retorna la actualización más reciente (la primera de UPDATES).

    Si por algún motivo la lista está vacía, retorna un dict "seguro" para que
    la interfaz nunca se rompa por falta de datos.
    """
    if not UPDATES:
        return {"version": APP_VERSION, "date": "", "summary": []}
    return UPDATES[0]
