# Guía de desarrollo — RemuPro

Esta guía explica **qué hace cada parte del sistema y por qué**, para que
cualquiera del equipo (o Claude) pueda entenderlo rápido sin tener que leer todo
el código. Si vas a tocar el proyecto después de un tiempo, empieza por aquí.

> RemuPro procesa **remuneraciones de docentes** de un sostenedor educacional
> chileno (DAEM). Toma planillas de horas y montos, las **prorratea por tipo de
> subvención** (SEP / PIE / Normal / EIB) y genera un Excel consolidado, además
> de cálculos MINEDUC (BRP) y comparaciones entre meses.

---

## 1. Mapa del proyecto

```
app.py                 # Interfaz web (Streamlit). TODO lo que ve el usuario.
config/
  columns.py           # Nombres de columnas, clasificación, y utilidades (RUT, meses).
  escuelas.py          # Mapa RBD → nombre de establecimiento.
  version.py           # Versión + registro de actualizaciones (alimenta el aviso).
processors/            # El "motor". Cada archivo procesa un tipo de dato.
  base.py              # Clase base común: validación, carga, prorrateo, alertas.
  sep.py / pie.py / eib.py   # Prorrateo por subvención SEP / PIE+SN / EIB.
  brp.py               # Cálculo BRP con datos MINEDUC (web sostenedor).
  integrado.py         # Junta SEP+PIE+BRP en un solo flujo.
  anual_batch.py       # Lote Anual: procesa 12 meses de golpe.
  rem.py / duplicados.py     # Utilidades (archivo REM, detección de duplicados).
database/              # Persistencia (SQLAlchemy) y comparación entre meses.
  models.py            # Tablas (procesamientos, detalles por docente/mes).
  repository.py / repository_anual.py   # Guardar / leer procesamientos.
  comparador.py        # Comparar dos meses.
reports/
  audit_log.py         # Registro estructurado de eventos (avisos/errores).
  word_report.py       # Informe en Word.
tests/                 # Tests con pytest.
```

**Regla mental:** `app.py` = interfaz, `processors/` = cálculo, `config/` =
configuración, `database/` = memoria, `reports/` = salidas. Si buscas *qué
calcula*, mira `processors/`. Si buscas *qué columna se usa*, mira
`config/columns.py`.

---

## 2. Conceptos del dominio (imprescindibles)

- **Subvenciones:** el sueldo de un docente se financia con distintas fuentes.
  Las horas se reparten entre:
  - **SEP** — Subvención Escolar Preferencial.
  - **PIE** — Programa de Integración Escolar.
  - **SN / Normal** — subvención general.
  - **EIB** — Educación Intercultural Bilingüe.
- **Prorrateo:** un monto (ej. sueldo base) se reparte proporcionalmente a las
  horas de cada subvención. Fórmula base:
  `valor_subvención = (monto_total / horas_totales) × horas_de_esa_subvención`.
  Vive en `BaseProcessor.calculate_proportional_value`.
- **BRP:** Bonificación de Reconocimiento Profesional; se calcula con datos del
  archivo MINEDUC ("web sostenedor").
- **DAEM vs CPEIP:** DAEM = lo que paga el municipio (subvención). CPEIP = lo que
  transfiere el ministerio (transferencia directa).
- **RUT:** siempre se normaliza (sin puntos ni guion) con
  `config.columns.normalize_rut` antes de cruzar datos entre archivos.

---

## 3. Cómo se procesa un archivo (flujo SEP/PIE/EIB)

Cada archivo de entrada tiene **dos hojas**:
- `HORAS` — `Rut`, `Nombre`, y una columna de horas (`SEP`, `PIE`/`SN`, o `Jornada`).
- `TOTAL` — montos por RUT (sueldo base, asignaciones, aportes, etc.).

Pasos (ver `processors/sep.py`, `pie.py`, `eib.py`):
1. `load_sheets` carga ambas hojas y limpia columnas (`clean_columns`).
2. `validate_columns` exige columnas estructurales (`Rut`, `Nombre`, horas). Si
   faltan → **lanza excepción** y el proceso se detiene.
3. Se calcula el total de horas por docente y se cruzan HORAS + TOTAL por `Rut`.
4. Se **prorratean** las columnas de montos según horas.
5. Se validan las horas (máximo configurable, `MAX_HOURS`).

### Columnas de montos: dos listas (importante)
En `config/columns.py`:
- **`SPECIAL_SALARY_COLUMNS`** — montos que **deberían venir siempre** (sueldo
  base, mutual, SIS, `Aporte Adicional AFP`, etc.). En PIE se dividen en dos
  columnas (`... PIE` y `... SN`); en SEP/EIB se prorratean como el resto.
  **Si falta una de estas, ahora se AVISA al usuario** (ver sección 5).
