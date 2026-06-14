---
name: executor
description: >
  Ejecuta el pipeline de datos MEF para un periodo dado, descargando y procesando
  el CSV de Gasto Devengado Mensual 2025 y generando los 4 Parquets en data/processed/.
  Úsalo cuando necesites actualizar los datos del dashboard para un nuevo periodo
  (ej. "corre el executor para 2025-Q3", "actualiza los datos del trimestre 4",
  "procesa el periodo 2025-06").
---

# Executor Skill — Constructor del Pipeline de Datos MEF

## Rol

El Executor es el **agente constructor**: lee datos del portal MEF, procesa el CSV
y genera las salidas analíticas. **No audita su propio trabajo** — eso es responsabilidad
exclusiva del Evaluator.

## Cuándo invocar este skill

- El usuario pide procesar un periodo de presupuesto: año completo, trimestre o mes.
- Los Parquets en `data/processed/` están desactualizados o corruptos.
- Se quiere comparar la ejecución de distintos periodos (Q1 vs Q2, etc.).

## Cómo correrlo

```bash
# Periodo anual (default)
python -m src.run_skill executor --periodo 2025

# Trimestre
python -m src.run_skill executor --periodo 2025-Q3

# Acumulado hasta un mes
python -m src.run_skill executor --periodo 2025-06

# Forzar re-descarga del CSV
python -m src.run_skill executor --periodo 2025 --force-download
```

## Periodos válidos

| Formato   | Descripción                        | Ejemplo     |
|-----------|------------------------------------|-------------|
| `2025`    | Año completo (usa MONTO_DEVENGADO_ANUAL) | `2025`  |
| `2025-Q1` | Trimestre 1 (Enero–Marzo)          | `2025-Q1`   |
| `2025-Q2` | Trimestre 2 (Abril–Junio)          | `2025-Q2`   |
| `2025-Q3` | Trimestre 3 (Julio–Septiembre)     | `2025-Q3`   |
| `2025-Q4` | Trimestre 4 (Octubre–Diciembre)    | `2025-Q4`   |
| `2025-MM` | Acumulado hasta el mes MM          | `2025-06`   |

## Qué hace internamente

1. **Lee esquema vía MCP** (opcional): usa los tools `buscar_datasets` e
   `inspeccionar_esquema_csv` del servidor `mef-datos-abiertos` para verificar
   la estructura del CSV antes de procesar.
2. **Descarga el CSV** del portal MEF (`data/raw/2025-Gasto-Devengado-Mensual.csv`).
   Si ya existe en caché, lo reutiliza (no vuelve a descargar los 2.66 GB).
3. **Filtra y proyecta** vía DuckDB: solo filas de gobierno subnacional (R/M),
   solo las columnas necesarias de las 73 disponibles.
4. **Calcula métricas** fila a fila: `avance_pct`, `saldo_no_devengado`.
5. **Genera los 4 Parquets** en `data/processed/`:
   - `resumen_nacional.parquet` — KPIs agregados (1 fila)
   - `por_departamento.parquet` — 25 departamentos
   - `hall_of_shame.parquet` — ejecutoras con PIM > 10M
   - `por_funcion.parquet` — 23 funciones de gasto
6. **Verifica** que los Parquets son legibles (no corruptos).

## Scripts involucrados

- `src/data_pipeline.py` — lógica core de descarga y procesamiento
- `src/run_skill.py executor` — punto de entrada CLI
- `src/analytical_engine.py` — usado por el dashboard para leer los Parquets

## Salida esperada

```
EXECUTOR  |  Periodo: 2025-Q3
✓ resumen_nacional.parquet  (2654 bytes)
✓ por_departamento.parquet  (2916 bytes)
✓ hall_of_shame.parquet     (46950 bytes)
✓ por_funcion.parquet       (2950 bytes)
Executor completado. Parquets listos en data/processed/
Ahora puedes correr: python -m src.run_skill evaluator
```

## Qué NO hace

- No audita ni valida los datos generados (→ usa el Evaluator para eso).
- No modifica el dashboard `app.py`.
- No genera el reporte QA.
