# Registro de cambios (CHANGELOG)

Este archivo lista los cambios importantes de RemuPro, del más nuevo al más
antiguo. La misma información (versión, fecha, resumen) vive en
[`config/version.py`](config/version.py) y alimenta el aviso "Sistema
actualizado" que aparece en la app.

> **Cómo agregar una entrada:** sube `APP_VERSION` en `config/version.py`,
> agrega el dict nuevo al principio de `UPDATES`, y refleja aquí lo mismo.
> El formato de fecha es `AAAA-MM-DD` (año-mes-día).

---

## [2.7.0] — 2026-10-13

### Agregado
- **Pestaña «Remuneraciones x Mes».** Prorratea sueldos por SEP/PIE/SN con un único archivo de horas, sin BRP ni web sostenedor. Reproduce el formato clásico `_procesado`. (`processors/remuneraciones_mes.py`, `app.py`)
- **Haberes con código `H###`.** Las planillas con títulos como `H001 SUELDO BASE` se reconocen igual que las de siempre; el código se quita al leer. (`config/columns.py`)
- **Aviso de columnas nuevas.** Una columna fuera de las listas conocidas genera una alerta en pantalla en vez de omitirse. (`processors/base.py`, `app.py`)
- **Check «Conservar códigos H###»** en SEP/PIE/EIB y Remuneraciones x Mes.
- `Cotización Expectativa de Vi` y `Rentabilidad Protegida` pasan a columnas especiales; `Aporte adicional empleador` es alias de `Aporte Adicional AFP`.

### Documentación
- README reescrito (app local para Windows y Mac), `CLAUDE.md` versionado y nueva carpeta `docs/`.

---

## [2.6.0] — 2026-07-02

### Agregado
- **Aviso de columnas de salario faltantes.** Si falta una columna especial
  esperada (ej. `Aporte Adicional AFP`), la app ahora lo avisa en pantalla en
  vez de omitirla en silencio. Aplica a SEP, PIE y EIB.
  (`processors/base.py`, `processors/pie.py`, `app.py`)
- **Avisos de detección en Lote Anual.** Se avisa cuando:
  - un archivo no trae el mes en el nombre y por eso no se incluye;
  - un archivo no se pudo clasificar (ni tipo, ni horas, ni anual consolidado);
  - un archivo parece de horas pero le falta la columna `Mes` (se usarían horas
    estimadas en vez de reales).
  (`processors/anual_batch.py`, `app.py`)
- **Aviso "Sistema actualizado"** con la fecha del cambio y un botón "Ya lo vi"
  que recuerda por navegador (localStorage) que ya se revisó, para no repetir.
  (`config/version.py`, `app.py`)
- **Documentación:** guía de desarrollo (`DEV_GUIDE.md`), este CHANGELOG y más
  comentarios explicativos dentro del código.

### Corregido
- Se reconoce la columna `Aporte Adicional AFP` en `SPECIAL_SALARY_COLUMNS`, de
  modo que se prorratea y aparece en el resultado (antes el nombre no coincidía).

---

## Versiones anteriores

Las versiones previas a la 2.6.0 no tenían este registro. El historial completo
está en el `git log` del repositorio.
