<div align="center">
  <h1>RemuPro v3</h1>
  <p><strong>Sistema de Procesamiento de Remuneraciones Educativas</strong></p>
  <p>Distribucion automatizada de la BRP para el DAEM de Galvarino</p>
</div>

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.13-blue.svg" alt="Python 3.13">
  <img src="https://img.shields.io/badge/FastAPI-0.115-green.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/Next.js-14-black.svg" alt="Next.js 14">
  <img src="https://img.shields.io/badge/Electron-33-blue.svg" alt="Electron">
  <img src="https://img.shields.io/badge/Licencia-MIT-green.svg" alt="MIT">
</div>

---

## Que hace

RemuPro automatiza la distribucion de la **Bonificacion de Reconocimiento Profesional (BRP)** entre subvenciones SEP, PIE y Normal. Toma 3 archivos de entrada (MINEDUC, SEP, NORMAL/PIE), los procesa y genera reportes detallados por docente y establecimiento.

## Arquitectura

```
remupro_v3/
  app.py                    # UI Streamlit (legado)
  processors/               # Logica de procesamiento
    sep.py                  # Procesador SEP
    pie.py                  # Procesador PIE/Normal
    brp.py                  # Distribucion BRP
    integrado.py            # Orquestador SEP+PIE+BRP
    rem.py                  # Analisis horas REM
  config/
    columns.py              # Definiciones de columnas Excel
    escuelas.json           # Mapa RBD -> nombre escuela
  database/
    models.py               # SQLAlchemy (ProcesamientoMensual, DocenteMensual, ColumnAlertPreference)
    repository.py           # Queries: busqueda, tendencias, multi-establecimiento
  reports/
    audit_log.py            # Sistema de auditoria
    word_report.py          # Generacion informes Word
  api/                      # REST API (FastAPI)
    main.py                 # App FastAPI + CORS + routers
    routes/
      upload.py             # Subida de archivos
      process.py            # Procesamiento integrado
      data.py               # Resultados + descargas individuales
      dashboard.py          # Datos historicos, tendencias, busqueda
      preferences.py        # Preferencias de alertas de columnas
    ws.py                   # WebSocket para progreso en tiempo real
  dashboard/                # Frontend (Next.js 14 + shadcn/ui + Recharts)
    src/app/                # Paginas: /, /upload, /results, /multi-establecimiento, /auditoria, /alertas
    src/components/         # Componentes: sidebar, download-selector, monthly-trends-chart, etc.
    src/lib/                # API client, store, utilidades
  electron/                 # Empaquetado desktop (Windows/macOS/Linux)
    main.js                 # Proceso principal: lanza FastAPI + BrowserWindow
  tests/                    # Tests pytest (28 tests)
```

## Funcionalidades principales

- **Distribucion BRP automatizada**: Calcula BRP proporcional por horas SEP/PIE/Normal para cada docente
- **Multi-establecimiento**: Detecta docentes en 2+ establecimientos, distribuye por RBD
- **Descargas individuales**: SEP procesado, NORMAL/PIE procesado, BRP distribuido, Excel combinado, Informe Word
- **Dashboard historico**: Tendencias mensuales, busqueda de docentes, comparacion entre meses
- **Alertas de columnas**: Configura columnas como Normal/Ignorar/Importante
- **Analisis REM**: Clasificacion de contratos por tipo (SEP/PIE/EIB/NORMAL)
- **Auditoria**: Log estructurado de cada operacion con niveles INFO/WARNING/ERROR
- **Limite 44 horas**: Validacion automatica del maximo legal

## Inicio rapido

### Opcion 1: Streamlit (desarrollo)

```bash
git clone https://github.com/eaguayo-dev/remupro_v3.git
cd remupro_v3
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt
streamlit run app.py
```

### Opcion 2: API + Dashboard (produccion)

```bash
# Terminal 1: API
.venv/bin/uvicorn api.main:app --port 8000

# Terminal 2: Dashboard
cd dashboard
npm install
npm run dev
# Abrir http://localhost:3000
```

### Opcion 3: Electron (escritorio)

```bash
# Build completo
./build.sh          # macOS/Linux
build.bat           # Windows
# Instalador generado en dist/
```

## Archivos de entrada

| Archivo | Prefijo | Descripcion |
|---------|---------|-------------|
| CPEIP | `web*` | Asignaciones por sostenedor y establecimiento |
| SEP | `sep*` | Liquidacion subvencion SEP |
| NORMAL/PIE | `sn*` o `*pie*` | Liquidacion subvencion Normal y PIE |
| REM (opcional) | `rem*` | Resumen de horas por contrato |

## API endpoints

| Endpoint | Descripcion |
|----------|-------------|
| `POST /api/upload` | Subir archivos Excel/CSV |
| `POST /api/process/integrado` | Procesar SEP+PIE+BRP |
| `GET /api/results/{sid}/download/{sep,pie,brp,combo,word}` | Descargas individuales |
| `GET /api/dashboard/months` | Meses disponibles |
| `GET /api/dashboard/trends` | Tendencias multi-mes |
| `GET /api/dashboard/teachers/{mes}` | Busqueda paginada de docentes |
| `GET /api/preferences/columns` | Preferencias de alertas |
| `PUT /api/preferences/columns/{key}` | Actualizar preferencia |

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

28 tests cubriendo endpoints de dashboard, preferencias y descargas.

## Licencia

MIT. La responsabilidad final sobre la veracidad de los datos procesados recae en el usuario.

---
<div align="center">
  Desarrollado por Eric Aguayo Quintriqueo
</div>
