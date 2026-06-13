"""Script de prueba manual para las 5 herramientas del MCP server.

Ejecutar desde la raíz del proyecto:
    python scripts/test_mcp.py

Cada test imprime PASS o FAIL con el resultado resumido.
"""

import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp_server import (
    buscar_datasets,
    obtener_detalle_dataset,
    inspeccionar_esquema_csv,
    descargar_documento_1964,
    obtener_ultimas_actualizaciones,
)

SEP = "─" * 60


def run(label: str, fn, *args, **kwargs):
    print(f"\n{SEP}")
    print(f"TEST: {label}")
    print(SEP)
    try:
        result = fn(*args, **kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
        print("✓ PASS")
        return result
    except Exception as exc:
        print(f"✗ FAIL: {exc}")
        return None


if __name__ == "__main__":
    # 1 — buscar_datasets
    res1 = run("buscar_datasets('presupuesto', rows=3)", buscar_datasets, "presupuesto", rows=3)

    # 2 — obtener_detalle_dataset (usa el primer slug del test anterior)
    slug = res1[0]["slug"] if res1 else "visitantes-sitios-arqueologicos"
    res2 = run(f"obtener_detalle_dataset('{slug}')", obtener_detalle_dataset, slug)

    # 3a — inspeccionar_esquema_csv vía URL directa
    csv_url = None
    if res2:
        for r in res2.get("resources", []):
            if r.get("formato", "").upper() in ("CSV", ""):
                csv_url = r.get("url")
                break
    if not csv_url:
        csv_url = "https://www.datosabiertos.gob.pe/sites/default/files/base%20de%20datos%20sitios_2.csv"
    run(f"inspeccionar_esquema_csv(url=...)", inspeccionar_esquema_csv, url=csv_url)

    # 3b — inspeccionar_esquema_csv vía resource_id (espera graceful error/fallback)
    res_id = res2["resources"][0]["resource_id"] if res2 and res2.get("resources") else "abc-123"
    run(f"inspeccionar_esquema_csv(resource_id='{res_id}')", inspeccionar_esquema_csv, resource_id=res_id)

    # 4 — descargar_documento_1964 (URL de prueba — reemplazar con PDF real del archivo 1964)
    # Usa un PDF público pequeño de muestra; sustituir por URL real del MEF.
    pdf_url = "https://www.w3.org/WAI/WCAG21/Techniques/pdf/pdf-sample.pdf"
    run(f"descargar_documento_1964(url='{pdf_url[:50]}...')", descargar_documento_1964, pdf_url)

    # 5 — obtener_ultimas_actualizaciones
    run("obtener_ultimas_actualizaciones(rows=3)", obtener_ultimas_actualizaciones, rows=3)

    print(f"\n{SEP}")
    print("Todos los tests ejecutados. Revisa logs/mcp_server.log para detalle.")
