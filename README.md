# MEF Subnational Efficiency MCP

Pipeline local multi-agente que analiza la **eficiencia del gasto público subnacional del Perú (2025)** usando datos abiertos del Ministerio de Economía y Finanzas (MEF). Combina un servidor MCP local, un pipeline de datos con estrategia anti-context-flooding, una arquitectura de skills duales (Executor + Evaluator) y un dashboard Streamlit de 4 tabs.

---

## Arquitectura

```
Portal de Datos Abiertos MEF (DKAN)
        │
        ▼
┌─────────────────────────────┐
│   MCP Server (stdio)        │  ← Claude Code lo invoca via tools
│   mef-datos-abiertos        │    5 herramientas sobre el portal DKAN
└────────────┬────────────────┘
             │ URL directa del CSV (2,664 MB)
             ▼
┌─────────────────────────────┐
│   src/data_pipeline.py      │  Executor skill → python -m src.run_skill executor
│                             │
│  1. Descarga del CSV        │  HTTP streaming → data/raw/ (caché)
│  2. DuckDB projection       │  Solo 22 cols de 73 · solo filas R/M
│  3. Escritura atómica       │  .tmp → os.replace → Parquet final
└────────────┬────────────────┘
             │ 4 Parquets (~54 KB total)
             ▼
┌─────────────────────────────┐
│   data/processed/           │
│   resumen_nacional.parquet  │  1 fila · KPIs nacionales
│   por_departamento.parquet  │  25 filas · una por departamento
│   hall_of_shame.parquet     │  1,535 filas · ejecutoras con PIM > S/10M
│   por_funcion.parquet       │  23 filas · una por función de gasto
└────────────┬────────────────┘
             │
      ┌──────┴──────┐
      ▼             ▼
┌──────────┐  ┌──────────────────────────┐
│Evaluator │  │  src/analytical_engine.py│
│  skill   │  │  Capa de lectura de      │
│ audita + │  │  Parquets para el        │
│ qa_report│  │  dashboard               │
└──────────┘  └──────────┬───────────────┘
                          │
                          ▼
               ┌─────────────────┐
               │    app.py       │
               │  Dashboard      │
               │  Streamlit      │
               │  4 tabs         │
               └─────────────────┘
```

### Componentes

#### MCP Server — `mef-datos-abiertos`

Servidor local stdio que expone 5 herramientas sobre el Portal Nacional de Datos Abiertos del Perú. Claude Code lo invoca durante la exploración inicial y para que el Executor localice los recursos CSV antes de procesarlos.

| Herramienta | Descripción |
|---|---|
| `buscar_datasets` | Busca datasets por palabra clave (filtra `package_list` + `package_show`) |
| `obtener_detalle_dataset` | Metadata y lista de recursos de un dataset por slug o UUID |
| `inspeccionar_esquema_csv` | Headers y 5 filas de muestra sin descargar el archivo completo (streaming de 256 KB) |
| `obtener_ultimas_actualizaciones` | Últimos datasets publicados en el portal |
| `descargar_documento_1964` | Descarga documentos PDF del portal a `data/raw_pdfs/` |

El servidor implementa un **fallback CKAN → DKAN**: intenta primero la capa de compatibilidad CKAN (`/api/3/action/`) y, si responde con redirect o error, cae al endpoint DKAN nativo (`/api/action/`). Esto es necesario porque `package_search` redirige a HTML en este portal.

#### Pipeline de datos — `src/data_pipeline.py`

Convierte el CSV de 2,664 MB en 4 Parquets de ~54 KB en total (**reducción del 99.99%**) sin cargar el archivo completo en memoria.

**Estrategia snapshot → filtrado local:**
1. **Descarga con caché**: descarga el CSV una sola vez a `data/raw/` vía HTTP streaming. Las ejecuciones posteriores reutilizan el caché. Soporta modo demo con `--sample-mb N` (HTTP Range request).
2. **Proyección con DuckDB**: lee solo las 22 columnas necesarias de las 73 disponibles y filtra únicamente filas de gobierno subnacional (`NIVEL_GOBIERNO IN ('R', 'M')`), resultando en 2,347,096 filas.
3. **Escritura atómica**: cada Parquet se escribe primero a un `.tmp` y luego se mueve con `os.replace()`. Al ser atómico en el mismo filesystem, el dashboard siempre lee el archivo anterior completo o el nuevo completo; nunca un estado intermedio corrupto.
4. **Soporte de periodos**: año completo (`2025`), trimestres (`2025-Q1..Q4`) o acumulado hasta un mes (`2025-06`).

#### Skills duales — separación de responsabilidades

| | Executor | Evaluator |
|---|---|---|
| **Rol** | Constructor | Auditor |
| **Entrada** | Periodo (`--periodo`) | Parquets existentes |
| **Salida** | 4 Parquets en `data/processed/` | `data/processed/qa_report.md` |
| **Usa** | MCP server + `data_pipeline.py` | `analytical_engine.py` |
| **No hace** | Auditar su propio trabajo | Modificar datos ni código |

