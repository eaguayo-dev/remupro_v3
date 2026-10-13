# CLAUDE.md

Guía para trabajar en este repositorio con Claude Code.

## Qué es

RemuPro: aplicación **local** (Streamlit, Windows y Mac) que prorratea sueldos de docentes por subvención (SEP / PIE / Normal / EIB) y distribuye la BRP para el DAEM de Galvarino. Maneja **datos de remuneraciones**: un error de cálculo es un error de nómina.

## Comandos

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py            # app en http://localhost:8501
pytest tests/ -q                # tests (correrlos antes de cada commit)
```

Tras cambiar `processors/` o `config/`, **reiniciar Streamlit**: el auto-recargado no siempre refresca los módulos importados.

## Mapa

- `app.py` — toda la interfaz. Cada pestaña es una función `tab_*`. Es un archivo muy grande: **agregar código nuevo en módulos propios** (como `processors/remuneraciones_mes.py`) y no engordarlo.
- `config/columns.py` — única fuente de verdad de los nombres de columna. Ver `docs/operaciones/columnas-y-codigos-h.md`.
- `processors/` — el cálculo. `base.py` tiene la carga, el prorrateo y las alertas; `sep.py`, `pie.py`, `eib.py` heredan de él.
- `config/version.py` + `CHANGELOG.md` — versión y aviso «Sistema actualizado».
- `docs/` — documentación. Guía técnica en `docs/engineering/`.

## Reglas críticas

1. **No cambiar el cálculo sin pedirlo.** El prorrateo (`BaseProcessor.calculate_proportional_value`) y el redondeo son decisiones de negocio. Las pestañas nuevas reutilizan los procesadores existentes en vez de reimplementar.
2. **Fallar ruidosamente.** Una columna faltante o desconocida debe avisarse en pantalla, nunca omitirse en silencio. Si agregas una columna de monto, ponla en la lista correcta de `config/columns.py`; si no, el aviso de columnas nuevas saltará.
3. **No hardcodear nombres de columna** dentro de los procesadores.
4. **RUT siempre normalizado** con `normalize_rut` antes de cruzar archivos.
5. **Nunca subir datos reales.** `data/`, `*.db` y las planillas de sueldos no se versionan. Los tests usan datos sintéticos.
6. **Verificar en la app, no solo en tests.** Un cambio de interfaz o de cálculo se prueba con una planilla en Streamlit; lo que no se pudo ver se dice con esas palabras.

## Convenciones

- Comentarios que expliquen el *por qué*, no el *qué*.
- Al agregar un cambio relevante: subir `APP_VERSION` y agregar la entrada en `UPDATES` (`config/version.py`), reflejarlo en `CHANGELOG.md`, y actualizar `docs/` si cambia la arquitectura.
- Commits en español, estilo `feat(area): descripción` / `fix(area): descripción`.
- Push directo a `main` solo cuando el usuario lo pida; los tests deben estar en verde.
