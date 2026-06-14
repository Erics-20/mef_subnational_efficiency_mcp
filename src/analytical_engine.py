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


def _read_parquet_safe(name: str) -> pl.DataFrame | None:
    """Lee un Parquet con manejo de archivo corrupto o en regeneración.

    Devuelve None (en lugar de propagar la excepción) si el archivo no existe
    o está corrupto — por ejemplo, si el pipeline está escribiendo en ese momento.
    Con escritura atómica en data_pipeline esto ya no debería ocurrir, pero
    esta capa defensiva evita que el dashboard crashee en cualquier caso borde.
    """
    p = _parquet_path(name)
    if not p.exists():
        _missing(name)
        return None
    try:
        return pl.read_parquet(p)
    except Exception as exc:
        logger.warning(
            "Parquet '%s' no se pudo leer (%s). "
            "Puede estar en regeneración — reintenta en unos segundos.",
            name,
            exc,
        )
        return None


# ── Public loaders ──────────────────────────────────────────────────────────

def load_resumen_nacional() -> dict | None:
    """Devuelve dict con KPIs nacionales o None si el Parquet no existe o está en regeneración."""
    df = _read_parquet_safe("resumen_nacional")
    if df is None:
        return None
    row = df.row(0, named=True)
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
    return _read_parquet_safe("por_departamento")


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
    df = _read_parquet_safe("hall_of_shame")
    if df is None:
        return None

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
    return _read_parquet_safe("por_funcion")
