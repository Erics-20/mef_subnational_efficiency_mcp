"""Local worker scripts 2025 — Data Pipeline for MEF Presupuesto Subnacional.

Descarga el CSV de Gasto Devengado Mensual 2025 del MEF, filtra gobierno
subnacional (R=Regional, M=Local), calcula métricas y genera 4 Parquets
microscópicos en data/processed/ para la app Streamlit.

Uso:
    python -m src.data_pipeline [--periodo PERIODO] [--force-download]

Periodos:
    2025        Año completo — usa MONTO_DEVENGADO_ANUAL (default)
    2025-Q1     Q1: Enero + Febrero + Marzo
    2025-Q2     Q2: Abril + Mayo + Junio
    2025-Q3     Q3: Julio + Agosto + Septiembre
    2025-Q4     Q4: Octubre + Noviembre + Diciembre
    2025-06     Acumulado hasta Junio (Enero → Junio)
    2025-03     Acumulado hasta Marzo (solo Enero → Marzo)

Salidas en data/processed/:
    resumen_nacional.parquet   — KPIs nacionales (1 fila)
    por_departamento.parquet   — Agregado por departamento
    hall_of_shame.parquet      — Peores ejecutoras (PIM > 10M, % ascendente)
    por_funcion.parquet        — Bloques de gasto por función
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
import polars as pl

# ── Path bootstrap ─────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils import get_logger  # noqa: E402

logger = get_logger("data_pipeline")

# ── Constantes ──────────────────────────────────────────────────────────────
CSV_URL = (
    "https://fs.datosabiertos.mef.gob.pe/datastorefiles/2025-Gasto-Devengado-Mensual.csv"
)
CSV_CACHE = _ROOT / "data" / "raw" / "2025-Gasto-Devengado-Mensual.csv"
PROCESSED = _ROOT / "data" / "processed"
SNAPSHOTS = _ROOT / "data" / "snapshots"

PROCESSED.mkdir(exist_ok=True)
SNAPSHOTS.mkdir(exist_ok=True)

# Columnas a conservar de las 73 disponibles
KEEP_COLS = [
    "ANO_EJE",
    "NIVEL_GOBIERNO",
    "NIVEL_GOBIERNO_NOMBRE",
    "DEPARTAMENTO_EJECUTORA_NOMBRE",
    "EJECUTORA_NOMBRE",
    "PLIEGO_NOMBRE",
    "MONTO_PIM",
    "MONTO_DEVENGADO_ANUAL",
    "MONTO_DEVENGADO_ENERO",
    "MONTO_DEVENGADO_FEBRERO",
    "MONTO_DEVENGADO_MARZO",
    "MONTO_DEVENGADO_ABRIL",
    "MONTO_DEVENGADO_MAYO",
    "MONTO_DEVENGADO_JUNIO",
    "MONTO_DEVENGADO_JULIO",
    "MONTO_DEVENGADO_AGOSTO",
    "MONTO_DEVENGADO_SEPTIEMBRE",
    "MONTO_DEVENGADO_OCTUBRE",
    "MONTO_DEVENGADO_NOVIEMBRE",
    "MONTO_DEVENGADO_DICIEMBRE",
    "FUNCION_NOMBRE",
    "FUENTE_FINANCIAMIENTO_NOMBRE",
]

# Columnas de meses en orden calendárico — detectadas dinámicamente si no existen
MES_COLS = [c for c in KEEP_COLS if c.startswith("MONTO_DEVENGADO_") and c != "MONTO_DEVENGADO_ANUAL"]

QUARTER_MAP: dict[str, list[str]] = {
    "Q1": MES_COLS[0:3],   # ENE FEB MAR
    "Q2": MES_COLS[3:6],   # ABR MAY JUN
    "Q3": MES_COLS[6:9],   # JUL AGO SEP
    "Q4": MES_COLS[9:12],  # OCT NOV DIC
}


# ── Period parsing ──────────────────────────────────────────────────────────

def resolve_period(period: str) -> tuple[str, list[str]]:
    """Devuelve (label, columnas_a_sumar) para el periodo dado.

    Returns "MONTO_DEVENGADO_ANUAL" (lista de 1) cuando el periodo es anual,
    lo que señala que se debe usar la columna pre-calculada del CSV.
    """
    period = period.strip().upper()

    # Trimestre: "2025-Q1", "2025-Q2", etc.
    m = re.match(r"^\d{4}-Q([1-4])$", period)
    if m:
        q = f"Q{m.group(1)}"
        cols = QUARTER_MAP.get(q)
        if not cols:
            raise ValueError(f"Trimestre inválido: {q}")
        return period, cols

    # Mes acumulado: "2025-06", "2025-03", etc.
    m = re.match(r"^\d{4}-(\d{1,2})$", period)
    if m:
        month_num = int(m.group(1))
        if not 1 <= month_num <= 12:
            raise ValueError(f"Mes fuera de rango 1-12: {month_num}")
        return period, MES_COLS[:month_num]

    # Año completo: "2025"
    return period, ["MONTO_DEVENGADO_ANUAL"]


def build_devengado_sql(period_cols: list[str]) -> str:
    """Construye la expresión SQL que representa el devengado del periodo."""
    if period_cols == ["MONTO_DEVENGADO_ANUAL"]:
        return "COALESCE(TRY_CAST(MONTO_DEVENGADO_ANUAL AS DOUBLE), 0.0)"
    parts = [f"COALESCE(TRY_CAST({c} AS DOUBLE), 0.0)" for c in period_cols]
    return " + ".join(parts)


# ── Download ────────────────────────────────────────────────────────────────

def download_csv(force: bool = False, sample_mb: int = 0) -> Path:
    """Descarga el CSV en streaming a data/raw/. Cachea si ya existe.

    Args:
        force:     Re-descarga aunque el archivo exista.
        sample_mb: Si > 0, descarga solo los primeros N MB via HTTP Range
                   (modo demo). El archivo cacheado se nombra con el sufijo
                   _sampleNMB para no colisionar con la descarga completa.
    """
    CSV_CACHE.parent.mkdir(parents=True, exist_ok=True)

    if sample_mb > 0:
        dest = CSV_CACHE.parent / f"2025-Gasto-Devengado-Mensual_sample{sample_mb}MB.csv"
    else:
        dest = CSV_CACHE

    if dest.exists() and not force:
        size_mb = dest.stat().st_size / 1_048_576
        logger.info("CSV en caché: %s (%.1f MB) — omitiendo descarga", dest.name, size_mb)
        return dest

    total_size = 2_793_608_957  # bytes, verificado vía HEAD
    logger.info("Archivo total declarado: %.1f MB", total_size / 1_048_576)

    headers: dict[str, str] = {}
    if sample_mb > 0:
        byte_end = sample_mb * 1_048_576 - 1
        headers["Range"] = f"bytes=0-{byte_end}"
        logger.info("Modo SAMPLE: descargando primeros %d MB via Range request", sample_mb)
    else:
        logger.info("Descarga completa (~%.0f MB) — puede tardar varios minutos", total_size / 1_048_576)

    timeout = httpx.Timeout(connect=15.0, read=600.0, write=None, pool=None)
    downloaded = 0

    with httpx.stream("GET", CSV_URL, headers=headers, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        declared = int(resp.headers.get("content-length", 0))

        with open(dest, "wb") as fout:
            for chunk in resp.iter_bytes(chunk_size=1_048_576):
                fout.write(chunk)
                downloaded += len(chunk)
                if declared and downloaded % (50 * 1_048_576) < 1_048_576:
                    logger.info("  Descargado: %.0f / %.0f MB", downloaded / 1_048_576, declared / 1_048_576)

    # Truncar en el último salto de línea completo para no dejar línea cortada
    if sample_mb > 0:
        raw = dest.read_bytes()
        cut = raw.rfind(b"\n")
        if cut > 0:
            dest.write_bytes(raw[: cut + 1])

    size_mb = dest.stat().st_size / 1_048_576
    coverage = dest.stat().st_size / total_size * 100
    logger.info("Descarga lista: %.1f MB → %s  (%.1f%% del archivo completo)", size_mb, dest.name, coverage)
    return dest


# ── DuckDB loading ──────────────────────────────────────────────────────────

def load_filtered(csv_path: Path, devengado_sql: str) -> pl.DataFrame:
    """DuckDB lee solo las columnas útiles y filtra gobierno subnacional.

    Nunca carga las 73 columnas a memoria; proyecta solo las necesarias.
    """
    str_cols = [
        "ANO_EJE", "NIVEL_GOBIERNO", "NIVEL_GOBIERNO_NOMBRE",
        "DEPARTAMENTO_EJECUTORA_NOMBRE", "EJECUTORA_NOMBRE", "PLIEGO_NOMBRE",
        "FUNCION_NOMBRE", "FUENTE_FINANCIAMIENTO_NOMBRE",
    ]
    select_parts = [f'"{c}"' for c in str_cols]
    select_parts.append("TRY_CAST(MONTO_PIM AS DOUBLE) AS MONTO_PIM")
    select_parts.append(f"({devengado_sql}) AS DEVENGADO_PERIODO")
    select_clause = ",\n        ".join(select_parts)

    # DuckDB espera paths con forward-slashes incluso en Windows
    safe_path = str(csv_path).replace("\\", "/").replace("'", "''")

    sql = f"""
        SELECT
            {select_clause}
        FROM read_csv_auto(
            '{safe_path}',
            header=true,
            ignore_errors=true
        )
        WHERE NIVEL_GOBIERNO IN ('R', 'M')
    """

    logger.info("DuckDB: leyendo %s (solo columnas útiles)...", csv_path.name)
    con = duckdb.connect()
    df = con.execute(sql).pl()
    con.close()

    logger.info("Filas subnacionales (R + M): %s", f"{len(df):,}")
    return df


# ── Metrics ─────────────────────────────────────────────────────────────────

def add_row_metrics(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        pl.when(pl.col("MONTO_PIM") > 0)
          .then((pl.col("DEVENGADO_PERIODO") / pl.col("MONTO_PIM") * 100).round(2))
          .otherwise(None)
          .alias("avance_pct"),
        (pl.col("MONTO_PIM") - pl.col("DEVENGADO_PERIODO"))
          .alias("saldo_no_devengado"),
    ])


def _agg_metrics(df: pl.DataFrame) -> pl.DataFrame:
    """Añade avance_pct y saldo_no_devengado a un df ya agrupado con MONTO_PIM y DEVENGADO_PERIODO."""
    return df.with_columns([
        pl.when(pl.col("MONTO_PIM") > 0)
          .then((pl.col("DEVENGADO_PERIODO") / pl.col("MONTO_PIM") * 100).round(2))
          .otherwise(None)
          .alias("avance_pct"),
        (pl.col("MONTO_PIM") - pl.col("DEVENGADO_PERIODO")).alias("saldo_no_devengado"),
    ])


# ── Atomic write ────────────────────────────────────────────────────────────

def _write_parquet_atomic(df: pl.DataFrame, dest: Path) -> None:
    """Escribe un Parquet de forma atómica: tmp → os.replace → dest.

    os.replace en el mismo filesystem es atómico: los lectores (dashboard,
    analytical_engine) ven siempre el archivo anterior completo o el nuevo
    completo, nunca un estado intermedio a mitad de escritura.
    """
    tmp = dest.with_suffix(".parquet.tmp")
    try:
        df.write_parquet(tmp)
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Output generation ────────────────────────────────────────────────────────

def generate_outputs(df: pl.DataFrame, period_label: str) -> dict[str, int]:
    """Genera los 4 Parquets en data/processed/. Devuelve tamaños en bytes."""
    sizes: dict[str, int] = {}

    # ── 1. resumen_nacional ─────────────────────────────────────────────────
    total_pim = df["MONTO_PIM"].sum() or 0.0
    total_dev = df["DEVENGADO_PERIODO"].sum() or 0.0
    avance_nac = round(total_dev / total_pim * 100, 2) if total_pim > 0 else None

    resumen = pl.DataFrame({
        "periodo":              [period_label],
        "total_pim":            [total_pim],
        "total_devengado":      [total_dev],
        "avance_pct_nacional":  [avance_nac],
        "saldo_no_devengado":   [total_pim - total_dev],
        "num_ejecutoras":       [df["EJECUTORA_NOMBRE"].n_unique()],
        "num_departamentos":    [df["DEPARTAMENTO_EJECUTORA_NOMBRE"].n_unique()],
    })
    out = PROCESSED / "resumen_nacional.parquet"
    _write_parquet_atomic(resumen, out)
    sizes["resumen_nacional"] = out.stat().st_size
    logger.info("resumen_nacional.parquet → %d bytes", sizes["resumen_nacional"])

    # ── 2. por_departamento ─────────────────────────────────────────────────
    por_depto = (
        df.group_by("DEPARTAMENTO_EJECUTORA_NOMBRE")
        .agg([pl.col("MONTO_PIM").sum(), pl.col("DEVENGADO_PERIODO").sum()])
        .pipe(_agg_metrics)
        .sort("avance_pct", nulls_last=True)
    )
    out = PROCESSED / "por_departamento.parquet"
    _write_parquet_atomic(por_depto, out)
    sizes["por_departamento"] = out.stat().st_size
    logger.info("por_departamento.parquet → %d bytes  (%d deptos)", sizes["por_departamento"], len(por_depto))

    # ── 3. hall_of_shame ────────────────────────────────────────────────────
    hall = (
        df.group_by(["EJECUTORA_NOMBRE", "PLIEGO_NOMBRE",
                     "DEPARTAMENTO_EJECUTORA_NOMBRE", "NIVEL_GOBIERNO_NOMBRE"])
        .agg([pl.col("MONTO_PIM").sum(), pl.col("DEVENGADO_PERIODO").sum()])
        .pipe(_agg_metrics)
        .filter(pl.col("MONTO_PIM") > 10_000_000)
        .sort("avance_pct", nulls_last=True)
    )
    out = PROCESSED / "hall_of_shame.parquet"
    _write_parquet_atomic(hall, out)
    sizes["hall_of_shame"] = out.stat().st_size
    logger.info("hall_of_shame.parquet → %d bytes  (%d ejecutoras)", sizes["hall_of_shame"], len(hall))

    # ── 4. por_funcion ──────────────────────────────────────────────────────
    por_func = (
        df.group_by("FUNCION_NOMBRE")
        .agg([pl.col("MONTO_PIM").sum(), pl.col("DEVENGADO_PERIODO").sum()])
        .pipe(_agg_metrics)
        .sort("avance_pct", nulls_last=True)
    )
    out = PROCESSED / "por_funcion.parquet"
    _write_parquet_atomic(por_func, out)
    sizes["por_funcion"] = out.stat().st_size
    logger.info("por_funcion.parquet → %d bytes  (%d funciones)", sizes["por_funcion"], len(por_func))

    return sizes


# ── Schema snapshot ─────────────────────────────────────────────────────────

def save_schema_snapshot(df: pl.DataFrame, period_cols: list[str]) -> None:
    """Guarda data/snapshots/schema_candidato_A.json como evidencia de la estrategia."""
    snapshot = {
        "source": CSV_CACHE.name,
        "url": CSV_URL,
        "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
        "total_original_columns": 73,
        "selected_columns": KEEP_COLS,
        "period_columns_used": period_cols,
        "output_schema": {
            col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)
        },
        "sample_rows": df.head(3).to_dicts(),
    }
    out = SNAPSHOTS / "schema_candidato_A.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    logger.info("Schema snapshot → %s", out)


# ── Main ────────────────────────────────────────────────────────────────────

def run(periodo: str = "2025", force_download: bool = False, sample_mb: int = 0) -> dict[str, int]:
    period_label, period_cols = resolve_period(periodo)

    logger.info("═" * 60)
    logger.info("Pipeline MEF Subnacional 2025  |  Periodo: %s", period_label)
    if sample_mb:
        logger.info("MODO DEMO: primeros %d MB del CSV (archivo total ~2.66 GB)", sample_mb)
    logger.info("═" * 60)

    csv_path = download_csv(force=force_download, sample_mb=sample_mb)
    csv_size = csv_path.stat().st_size
    logger.info("CSV fuente: %.1f MB", csv_size / 1_048_576)

    devengado_sql = build_devengado_sql(period_cols)
    df = load_filtered(csv_path, devengado_sql)

    save_schema_snapshot(df, period_cols)

    sizes = generate_outputs(df, period_label)

    total_parquet = sum(sizes.values())
    reduction_pct = (1 - total_parquet / csv_size) * 100

    logger.info("═" * 60)
    logger.info("FOOTPRINT REDUCTION:")
    logger.info("  CSV origen      : %8.1f MB", csv_size / 1_048_576)
    logger.info("  Total Parquet   : %8.1f KB  (%.1f%% más pequeño)",
                total_parquet / 1024, reduction_pct)
    for name, size in sizes.items():
        logger.info("  %-28s %6.1f KB", f"{name}.parquet", size / 1024)
    logger.info("═" * 60)

    return sizes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline MEF — Genera Parquets subnacionales desde Gasto Devengado 2025",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--periodo", default="2025",
        metavar="PERIODO",
        help="2025 | 2025-Q1 | 2025-Q2 | 2025-06 | ... (default: 2025)",
    )
    parser.add_argument(
        "--force-download", action="store_true",
        help="Re-descarga el CSV aunque ya exista en caché",
    )
    parser.add_argument(
        "--sample-mb", type=int, default=0, metavar="N",
        help="Demo: descarga solo los primeros N MB via HTTP Range (ej. --sample-mb 100)",
    )
    args = parser.parse_args()
    run(periodo=args.periodo, force_download=args.force_download, sample_mb=args.sample_mb)
