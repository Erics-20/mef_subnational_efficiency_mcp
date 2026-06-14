---
name: evaluator
description: >
  Audita los Parquets generados por el Executor y produce un reporte de calidad
  en data/processed/qa_report.md. Úsalo después de correr el executor o cuando
  necesites verificar la integridad de los datos del dashboard
  (ej. "corre el evaluador", "genera el reporte QA", "audita los datos",
  "verifica que los 25 departamentos estén completos").
---

# Evaluator Skill — Auditor de Calidad (QA)

## Rol

El Evaluator es el **agente auditor**: revisa los Parquets generados por el Executor,
detecta inconsistencias, y genera el reporte de calidad que aparece en el Tab 4 del
dashboard. **No construye features nuevos** — solo valida, audita y reporta.

## Cuándo invocar este skill

- Después de correr el Executor para un nuevo periodo.
- Cuando el Tab 4 del dashboard muestra "Reporte QA pendiente de generación".
- Para verificar la integridad antes de presentar los datos.
- Periódicamente como control de calidad.

## Cómo correrlo

```bash
python -m src.run_skill evaluator
```

No requiere parámetro de periodo — audita los Parquets que existan actualmente
en `data/processed/`.

## Verificaciones que realiza

| # | Verificación | Qué detecta |
|---|---|---|
| 1 | **Completitud territorial** | ¿Están los 25 departamentos del Perú? ¿Hay departamentos extra? |
| 2 | **Rango de avance %** | Valores imposibles: negativos o >100% |
| 3 | **Consistencia PIM vs Devengado** | PIM=0 con devengado>0; devengado>PIM (+1% tolerancia) |
| 4 | **Nulos en columnas clave** | Campos críticos con valores nulos |
| 5 | **Umbral Hall of Shame** | Confirma que todas las ejecutoras tienen PIM > S/ 10M |
| 6 | **Duplicados de ejecutora** | Ejecutoras repetidas en hall_of_shame |
| 7 | **Cuadratura nacional** | Suma de departamentos ≈ resumen_nacional (±1%) |
| 8 | **Cobertura del Hall of Shame** | Capital paralizado total; % de ejecutoras cubiertas |

## Semáforo de resultados

- `✅ PASS` — verificación superada sin anomalías
- `⚠️ WARN` — anomalía menor que no invalida los datos (ej. devengado ligeramente >PIM)
- `❌ FAIL` — anomalía grave que requiere revisión del pipeline

## Salida

Genera `data/processed/qa_report.md` con:
- Resultado global (APROBADO / CON ADVERTENCIAS / CON FALLOS)
- Tabla de métricas nacionales
- Detalle de cada verificación con hallazgos específicos

Este archivo es **leído automáticamente por el Tab 4 del dashboard** (`app.py`).

## Scripts involucrados

- `src/run_skill.py evaluator` — punto de entrada CLI
- `src/analytical_engine.py` — carga los Parquets para auditarlos
- `data/processed/qa_report.md` — reporte generado (leído por Tab 4)

## Qué NO hace

- No re-procesa el CSV ni regenera los Parquets (→ usa el Executor para eso).
- No modifica los datos; solo los lee y reporta hallazgos.
- No modifica `app.py` ni ningún código fuente.
