"""CLI unificado para invocar el Executor y el Evaluator.

Uso:
    python -m src.run_skill executor --periodo 2025
    python -m src.run_skill executor --periodo 2025-Q3
    python -m src.run_skill executor --periodo 2025-06  --force-download
    python -m src.run_skill evaluator

El Executor corre el pipeline de datos y regenera los Parquets.
El Evaluator audita los Parquets y escribe data/processed/qa_report.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import polars as pl

from src.utils import get_logger
from src import data_pipeline
from src.analytical_engine import (
    load_hall_of_shame,
    load_por_departamento,
    load_por_funcion,
    load_resumen_nacional,
)

logger = get_logger("run_skill")

PROCESSED = _ROOT / "data" / "processed"

PERIODOS_DOC = "2025 | 2025-Q1..Q4 | 2025-01..2025-12"

# Los 25 departamentos esperados en el Perú (en mayúsculas, como viene el CSV)
DEPARTAMENTOS_PERU = {
    "AMAZONAS", "ANCASH", "APURIMAC", "AREQUIPA", "AYACUCHO",
    "CAJAMARCA", "CUSCO", "HUANCAVELICA", "HUANUCO", "ICA",
    "JUNIN", "LA LIBERTAD", "LAMBAYEQUE", "LIMA", "LORETO",
    "MADRE DE DIOS", "MOQUEGUA", "PASCO", "PIURA",
    "PROVINCIA CONSTITUCIONAL DEL CALLAO", "PUNO", "SAN MARTIN",
    "TACNA", "TUMBES", "UCAYALI",
}


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

def run_executor(periodo: str, force_download: bool = False) -> None:
    """Construye los Parquets para el periodo dado. No audita — eso es trabajo del Evaluator."""
    logger.info("═" * 60)
    logger.info("EXECUTOR  |  Periodo: %s", periodo)
    logger.info("═" * 60)

    # Validación básica de periodo antes de invocar el pipeline
    try:
        data_pipeline.resolve_period(periodo)
    except ValueError as exc:
        logger.error("Periodo inválido '%s': %s", periodo, exc)
        logger.error("Periodos válidos: %s", PERIODOS_DOC)
        sys.exit(1)

    # Disparar pipeline — usa el CSV cacheado si ya existe
    sizes = data_pipeline.run(
        periodo=periodo,
        force_download=force_download,
        sample_mb=0,
    )

    # Verificar que los 4 Parquets existen y son legibles
    expected = ["resumen_nacional", "por_departamento", "hall_of_shame", "por_funcion"]
    all_ok = True
    for name in expected:
        p = PROCESSED / f"{name}.parquet"
        if not p.exists():
            logger.error("Parquet faltante: %s", p)
            all_ok = False
            continue
        try:
            pl.read_parquet(p)
            logger.info("✓ %s.parquet  (%d bytes)", name, p.stat().st_size)
        except Exception as exc:
            logger.error("✗ %s.parquet corrupto: %s", name, exc)
            all_ok = False

    if not all_ok:
        logger.error("Executor: algunos Parquets fallaron. Revisa el log.")
        sys.exit(1)

    logger.info("Executor completado. Parquets listos en %s", PROCESSED)
    logger.info("Ahora puedes correr: python -m src.run_skill evaluator")


# ══════════════════════════════════════════════════════════════════════════════
# EVALUATOR
# ══════════════════════════════════════════════════════════════════════════════

class _Check:
    def __init__(self, nombre: str):
        self.nombre = nombre
        self.estado: str = "PASS"   # PASS | WARN | FAIL
        self.hallazgos: list[str] = []

    def warn(self, msg: str) -> None:
        self.estado = "WARN"
        self.hallazgos.append(msg)

    def fail(self, msg: str) -> None:
        self.estado = "FAIL"
        self.hallazgos.append(msg)

    def ok(self, msg: str) -> None:
        self.hallazgos.append(msg)

    @property
    def emoji(self) -> str:
        return {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[self.estado]


def run_evaluator() -> None:
    """Audita los Parquets y genera data/processed/qa_report.md."""
    logger.info("═" * 60)
    logger.info("EVALUATOR  |  Iniciando auditoría de calidad")
    logger.info("═" * 60)

    checks: list[_Check] = []
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Cargar datos ──────────────────────────────────────────────────────────
    resumen = load_resumen_nacional()
    df_depto_pl = load_por_departamento()
    df_hall_pl = load_hall_of_shame()
    df_func_pl = load_por_funcion()

    if any(x is None for x in [resumen, df_depto_pl, df_hall_pl, df_func_pl]):
        logger.error("No se pueden leer los Parquets. Corre el Executor primero.")
        sys.exit(1)

    df_depto = df_depto_pl.to_pandas()
    df_hall = df_hall_pl.to_pandas()
    df_func = df_func_pl.to_pandas()

    periodo = resumen.get("periodo", "desconocido")

    # ── CHECK 1: Completitud territorial ─────────────────────────────────────
    c1 = _Check("Completitud territorial (25 departamentos)")
    deptos_en_datos = set(df_depto["DEPARTAMENTO_EJECUTORA_NOMBRE"].dropna().unique())
    faltantes = DEPARTAMENTOS_PERU - deptos_en_datos
    extras = deptos_en_datos - DEPARTAMENTOS_PERU
    if faltantes:
        c1.fail(f"Departamentos faltantes ({len(faltantes)}): {sorted(faltantes)}")
    else:
        c1.ok(f"Los 25 departamentos del Perú están presentes.")
    if extras:
        c1.warn(f"Departamentos no esperados ({len(extras)}): {sorted(extras)}")
    checks.append(c1)

    # ── CHECK 2: Rango de avance % ────────────────────────────────────────────
    c2 = _Check("Rango de avance % [0%, 100%]")
    for label, df in [("por_departamento", df_depto), ("hall_of_shame", df_hall), ("por_funcion", df_func)]:
        col = "avance_pct"
        if col not in df.columns:
            continue
        validos = df[col].dropna()
        negativos = (validos < 0).sum()
        excede_100 = (validos > 100).sum()
        if negativos:
            c2.fail(f"{label}: {negativos} registros con avance % negativo")
        if excede_100:
            c2.warn(f"{label}: {excede_100} registros con avance % > 100%")
        if not negativos and not excede_100:
            c2.ok(f"{label}: todos los valores en [0%, 100%]  (min={validos.min():.1f}%, max={validos.max():.1f}%)")
    checks.append(c2)

    # ── CHECK 3: PIM=0 con devengado positivo ─────────────────────────────────
    c3 = _Check("Consistencia PIM vs Devengado")
    for label, df in [("por_departamento", df_depto), ("hall_of_shame", df_hall)]:
        pim_col = "MONTO_PIM"
        dev_col = "DEVENGADO_PERIODO"
        if pim_col not in df.columns or dev_col not in df.columns:
            continue
        anomalos = df[(df[pim_col] <= 0) & (df[dev_col] > 0)]
        if len(anomalos):
            c3.fail(f"{label}: {len(anomalos)} filas con PIM≤0 pero devengado>0")
        else:
            c3.ok(f"{label}: ningún caso de devengado>PIM nulo/cero")
        # Devengado mayor que PIM (significativo: >1%)
        exceso = df[(df[pim_col] > 0) & (df[dev_col] > df[pim_col] * 1.01)]
        if len(exceso):
            c3.warn(f"{label}: {len(exceso)} filas con devengado > PIM (+1% tolerancia)")
    checks.append(c3)

    # ── CHECK 4: Nulos en columnas clave ──────────────────────────────────────
    c4 = _Check("Valores nulos en columnas clave")
    for label, df, cols in [
        ("resumen_nacional", None, ["total_pim", "total_devengado", "avance_pct_nacional"]),
        ("por_departamento", df_depto, ["DEPARTAMENTO_EJECUTORA_NOMBRE", "MONTO_PIM", "DEVENGADO_PERIODO"]),
        ("hall_of_shame", df_hall, ["EJECUTORA_NOMBRE", "MONTO_PIM", "avance_pct"]),
    ]:
        if label == "resumen_nacional":
            nulos = [c for c in cols if resumen.get(c) is None]
        else:
            nulos = [c for c in cols if c in df.columns and df[c].isna().any()]
        if nulos:
            c4.warn(f"{label}: nulos en {nulos}")
        else:
            c4.ok(f"{label}: sin nulos en columnas clave")
    checks.append(c4)

    # ── CHECK 5: Hall of Shame — solo PIM > 10M ───────────────────────────────
    c5 = _Check("Hall of Shame — umbral PIM > 10M")
    if "MONTO_PIM" in df_hall.columns:
        bajo_umbral = (df_hall["MONTO_PIM"] <= 10_000_000).sum()
        if bajo_umbral:
            c5.fail(f"{bajo_umbral} ejecutoras en hall_of_shame con PIM ≤ S/ 10M")
        else:
            c5.ok(f"Todas las {len(df_hall):,} ejecutoras tienen PIM > S/ 10M")
    checks.append(c5)

    # ── CHECK 6: Duplicados en Hall of Shame ──────────────────────────────────
    c6 = _Check("Duplicados de ejecutora en Hall of Shame")
    if "EJECUTORA_NOMBRE" in df_hall.columns:
        total = len(df_hall)
        unicos = df_hall["EJECUTORA_NOMBRE"].nunique()
        dupes = total - unicos
        if dupes > 0:
            c6.warn(f"{dupes} nombres de ejecutora duplicados ({total} filas, {unicos} únicos)")
        else:
            c6.ok(f"Sin duplicados: {unicos:,} ejecutoras únicas")
    checks.append(c6)

    # ── CHECK 7: Cuadratura resumen nacional vs suma de departamentos ──────────
    c7 = _Check("Cuadratura: suma departamentos vs resumen nacional")
    suma_pim_deptos = df_depto["MONTO_PIM"].sum()
    suma_dev_deptos = df_depto["DEVENGADO_PERIODO"].sum()
    pim_nac = resumen["total_pim"]
    dev_nac = resumen["total_devengado"]
    tol_pct = 0.01  # 1% tolerancia por redondeos
    diff_pim = abs(suma_pim_deptos - pim_nac) / max(pim_nac, 1)
    diff_dev = abs(suma_dev_deptos - dev_nac) / max(dev_nac, 1)
    if diff_pim > tol_pct:
        c7.fail(f"PIM: suma deptos S/ {suma_pim_deptos:,.0f} ≠ resumen S/ {pim_nac:,.0f} (diff {diff_pim*100:.2f}%)")
    else:
        c7.ok(f"PIM cuadra: diff = {diff_pim*100:.4f}%  (S/ {pim_nac/1e9:.3f}B)")
    if diff_dev > tol_pct:
        c7.fail(f"Devengado: suma deptos S/ {suma_dev_deptos:,.0f} ≠ resumen S/ {dev_nac:,.0f} (diff {diff_dev*100:.2f}%)")
    else:
        c7.ok(f"Devengado cuadra: diff = {diff_dev*100:.4f}%  (S/ {dev_nac/1e9:.3f}B)")
    checks.append(c7)

    # ── CHECK 8: Estadísticas de cobertura del Hall of Shame ──────────────────
    c8 = _Check("Cobertura del Hall of Shame")
    n_ejecutoras_nac = resumen.get("num_ejecutoras", 0)
    n_hall = len(df_hall)
    pct_hall = n_hall / n_ejecutoras_nac * 100 if n_ejecutoras_nac else 0
    saldo_hall = df_hall["saldo_no_devengado"].sum() if "saldo_no_devengado" in df_hall.columns else 0
    c8.ok(
        f"{n_hall:,} ejecutoras con PIM>10M de {n_ejecutoras_nac:,} totales "
        f"({pct_hall:.1f}%). "
        f"Capital paralizado acumulado: S/ {saldo_hall/1e9:.2f}B"
    )
    checks.append(c8)

    # ── Generar reporte markdown ───────────────────────────────────────────────
    n_pass = sum(1 for c in checks if c.estado == "PASS")
    n_warn = sum(1 for c in checks if c.estado == "WARN")
    n_fail = sum(1 for c in checks if c.estado == "FAIL")

    global_estado = "✅ APROBADO" if n_fail == 0 else "❌ CON FALLOS"
    if n_fail == 0 and n_warn > 0:
        global_estado = "⚠️ APROBADO CON ADVERTENCIAS"

    lines: list[str] = [
        "# Reporte de Calidad de Datos (QA)",
        "",
        f"**Generado:** {generated_at}  ",
        f"**Periodo auditado:** `{periodo}`  ",
        f"**Resultado global:** {global_estado}  ",
        "",
        f"| ✅ Pasados | ⚠️ Advertencias | ❌ Fallos |",
        f"|-----------|----------------|---------|",
        f"| {n_pass} | {n_warn} | {n_fail} |",
        "",
        "---",
        "",
        "## Métricas del conjunto de datos",
        "",
        f"| Métrica | Valor |",
        f"|---------|-------|",
        f"| PIM total subnacional | S/ {resumen['total_pim']/1e9:.2f}B |",
        f"| Devengado total | S/ {resumen['total_devengado']/1e9:.2f}B |",
        f"| Avance % nacional | {resumen.get('avance_pct_nacional') or 0:.1f}% |",
        f"| Saldo no devengado | S/ {resumen['saldo_no_devengado']/1e9:.2f}B |",
        f"| Ejecutoras procesadas | {resumen.get('num_ejecutoras', 0):,} |",
        f"| Departamentos | {resumen.get('num_departamentos', 0)} |",
        f"| Funciones de gasto | {len(df_func)} |",
        f"| Ejecutoras en Hall of Shame (PIM>10M) | {len(df_hall):,} |",
        "",
        "---",
        "",
        "## Detalle de verificaciones",
        "",
    ]

    for c in checks:
        lines.append(f"### {c.emoji} {c.nombre}")
        lines.append(f"**Estado:** {c.estado}")
        lines.append("")
        for h in c.hallazgos:
            lines.append(f"- {h}")
        lines.append("")

    lines += [
        "---",
        "",
        "*Reporte generado automáticamente por `src/run_skill.py evaluator`.*",
        f"*Fuente: Portal de Datos Abiertos MEF — Gasto Devengado Mensual 2025.*",
    ]

    qa_path = PROCESSED / "qa_report.md"
    qa_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("qa_report.md → %s", qa_path)

    # Resumen en consola
    logger.info("═" * 60)
    logger.info("RESULTADO: %s  (PASS=%d  WARN=%d  FAIL=%d)", global_estado, n_pass, n_warn, n_fail)
    for c in checks:
        logger.info("  %s %-50s", c.emoji, c.nombre)
        for h in c.hallazgos:
            logger.info("      → %s", h)
    logger.info("═" * 60)
    logger.info("Reporte completo: %s", qa_path)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.run_skill",
        description="Dual-skill CLI para el sistema MEF Subnacional 2025.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="skill", required=True)

    # ── executor ──────────────────────────────────────────────────────────────
    p_exec = sub.add_parser(
        "executor",
        help="Procesa el CSV y genera/actualiza los Parquets en data/processed/",
    )
    p_exec.add_argument(
        "--periodo", default="2025", metavar="PERIODO",
        help=f"Periodo a procesar. Valores válidos: {PERIODOS_DOC}",
    )
    p_exec.add_argument(
        "--force-download", action="store_true",
        help="Re-descarga el CSV aunque ya exista en caché",
    )

    # ── evaluator ─────────────────────────────────────────────────────────────
    sub.add_parser(
        "evaluator",
        help="Audita los Parquets existentes y genera data/processed/qa_report.md",
    )

    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.skill == "executor":
        run_executor(periodo=args.periodo, force_download=args.force_download)
    elif args.skill == "evaluator":
        run_evaluator()