El Evaluator ejecuta 8 verificaciones automáticas: completitud territorial (25 departamentos), rango de avance % en [0%, 100%], consistencia PIM vs. devengado, nulos en columnas clave, umbral del Hall of Shame (PIM > S/10M), duplicados de ejecutora, cuadratura del resumen nacional vs. suma de departamentos (±1%), y cobertura del capital paralizado.

#### Dashboard Streamlit — `app.py`

Consume únicamente los 4 Parquets vía `analytical_engine.py`. Nunca toca el CSV gigante. Usa `@st.cache_data(ttl=3600)` para no releer los Parquets en cada interacción.

| Tab | Contenido |
|---|---|
| **Resumen Nacional 2025** | 4 métricas (PIM, Devengado, Avance %, Saldo), donut chart Devengado vs. Saldo, barra de progreso |
| **Distribución Territorial** | Bar chart horizontal por departamento coloreado por avance % (rojo/amarillo/verde), tabla ordenable |
| **Hall of Shame** | Top-15 ejecutoras por capital paralizado (S/), tabla completa, slider de PIM mínimo, filtro por departamento |
| **Reporte de Calidad / QA** | Renderiza `qa_report.md` generado por el Evaluator; placeholder si aún no se generó |

---

## Estructura del repositorio

```
mef_subnational_efficiency_mcp/
│
├── app.py                          # Dashboard Streamlit (4 tabs)
├── requirements.txt                # Dependencias Python
├── .mcp.json                       # Registro del MCP server para Claude Code
│
├── src/
│   ├── mcp_server.py               # Servidor MCP stdio (5 tools)
│   ├── data_pipeline.py            # Descarga + DuckDB + escritura atómica de Parquets
│   ├── analytical_engine.py        # Capa de lectura de Parquets (usada por app.py y el Evaluator)
│   ├── run_skill.py                # CLI unificado: executor y evaluator
│   ├── ocr_engine.py               # Motor OCR para documentos históricos (pendiente)
│   └── utils.py                    # Logger compartido
│
├── .claude/
│   └── skills/
│       ├── executor/SKILL.md       # Skill ejecutable del Executor (formato Claude Code)
│       ├── evaluator/SKILL.md      # Skill ejecutable del Evaluator (formato Claude Code)
│       ├── executor_skill.json     # Spec declarativa del Executor
│       └── evaluator_skill.json    # Spec declarativa del Evaluator
│
├── data/
│   ├── raw/                        # CSV original del MEF (~2.66 GB, excluido de git)
│   ├── raw_pdfs/                   # PDFs descargados vía MCP (excluidos de git)
│   ├── processed/                  # 4 Parquets + qa_report.md (generados)
│   └── snapshots/                  # schema_candidato_A.json (evidencia de estructura)
│
├── scripts/
│   └── test_mcp.py                 # Script de prueba manual del MCP server
│
└── logs/
    └── mcp_server.log              # Log unificado de todos los módulos
```

---

## Instalación

### 1. Crear el environment y dependencias

```bash
conda create -n mef_subnacional python=3.10
conda activate mef_subnacional
pip install -r requirements.txt
```

### 2. Registrar el MCP server en Claude Code

El archivo `.mcp.json` del repositorio ya registra el servidor automáticamente para Claude Code cuando abres el proyecto. Si necesitas registrarlo manualmente:

```bash
claude mcp add --transport stdio --scope project \
  mef-datos-abiertos -- python -m src.mcp_server
```

Al abrirse el proyecto, Claude Code pedirá aprobar el servidor MCP. Selecciona **Allow** para que las 5 herramientas queden disponibles.

---

## Uso

### Pipeline de datos

```bash
# Año completo 2025 (usa MONTO_DEVENGADO_ANUAL del CSV)
python -m src.data_pipeline --periodo 2025

# Trimestre Q3 (suma Julio + Agosto + Septiembre)
python -m src.data_pipeline --periodo 2025-Q3

# Acumulado hasta Junio
python -m src.data_pipeline --periodo 2025-06

# Demo rápida sin descargar los 2.66 GB (HTTP Range de los primeros 100 MB)
python -m src.data_pipeline --sample-mb 100
```

El CSV se cachea en `data/raw/`. Las ejecuciones posteriores no re-descargan.

### Skills (CLI period-driven)

```bash
# Executor: procesa el CSV y regenera los 4 Parquets
python -m src.run_skill executor --periodo 2025
python -m src.run_skill executor --periodo 2025-Q4

# Evaluator: audita los Parquets actuales y genera qa_report.md
python -m src.run_skill evaluator

# Flujo completo para un nuevo periodo
python -m src.run_skill executor --periodo 2025-Q3
python -m src.run_skill evaluator
```

