<div align="center">
  <h1>RemuPro</h1>
  <p><strong>Procesamiento de remuneraciones educativas</strong></p>
  <p>Prorrateo de sueldos por subvención (SEP / PIE / Normal / EIB) y distribución de la BRP para el DAEM de Galvarino</p>
</div>

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Streamlit-app%20local-red.svg" alt="Streamlit">
  <img src="https://img.shields.io/badge/Licencia-MIT-green.svg" alt="MIT">
</div>

---

## Qué hace

RemuPro es una **aplicación local** (Streamlit) que corre en el computador del usuario, en Windows o Mac. No hay servidor, base de datos externa ni cuenta: los archivos se procesan en la máquina y los resultados se descargan como Excel o Word.

Toma las planillas de sueldos y de horas del DAEM y:

- **Prorratea** cada monto de sueldo entre las subvenciones según las horas de cada docente.
- **Distribuye la BRP** (Bonificación de Reconocimiento Profesional) con los datos del MINEDUC.
- **Compara meses**, consolida el año completo y detecta duplicados y docentes en más de un establecimiento.
- **Avisa** cuando una planilla trae columnas que el sistema no conoce, para que nada nuevo pase en silencio (ver [columnas y códigos H](docs/operaciones/columnas-y-codigos-h.md)).

## Páginas de la aplicación

La navegación está en el menú lateral, agrupada por secciones.

| Página | Grupo del menú | Para qué sirve |
|---|---|---|
| **Todo en Uno** | Procesar | Flujo completo de un mes: SEP + PIE + web sostenedor → BRP distribuido, Excel combinado e informe Word. |
| **SEP / PIE / EIB** | Procesar | Procesa un solo archivo (hojas `HORAS` y `TOTAL`) con el prorrateo de la subvención elegida. |
| **Lote Anual** | Anual | Procesa los 12 meses de golpe, con grilla editable para confirmar mes y tipo de cada archivo. |
| **Comparar Meses** | Análisis | Compara dos o más meses ya procesados. |
| **Horas x Contrato** | Análisis | Clasifica las horas de cada persona por tipo de contrato a partir de los archivos REM. |
| **Remuneraciones x Mes** | Procesar | Prorratea sueldos por SEP/PIE/SN con un único archivo de horas, **sin BRP ni web sostenedor**. Reproduce el formato clásico `_procesado`. |
| **Distribución BRP** y **Duplicados** | Herramientas | Ocultas por defecto; se activan en la barra lateral → *Secciones visibles*. |

## Inicio rápido

Requiere **Python 3.11 o superior**.

**macOS / Linux**

```bash
git clone https://github.com/eaguayo-dev/remupro_v3.git
cd remupro_v3
./Iniciar_RemuPro.sh
```

El script crea el entorno virtual, instala las dependencias la primera vez y abre la app en http://localhost:8501.

**Windows**

```bat
git clone https://github.com/eaguayo-dev/remupro_v3.git
cd remupro_v3
Iniciar_RemuPro.bat
```

**A mano** (cualquier sistema)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.lock
streamlit run app.py
```

> Tras un `git pull` que toque `processors/` o `config/`, reinicia Streamlit: el auto-recargado no siempre refresca los módulos importados.

## Archivos de entrada

Los archivos se reconocen por el **nombre**:

| Archivo | Nombre empieza / contiene | Descripción |
|---|---|---|
| web sostenedor | `web*` | Asignaciones del MINEDUC por sostenedor y establecimiento (datos para la BRP). |
| SEP | `sep*` | Planilla de sueldos de la subvención SEP. |
| Normal / PIE | `sn*` o `*pie*` | Planilla de sueldos de la subvención Normal y PIE. |
| EIB | `*eib*` | Planilla de Educación Intercultural Bilingüe. |
| REM | `REM 01 2026.xlsx` | Resumen de horas por contrato. Solo lo usa la pestaña Horas x Contrato. |

Cada planilla de sueldos lleva dos hojas: `HORAS` (`Rut`, `Nombre` y las horas por subvención) y `TOTAL` (los montos por RUT). El mes se toma del nombre del archivo (`sep_enero_2026.xlsx`) o de una columna `Mes`.

Las planillas pueden traer los haberes con o sin código `H###` (`H001 SUELDO BASE` o `SUELDO BASE`); ambos formatos se entienden.

## Estructura del repositorio

```
app.py                    # Interfaz Streamlit (todas las pestañas)
config/
  columns.py              # Nombres de columnas, códigos H###, control de columnas nuevas
  escuelas.py / .json     # Mapa RBD → establecimiento
  version.py              # Versión y aviso "Sistema actualizado"
processors/               # El motor de cálculo
  base.py                 # Clase base: carga, prorrateo, alertas
  sep.py  pie.py  eib.py  # Prorrateo por subvención
  brp.py                  # Distribución BRP con datos MINEDUC
  integrado.py            # Orquesta SEP + PIE + BRP
  anual.py  anual_batch.py        # Lote Anual
  remuneraciones_mes.py   # Remuneraciones x Mes (sin BRP)
  rem.py  duplicados.py   # Horas por contrato, duplicados
database/                 # Historial local en SQLite (data/remupro.db)
reports/                  # Informe Word y registro de auditoría
tests/                    # Tests con pytest
docs/                     # Documentación (ver abajo)
```

## Documentación

- [`docs/engineering/guia-desarrollo.md`](docs/engineering/guia-desarrollo.md): qué hace cada parte y por qué.
- [`docs/operaciones/columnas-y-codigos-h.md`](docs/operaciones/columnas-y-codigos-h.md): qué hacer cuando una planilla trae columnas nuevas.
- [`CHANGELOG.md`](CHANGELOG.md): historial de cambios.
- [`CLAUDE.md`](CLAUDE.md): reglas de trabajo para desarrollar con Claude Code.

## Pruebas

```bash
pytest tests/ -q
```

## Datos y privacidad

El historial se guarda en `data/remupro.db` (SQLite local), que está en el `.gitignore` porque contiene datos de remuneraciones. **Nunca subas ese archivo ni las planillas al repositorio.**

## Licencia

MIT. La responsabilidad final sobre la veracidad de los datos procesados recae en el usuario.

---
<div align="center">
  Desarrollado por Eric Aguayo Quintriqueo
</div>
