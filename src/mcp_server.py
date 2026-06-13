"""Local MCP Server — Portal Nacional de Datos Abiertos del Perú (DKAN).

Expone 5 herramientas via FastMCP/stdio.  Probado contra el portal real:
  - CKAN compat layer (/api/3/action/*)  →  package_list y package_show funcionan.
  - package_search redirige a HTML           →  se busca filtrando package_list.
  - DKAN native datastore (/api/action/*)   →  retorna 500 para la mayoría de recursos.
  - Acceso directo a CSV/PDF por URL        →  funciona vía streaming.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from src.utils import get_logger

# ── Constantes ─────────────────────────────────────────────────────────────
BASE_URL = "https://www.datosabiertos.gob.pe"
CKAN_BASE = f"{BASE_URL}/api/3/action"
DKAN_BASE = f"{BASE_URL}/api/action"

RAW_PDFS_DIR = Path(__file__).parent.parent / "data" / "raw_pdfs"
RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT = httpx.Timeout(20.0)
MAX_CSV_BYTES = 256 * 1024   # 256 KB máximo para inspección de esquema

logger = get_logger("mcp_server")
mcp = FastMCP("mef-datos-abiertos")


# ── Helper interno ──────────────────────────────────────────────────────────

def _smart_get(ckan_path: str, dkan_path: str, params: dict[str, Any]) -> dict:
    """Intenta CKAN primero; si falla, intenta DKAN nativo.

    Retorna el dict JSON parseado o lanza RuntimeError con contexto.
    """
    for label, url in [("CKAN", f"{CKAN_BASE}/{ckan_path}"),
                       ("DKAN", f"{DKAN_BASE}/{dkan_path}")]:
        try:
            r = httpx.get(url, params=params, timeout=TIMEOUT, follow_redirects=False)
            if r.status_code in (301, 302):
                logger.debug("%s %s → redirect (%s), skipping", label, url, r.status_code)
                continue
            if r.status_code >= 400:
                logger.debug("%s %s → HTTP %s, skipping", label, url, r.status_code)
                continue
            data = r.json()
            if not isinstance(data, dict):
                logger.debug("%s returned non-dict JSON, skipping", label)
                continue
            if not data.get("success", True):   # CKAN false-success
                logger.debug("%s success=false, skipping", label)
                continue
            logger.info("Endpoint activo: %s → %s", label, url)
            return data
        except Exception as exc:
            logger.debug("%s %s error: %s", label, url, exc)

    raise RuntimeError(
        f"Ningún endpoint respondió para ckan='{ckan_path}' dkan='{dkan_path}'"
    )


def _normalize_pkg(raw: Any) -> dict:
    """DKAN envuelve el resultado en una lista; lo normaliza a dict."""
    if isinstance(raw, list):
        return raw[0] if raw else {}
    return raw if isinstance(raw, dict) else {}


# ── Herramientas MCP ────────────────────────────────────────────────────────

@mcp.tool()
def buscar_datasets(query: str, rows: int = 10) -> list[dict]:
    """Busca datasets en el Portal de Datos Abiertos del Perú por palabra clave.

    Estrategia: descarga la lista completa de slugs (package_list) y filtra
    por coincidencia en el nombre; luego llama a package_show para obtener el
    título real y el número de recursos.

    Args:
        query: Término de búsqueda (ej. "presupuesto", "mef", "educacion").
        rows:  Máximo de resultados a devolver (default 10).

    Returns:
        Lista de dicts con keys: slug, title, num_resources.
    """
    logger.info("buscar_datasets: query='%s' rows=%d", query, rows)

    # 1. Obtener lista completa de slugs
    data = _smart_get("package_list", "package/list.json", {})
    all_names: list[str] = data.get("result", [])
    logger.debug("package_list total: %d", len(all_names))

    # 2. Filtrar por keyword en el slug
    q = query.lower()
    matches = [n for n in all_names if q in n.lower()]
    logger.info("Coincidencias en slugs: %d", len(matches))

    # 3. Enriquecer con package_show (hasta `rows` coincidencias)
    results = []
    for slug in matches[:rows]:
        try:
            detail = _smart_get(
                f"package_show", "package/show.json",
                {"id": slug},
            )
            pkg = _normalize_pkg(detail.get("result"))
            results.append({
                "slug": pkg.get("name", slug),
                "title": pkg.get("title", slug),
                "num_resources": len(pkg.get("resources", [])),
            })
        except Exception as exc:
            logger.warning("No se pudo obtener detalle de '%s': %s", slug, exc)
            results.append({"slug": slug, "title": slug, "num_resources": None})

    logger.info("buscar_datasets devuelve %d resultados", len(results))
    return results


@mcp.tool()
def obtener_detalle_dataset(dataset_id: str) -> dict:
    """Devuelve metadata y lista de recursos de un dataset.

    Args:
        dataset_id: Slug o UUID del dataset
                    (ej. "ejecucion-presupuesto-2020-pliego-013").

    Returns:
        Dict con keys: title, description (truncada), resources.
        Cada resource incluye: resource_id, nombre, formato, url.
    """
    logger.info("obtener_detalle_dataset: id='%s'", dataset_id)

    data = _smart_get("package_show", "package/show.json", {"id": dataset_id})
    pkg = _normalize_pkg(data.get("result"))

    resources = []
    for r in pkg.get("resources", []):
        resources.append({
            "resource_id": r.get("id"),
            "nombre": r.get("name") or r.get("description", "")[:80],
            "formato": r.get("format", "").upper(),
            "url": r.get("url", ""),
        })

    desc_raw = pkg.get("notes", "")
    # Eliminar tags HTML básicos para el resumen
    import re
    desc = re.sub(r"<[^>]+>", " ", desc_raw).strip()[:300]

    return {
        "title": pkg.get("title", dataset_id),
        "description": desc,
        "resources": resources,
    }


@mcp.tool()
def inspeccionar_esquema_csv(resource_id: str = "", url: str = "") -> dict:
    """Devuelve headers y 5 filas de muestra de un CSV sin descargar el archivo completo.

    Prioridad: si se da `url`, la usa directamente (más confiable en este portal
    porque el datastore DKAN nativo retorna 500 para la mayoría de recursos).
    Si solo se da `resource_id`, intenta el datastore DKAN; si falla, retorna
    instrucción para usar `url`.

    Args:
        resource_id: UUID del recurso (opcional).
        url:         URL directa al archivo CSV (opcional, preferida).

    Returns:
        Dict con keys: headers (list[str]), sample_rows (list[list]), fuente.
    """
    if not resource_id and not url:
        return {"error": "Debes proveer resource_id o url."}

    # ── Intento 1: URL directa (streaming, solo primeros MAX_CSV_BYTES) ──────
    if url:
        logger.info("inspeccionar_esquema_csv vía URL directa: %s", url[:80])
        try:
            with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=True) as resp:
                resp.raise_for_status()
                buf = io.BytesIO()
                for chunk in resp.iter_bytes(chunk_size=8192):
                    buf.write(chunk)
                    if buf.tell() >= MAX_CSV_BYTES:
                        break
            text = buf.getvalue().decode("utf-8", errors="replace")
            return _parse_csv_sample(text, fuente="url_directa")
        except Exception as exc:
            logger.error("Error leyendo CSV desde URL: %s", exc)
            return {"error": str(exc)}

    # ── Intento 2: DKAN datastore (frecuentemente 500) ────────────────────────
    logger.info("inspeccionar_esquema_csv vía datastore: resource_id=%s", resource_id)
    for label, url_ds in [
        ("CKAN", f"{CKAN_BASE}/datastore_search"),
        ("DKAN", f"{DKAN_BASE}/datastore/search.json"),
    ]:
        try:
            r = httpx.get(
                url_ds,
                params={"resource_id": resource_id, "limit": 5},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
            if r.status_code == 200:
                d = r.json()
                records = d.get("result", {}).get("records", [])
                if records:
                    headers = list(records[0].keys())
                    rows = [list(rec.values()) for rec in records]
                    logger.info("inspeccionar_esquema_csv OK vía %s datastore", label)
                    return {"headers": headers, "sample_rows": rows, "fuente": f"{label}_datastore"}
        except Exception as exc:
            logger.debug("%s datastore error: %s", label, exc)

    return {
        "error": (
            "El datastore DKAN no está indexado para este resource_id. "
            "Usa el parámetro `url` con la URL directa del CSV obtenida de "
            "obtener_detalle_dataset()."
        ),
        "resource_id": resource_id,
    }


def _parse_csv_sample(text: str, fuente: str) -> dict:
    """Parsea las primeras 6 líneas de un CSV (headers + 5 filas)."""
    import csv, io as _io
    # Detectar separador: ; o ,
    first_line = text.split("\n")[0]
    sep = ";" if first_line.count(";") > first_line.count(",") else ","

    reader = csv.reader(_io.StringIO(text), delimiter=sep)
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) > 6:
            break

    if not rows:
        return {"error": "CSV vacío o no parseable", "fuente": fuente}

    headers = rows[0]
    sample = rows[1:6]
    return {"headers": headers, "sample_rows": sample, "fuente": fuente}


@mcp.tool()
def descargar_documento_1964(url: str) -> dict:
    """Descarga un PDF histórico (archivo 1964) a data/raw_pdfs/.

    Valida que el archivo sea un PDF real (magic bytes %PDF).
    Es la única herramienta que descarga un archivo completo.

    Args:
        url: URL directa al PDF.

    Returns:
        Dict con keys: ruta_local, nombre_archivo, tamanio_bytes, sha256.
    """
    logger.info("descargar_documento_1964: url=%s", url[:100])

    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(60.0), follow_redirects=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            buf = b""
            for chunk in resp.iter_bytes(chunk_size=65536):
                buf += chunk

        # Validar magic bytes PDF
        if not buf.startswith(b"%PDF"):
            return {
                "error": f"El archivo no parece ser un PDF (content-type={content_type}, "
                         f"primeros 8 bytes={buf[:8]!r})."
            }

        # Nombre de archivo desde URL
        url_path = url.split("?")[0].rstrip("/")
        nombre = url_path.split("/")[-1] or "documento_1964.pdf"
        if not nombre.lower().endswith(".pdf"):
            nombre += ".pdf"

        destino = RAW_PDFS_DIR / nombre
        destino.write_bytes(buf)

        sha = hashlib.sha256(buf).hexdigest()
        logger.info("PDF guardado: %s (%d bytes)", destino, len(buf))

        return {
            "ruta_local": str(destino),
            "nombre_archivo": nombre,
            "tamanio_bytes": len(buf),
            "sha256": sha,
        }

    except httpx.HTTPStatusError as exc:
        logger.error("HTTP error descargando PDF: %s", exc)
        return {"error": f"HTTP {exc.response.status_code}: {url}"}
    except Exception as exc:
        logger.error("Error descargando PDF: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
def obtener_ultimas_actualizaciones(rows: int = 10) -> list[dict]:
    """Lista los datasets modificados más recientemente en el portal.

    Usa current_package_list_with_resources que devuelve 1 dataset por llamada
    ordenado por fecha de modificación descendente.  Realiza `rows` llamadas.

    Args:
        rows: Cantidad de datasets recientes a devolver (default 10).

    Returns:
        Lista de dicts con keys: slug, title, num_resources, ultima_modificacion.
    """
    logger.info("obtener_ultimas_actualizaciones: rows=%d", rows)
    results = []

    for offset in range(rows):
        try:
            r = httpx.get(
                f"{CKAN_BASE}/current_package_list_with_resources",
                params={"limit": 1, "offset": offset},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
            if r.status_code != 200:
                logger.warning("offset=%d HTTP %s, deteniendo", offset, r.status_code)
                break
            data = r.json()
            raw_list = data.get("result", [])
            if not raw_list:
                break
            pkg = _normalize_pkg(raw_list[0])
            results.append({
                "slug": pkg.get("name", "?"),
                "title": pkg.get("title", "?"),
                "num_resources": len(pkg.get("resources", [])),
                "ultima_modificacion": pkg.get(
                    "revision_timestamp", pkg.get("metadata_modified", "?")
                ),
            })
        except Exception as exc:
            logger.warning("Error en offset=%d: %s", offset, exc)
            break

    logger.info("obtener_ultimas_actualizaciones devuelve %d items", len(results))
    return results


# ── Entrypoint ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Iniciando MCP server 'mef-datos-abiertos' vía stdio")
    mcp.run(transport="stdio")