### Dashboard

```bash
streamlit run app.py
```

Abre `http://localhost:8501`. El dashboard lee los Parquets existentes; no requiere conexión a internet después de la primera descarga del CSV.

---

## Métricas clave

### Avance de ejecución (%)

```
Avance % = (MONTO_DEVENGADO_ANUAL / MONTO_PIM) × 100
```

Mide qué porcentaje del presupuesto aprobado y modificado (PIM) fue efectivamente ejecutado (devengado). Un avance bajo al cierre del año indica subejecución presupuestal.

### Saldo No Devengado (capital congelado)

```
Saldo No Devengado = MONTO_PIM − MONTO_DEVENGADO_ANUAL
```

El monto en soles que tenía presupuesto asignado pero no fue ejecutado. En el dashboard se denomina "capital congelado" porque representa recursos que no llegaron a bienes o servicios públicos.

### Hall of Shame

Ejecutoras con **PIM > S/ 10,000,000** ordenadas por avance % de menor a mayor. El umbral de S/10M filtra ejecutoras de cierto peso presupuestal para evitar que unidades pequeñas con ejecución cero distorsionen el ranking. Los datos reales del dataset completo 2025 muestran **1,535 ejecutoras** en esta categoría.

### Cifras del dataset completo (2025-Gasto-Devengado-Mensual.csv)

| Métrica | Valor |
|---|---|
| CSV fuente | 2,664 MB |
| Parquets generados | ~54 KB (reducción del 99.99%) |
| Filas procesadas (R + M) | 2,347,096 |
| Departamentos | 25 |
| Ejecutoras totales | 2,360 |
| Funciones de gasto | 23 |
| PIM subnacional total | S/ 115.12B |
| Devengado (año completo) | S/ 102.77B |
| Avance % nacional | 89.3% |
| Saldo no devengado | S/ 12.35B |

---

## Decisiones de diseño

### 5 tools en el MCP server, no más

Cada tool tiene una responsabilidad singular y un retorno predecible. Multiplicar tools para cubrir variantes del mismo recurso (por ejemplo, un tool por formato de descarga) aumenta la superficie de fallo sin aportar capacidad nueva. Con 5 tools, Claude Code puede resolver cualquier consulta sobre el portal componiendo llamadas.

### Fallback CKAN → DKAN

El portal `datosabiertos.gob.pe` expone simultáneamente una capa de compatibilidad CKAN (`/api/3/action/`) y su API nativa DKAN (`/api/action/`). En la práctica, `package_list` y `package_show` funcionan vía CKAN, mientras que `package_search` redirige a HTML y el datastore DKAN retorna HTTP 500 para la mayoría de recursos. El helper `_smart_get` intenta CKAN primero y cae a DKAN si detecta un redirect o error, sin requerir configuración manual.

### Escritura atómica de Parquets

La escritura directa de un Parquet grande (como `hall_of_shame.parquet`, ~46 KB con 1,535 ejecutoras) tarda lo suficiente para que el dashboard lo lea en estado intermedio si el pipeline y Streamlit corren en paralelo. La estrategia `write_parquet(tmp) → os.replace(tmp, dest)` garantiza que el archivo final sea siempre atómicamente válido: el lector ve el archivo anterior completo o el nuevo completo, nunca bytes parciales.

### Estrategia snapshot anti-context-flooding

El CSV de 2,664 MB no puede enviarse a un LLM ni cargarse íntegramente en memoria de forma eficiente. DuckDB lee el archivo en streaming y proyecta solo las 22 columnas necesarias, filtrando las 2,347,096 filas subnacionales. Los 4 Parquets resultantes (~54 KB) caben cómodamente en el contexto de cualquier herramienta de análisis.

---

## Limitaciones

### Componente histórico 1964 (OCR) — no incluido en esta entrega

El repositorio contiene `src/ocr_engine.py` y archivos en `data/raw_pdfs/` correspondientes a la **Cuenta General de la República de 1964**, un documento escaneado que requeriría OCR (PaddleOCR) para extraer datos de ejecución presupuestal histórica. Esta funcionalidad no se incluyó en la entrega actual por restricciones de tiempo de cómputo: el procesamiento OCR de un PDF de esa extensión requiere varias horas en CPU y validación manual de la calidad de extracción.

La arquitectura está diseñada para incorporar esta pista en una fase posterior sin modificar el pipeline 2025.

---

## Fuente de datos

**Portal Nacional de Datos Abiertos del Perú — MEF**
- Dataset: *Ejecución del Gasto — Devengado Mensual 2025*
- URL directa: `https://fs.datosabiertos.mef.gob.pe/datastorefiles/2025-Gasto-Devengado-Mensual.csv`
- Licencia: Datos Abiertos — uso libre con atribución
