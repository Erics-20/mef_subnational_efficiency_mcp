"""Capa de lectura sobre los Parquets generados por data_pipeline.

No vuelve a tocar el CSV gigante; solo lee data/processed/*.parquet.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.utils import get_logger

logger = get_logger("analytical_engine")

_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = _ROOT / "data" / "processed"


def _parquet_path(name: str) -> Path:
    return PROCESSED / f"{name}.parquet"


def _missing(name: str) -> None:
    logger.warning(
        "Parquet '%s' no encontrado en %s. Corre el pipeline primero: "
        "python -m src.data_pipeline --sample-mb 100",
        name,
        PROCESSED,
    )


# ── Public loaders ──────────────────────────────────────────────────────────

def load_resumen_nacional() -> dict | None:
    """Devuelve dict con KPIs nacionales o None si el Parquet no existe."""
    p = _parquet_path("resumen_nacional")
    if not p.exists():
        _missing("resumen_nacional")
        return None
    row = pl.read_parquet(p).row(0, named=True)
    return {
        "periodo":             row.get("periodo", "2025"),
        "total_pim":           row.get("total_pim", 0.0) or 0.0,
        "total_devengado":     row.get("total_devengado", 0.0) or 0.0,
        "avance_pct_nacional": row.get("avance_pct_nacional"),
        "saldo_no_devengado":  row.get("saldo_no_devengado", 0.0) or 0.0,
        "num_ejecutoras":      row.get("num_ejecutoras", 0),
        "num_departamentos":   row.get("num_departamentos", 0),
    }


def load_por_departamento() -> pl.DataFrame | None:
    """DataFrame con PIM, devengado, avance %, saldo por departamento."""
    p = _parquet_path("por_departamento")
    if not p.exists():
        _missing("por_departamento")
        return None
    return pl.read_parquet(p)


def load_hall_of_shame(
    min_pim: float = 10_000_000,
    top_n: int | None = None,
    departamento: str | None = None,
) -> pl.DataFrame | None:
    """Ejecutoras con PIM > min_pim ordenadas por avance % ascendente.

    Args:
        min_pim:      Umbral mínimo de PIM para incluir la ejecutora.
        top_n:        Limitar a las N peores. None = todas.
        departamento: Filtrar por nombre de departamento. None = todos.
    """
    p = _parquet_path("hall_of_shame")
    if not p.exists():
        _missing("hall_of_shame")
        return None
    df = pl.read_parquet(p)

    if min_pim != 10_000_000:
        df = df.filter(pl.col("MONTO_PIM") >= min_pim)

    if departamento and departamento != "Todos":
        df = df.filter(pl.col("DEPARTAMENTO_EJECUTORA_NOMBRE") == departamento)

    df = df.sort("avance_pct", nulls_last=True)

    if top_n is not None:
        df = df.head(top_n)

    return df


def load_por_funcion() -> pl.DataFrame | None:
    """DataFrame con PIM, devengado, avance %, saldo por función."""
    p = _parquet_path("por_funcion")
    if not p.exists():
        _missing("por_funcion")
        return None
    return pl.read_parquet(p)