- **`SALARY_BENEFIT_COLUMNS`** — asignaciones/bonos opcionales que no aplican a
  todos los colegios. Si faltan, se omiten **en silencio** (a propósito: avisar
  de todas generaría ruido).

> **Para agregar/renombrar una columna de monto:** edita la lista correspondiente
> en `config/columns.py`. El nombre debe coincidir **exactamente** con el de la
> planilla (mayúsculas, tildes, espacios). Un nombre que no calza = columna que
> "desaparece" del resultado.

---

## 4. Cómo se asigna el MES (clave para consolidados)

La hoja `HORAS` **no tiene** columna de fecha. El mes se determina por fuera, de
tres formas posibles:

1. **Por el nombre del archivo** (flujo normal). `detect_month_from_filename`
   busca `enero`/`ene`/... en el nombre. Ej: `sep_enero_2026.xlsx` → mes 01.
   Todas las horas de ese archivo pertenecen a ese mes.
2. **Por columna `Periodo`** (archivo anual consolidado). `anual_batch.py` parte
   el archivo por período.
3. **Por columna `Mes`** (archivo de "horas reales" separado). Debe traer
   `Mes + Rut + SEP + PIE + SN`. Si NO trae `Mes`, no se pueden asignar las
   horas a cada mes y el sistema cae a **horas estimadas** por tipo de contrato.

### Lote Anual (`processors/anual_batch.py`)
Procesa los 12 meses juntos:
- `classify_files` agrupa los archivos subidos por mes (según el nombre) en
  objetos `MonthlyFileSet`.
- Procesa cada mes y concatena todo agregando columnas `MES` / `MES_NUM`.
- Guarda por mes en la BD (`database/repository_anual.py`).

**Recomendación para el usuario:** para un consolidado confiable, lo mejor es
subir **un archivo por mes con el mes en el nombre**. La alternativa (anual
consolidado + horas con columna `Mes`) funciona pero es más frágil.

---

## 5. Manejo de errores y avisos

Hay dos niveles, a propósito:

- **Columnas estructurales** (`Rut`, `Nombre`, horas): si faltan →
  **excepción** (`ColumnMissingError`), el proceso se detiene. No se puede
  seguir sin ellas.
- **Columnas de montos especiales** (`SPECIAL_SALARY_COLUMNS`): si faltan →
  **aviso** en pantalla (no detiene el proceso). Se acumulan en
  `BaseProcessor.column_alerts` vía `_record_missing_special_columns` y se
  muestran con `show_column_alerts` en `app.py`.
- **Lote Anual:** problemas de detección de mes/tipo/columna `Mes` se juntan en
  `AnualBatchProcessor.classification_warnings` y se muestran en la UI.
- **`reports/audit_log.py`** — registro estructurado de eventos (INFO/WARNING/
  ERROR) para informes y revisión.

> Filosofía: **fallar ruidosamente cuando falta algo esperado.** Antes, varias
> cosas se omitían en silencio y el usuario no se enteraba. La v2.6.0 corrigió
> los casos más importantes.

---

## 6. Versión y aviso "Sistema actualizado"

- La versión y el historial de cambios viven en **`config/version.py`**
  (constante `APP_VERSION` + lista `UPDATES`).
- `app.py → show_update_banner()` muestra un aviso nativo con la última
  actualización. El botón "Ya lo vi" lo oculta durante la sesión (usa
  `session_state`); si se refresca o reabre la página, reaparece. Es nativo (no
  un componente HTML) para que al descartarlo no quede espacio en blanco.
- El aviso es **solo un recordatorio visual**: no guarda datos ni afecta cálculos.

**Para publicar una actualización:** sube `APP_VERSION`, agrega la entrada en
`UPDATES` (config/version.py) y refleja lo mismo en `CHANGELOG.md`. Nada más.

---

## 7. Cómo correr y testear

```bash
# Instalar dependencias
pip install -r requirements.txt

# Correr la app
streamlit run app.py

# Correr los tests
pytest tests/ -q
```

> **Streamlit y cambios de código:** al hacer `git pull`, si tocaste archivos de
> `processors/` o `config/` (módulos importados), **reinicia** el proceso de
> Streamlit — el auto-reload no siempre recarga módulos importados. En Streamlit
> Community Cloud el redeploy es automático al hacer push.

---

## 8. Convenciones

- **RUT normalizado** para cruzar datos (`normalize_rut`), formateado solo para
  mostrar (`format_rut`).
- **Nombres de columnas**: exactos, en `config/columns.py`. No hardcodear
  nombres de columnas dentro de los procesadores.
- **Comentarios**: explica el *por qué*, no solo el *qué*. El código dice qué
  hace; los comentarios deben decir por qué se hace así.
- **Al agregar un cambio relevante**: actualiza `config/version.py` y
  `CHANGELOG.md` (y esta guía si cambia la arquitectura).
