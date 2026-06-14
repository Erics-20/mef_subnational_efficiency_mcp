"""Dashboard MEF — Eficiencia del Gasto Subnacional 2025.

Consume únicamente los Parquets de data/processed/ vía analytical_engine.
Levantarlo: streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ── Path bootstrap ─────────────────────────────────────────────────────────
import sys
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytical_engine import (  # noqa: E402
    load_hall_of_shame,
    load_por_departamento,
    load_por_funcion,
    load_resumen_nacional,
)

# ── Configuración de página ─────────────────────────────────────────────────
st.set_page_config(
    page_title="MEF — Eficiencia del Gasto Subnacional 2025",
    page_icon="🇵🇪",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Fondo general más sobrio */
    .stApp { background-color: #f5f6fa; }

    /* Encabezado del dashboard */
    .dash-header {
        background: linear-gradient(135deg, #6C1A1A 0%, #B22222 100%);
        color: white;
        padding: 1.4rem 2rem;
        border-radius: 8px;
        margin-bottom: 1.5rem;
    }
    .dash-header h1 { margin: 0; font-size: 1.7rem; font-weight: 700; }
    .dash-header p  { margin: 0.3rem 0 0; font-size: 0.95rem; opacity: 0.88; }

    /* Tarjetas de métricas */
    [data-testid="stMetric"] {
        background: white;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }
    [data-testid="stMetricLabel"] { font-size: 0.82rem; color: #6b7280; font-weight: 600; }
    [data-testid="stMetricValue"] { font-size: 1.5rem; color: #111827; }

    /* Aviso de pipeline faltante */
    .pipeline-warning {
        background: #fffbeb;
        border-left: 4px solid #f59e0b;
        padding: 1rem 1.2rem;
        border-radius: 4px;
        margin: 0.8rem 0;
        font-size: 0.92rem;
        color: #78350f;
    }

    /* ── Tabs: botones — siempre mostrar label completo ── */
    /* Acotado a tab-list para NO afectar el contenido de los paneles */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        overflow-x: auto;
        flex-wrap: nowrap;
    }
    .stTabs [data-baseweb="tab-list"] [data-baseweb="tab"] {
        padding: 0.5rem 1.2rem !important;
        border-radius: 6px 6px 0 0;
        font-weight: 600;
        white-space: nowrap !important;
        min-width: fit-content !important;
        overflow: visible !important;
    }
    /* Solo el texto dentro del botón del tab (p y span del label) */
    .stTabs [data-baseweb="tab-list"] [data-baseweb="tab"] p,
    .stTabs [data-baseweb="tab-list"] [data-baseweb="tab"] span {
        visibility: visible !important;
        opacity: 1 !important;
        overflow: visible !important;
        white-space: nowrap !important;
        max-width: none !important;
        clip: auto !important;
        clip-path: none !important;
    }

    /* ── Paneles de contenido: texto oscuro legible ── */
    /* Restaura color y wrapping para TODO el contenido dentro de los tabs */
    [data-baseweb="tab-panel"],
    [data-testid="stTabsContent"] {
        color: #1f2937 !important;
    }
    [data-baseweb="tab-panel"] p,
    [data-baseweb="tab-panel"] span,
    [data-baseweb="tab-panel"] div,
    [data-baseweb="tab-panel"] h1,
    [data-baseweb="tab-panel"] h2,
    [data-baseweb="tab-panel"] h3,
    [data-baseweb="tab-panel"] h4,
    [data-baseweb="tab-panel"] td,
    [data-baseweb="tab-panel"] th,
    [data-baseweb="tab-panel"] li,
    [data-testid="stTabsContent"] p,
    [data-testid="stTabsContent"] span,
    [data-testid="stTabsContent"] div {
        color: inherit !important;
        opacity: 1 !important;
        white-space: normal !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Utilidades de formato ───────────────────────────────────────────────────

def fmt_millones(val: float | None, decimales: int = 1) -> str:
    """Formatea un monto en Soles a millones con separadores de miles."""
    if val is None:
        return "—"
    m = val / 1_000_000
    if abs(m) >= 1_000:
        return f"S/ {m:,.0f} M"
    return f"S/ {m:,.{decimales}f} M"


def fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1f} %"


def _aviso_pipeline() -> None:
    st.markdown(
        '<div class="pipeline-warning">'
        "⚠️ <strong>Datos no disponibles.</strong> "
        "Ejecuta el pipeline primero:<br>"
        "<code>python -m src.data_pipeline --sample-mb 100</code>"
        "</div>",
        unsafe_allow_html=True,
    )


# ── Carga de datos (cacheada) ───────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Cargando datos...")
def _get_resumen():
    return load_resumen_nacional()


@st.cache_data(ttl=3600, show_spinner="Cargando datos territoriales...")
def _get_departamentos():
    df = load_por_departamento()
    return df.to_pandas() if df is not None else None


@st.cache_data(ttl=3600, show_spinner="Cargando hall of shame...")
def _get_hall(min_pim: float, departamento: str | None):
    df = load_hall_of_shame(min_pim=min_pim, departamento=departamento)
    return df.to_pandas() if df is not None else None


@st.cache_data(ttl=3600, show_spinner="Cargando funciones...")
def _get_funciones():
    df = load_por_funcion()
    return df.to_pandas() if df is not None else None


# ── Encabezado ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="dash-header">
        <h1>🇵🇪 Eficiencia del Gasto Subnacional 2025</h1>
        <p>Ministerio de Economía y Finanzas · Presupuesto Institucional Modificado vs. Devengado</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Tabs ────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Resumen Nacional 2025",
    "🗺️ Distribución Territorial",
    "🚨 Hall of Shame",
    "🔍 Reporte de Calidad / QA",
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — RESUMEN NACIONAL
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    kpis = _get_resumen()

    if kpis is None:
        _aviso_pipeline()
    else:
        periodo = kpis.get("periodo", "2025")
        st.caption(f"Periodo de datos: **{periodo}** | Fuente: Portal de Datos Abiertos MEF")

        # ── Fila de métricas
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("PIM Total", fmt_millones(kpis["total_pim"]))
        with c2:
            st.metric("Devengado Total", fmt_millones(kpis["total_devengado"]))
        with c3:
            av = kpis.get("avance_pct_nacional")
            st.metric("Avance % Nacional", fmt_pct(av))
        with c4:
            st.metric("Saldo No Devengado", fmt_millones(kpis["saldo_no_devengado"]))

        st.markdown("---")

        # ── Gráfico: Devengado vs Saldo (donut)
        col_g, col_info = st.columns([2, 1])
        with col_g:
            dev = kpis["total_devengado"]
            saldo = kpis["saldo_no_devengado"]
            avance = kpis.get("avance_pct_nacional") or 0.0

            fig_donut = go.Figure(go.Pie(
                labels=["Devengado", "Saldo No Devengado"],
                values=[max(dev, 0), max(saldo, 0)],
                hole=0.55,
                marker_colors=["#16a34a", "#dc2626"],
                textinfo="percent",
                hovertemplate="%{label}<br>S/ %{value:,.0f}<extra></extra>",
            ))
            fig_donut.update_layout(
                title_text="Ejecución del PIM Subnacional 2025",
                title_font_size=15,
                showlegend=True,
                height=340,
                margin=dict(t=50, b=10, l=10, r=10),
                annotations=[dict(
                    text=f"<b>{avance:.1f}%</b>",
                    x=0.5, y=0.5,
                    font_size=26,
                    font_color="#111827",
                    showarrow=False,
                )],
            )
            st.plotly_chart(fig_donut, width="stretch")

        with col_info:
            st.markdown("### Resumen ejecutivo")
            st.markdown(
                f"- **Ejecutoras**: {kpis.get('num_ejecutoras', '—'):,}\n"
                f"- **Departamentos**: {kpis.get('num_departamentos', '—')}\n"
            )
            st.markdown(
                f"El presupuesto subnacional ejecutado al cierre del periodo **{periodo}** "
                f"alcanza el **{fmt_pct(kpis.get('avance_pct_nacional'))}** del PIM total. "
                f"Quedan **{fmt_millones(kpis['saldo_no_devengado'])}** sin ejecutar."
            )

            # Barra de progreso
            if avance:
                st.progress(min(avance / 100, 1.0))


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — DISTRIBUCIÓN TERRITORIAL
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    df_depto = _get_departamentos()

    if df_depto is None:
        _aviso_pipeline()
    else:
        st.caption("Avance de ejecución por departamento — Gobierno Regional y Local 2025")

        # Bar chart horizontal (fallback robusto)
        df_sorted = df_depto.sort_values("avance_pct", ascending=True, na_position="last")

        # Color escala: rojo → amarillo → verde
        def _color(pct):
            if pct is None or pd.isna(pct):
                return "#9ca3af"
            if pct < 30:
                return "#dc2626"
            if pct < 60:
                return "#f59e0b"
            return "#16a34a"

        colors = [_color(v) for v in df_sorted["avance_pct"]]

        fig_bar = go.Figure(go.Bar(
            x=df_sorted["avance_pct"].fillna(0),
            y=df_sorted["DEPARTAMENTO_EJECUTORA_NOMBRE"],
            orientation="h",
            marker_color=colors,
            text=[fmt_pct(v) for v in df_sorted["avance_pct"]],
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Avance: %{x:.1f}%<br>"
                "<extra></extra>"
            ),
        ))
        # Calcular margen izquierdo según el nombre de departamento más largo
        max_label_len = df_sorted["DEPARTAMENTO_EJECUTORA_NOMBRE"].str.len().max() or 10
        left_margin = max(160, int(max_label_len * 7.5))

        fig_bar.add_vline(x=50, line_dash="dot", line_color="#6b7280", annotation_text="50%")
        fig_bar.update_layout(
            title="Avance de Ejecución por Departamento (%)",
            xaxis_title="Avance %",
            yaxis_title="",
            xaxis=dict(range=[0, max(df_sorted["avance_pct"].fillna(0).max() * 1.15, 60)]),
            yaxis=dict(automargin=True),
            height=max(450, len(df_sorted) * 28),
            margin=dict(t=50, b=30, l=left_margin, r=90),
            plot_bgcolor="white",
        )
        st.plotly_chart(fig_bar, width="stretch")

        st.markdown("---")
        st.markdown("#### Tabla detallada por departamento")

        display_df = df_depto.copy()
        display_df = display_df.rename(columns={
            "DEPARTAMENTO_EJECUTORA_NOMBRE": "Departamento",
            "MONTO_PIM": "PIM (S/)",
            "DEVENGADO_PERIODO": "Devengado (S/)",
            "avance_pct": "Avance %",
            "saldo_no_devengado": "Saldo No Devengado (S/)",
        })

        for col in ["PIM (S/)", "Devengado (S/)", "Saldo No Devengado (S/)"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(
                    lambda v: f"S/ {v:,.0f}" if pd.notna(v) else "—"
                )
        if "Avance %" in display_df.columns:
            display_df["Avance %"] = display_df["Avance %"].apply(
                lambda v: f"{v:.1f}%" if pd.notna(v) else "—"
            )

        n_rows = len(display_df)
        table_height = min(700, max(380, n_rows * 38 + 42))
        st.dataframe(
            display_df.sort_values("Departamento"),
            width="stretch",
            hide_index=True,
            height=table_height,
        )


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — HALL OF SHAME
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown(
        "### 🚨 Capital Congelado — Ejecutoras con Baja Ejecución\n"
        "Ejecutoras con PIM superior al umbral seleccionado, ordenadas por avance % (peor primero)."
    )

    # ── Controles
    ctrl1, ctrl2 = st.columns([1, 1])
    with ctrl1:
        min_pim_m = st.slider(
            "PIM mínimo (millones S/)",
            min_value=1, max_value=100, value=10, step=1,
            help="Solo ejecutoras con PIM mayor al valor seleccionado",
        )
        min_pim = min_pim_m * 1_000_000

    with ctrl2:
        # Cargar una vez sin filtro para obtener lista de deptos
        df_hall_all = _get_hall(min_pim=10_000_000, departamento=None)
        if df_hall_all is not None:
            deptos = sorted(df_hall_all["DEPARTAMENTO_EJECUTORA_NOMBRE"].dropna().unique().tolist())
        else:
            deptos = []
        depto_sel = st.selectbox(
            "Filtrar por departamento",
            options=["Todos"] + deptos,
        )

    df_hall = _get_hall(min_pim=min_pim, departamento=depto_sel if depto_sel != "Todos" else None)

    if df_hall is None:
        _aviso_pipeline()
    elif df_hall.empty:
        st.info("No hay ejecutoras que cumplan los criterios seleccionados.")
    else:
        st.caption(f"Mostrando {len(df_hall):,} ejecutoras · PIM mínimo: S/ {min_pim:,.0f}")

        # ── Gráfico top-15 por saldo no devengado
        top15 = df_hall.head(15).copy()
        top15_sorted = top15.sort_values("saldo_no_devengado", ascending=True)

        fig_shame = go.Figure(go.Bar(
            x=top15_sorted["saldo_no_devengado"] / 1_000_000,
            y=top15_sorted["EJECUTORA_NOMBRE"].str[:45],
            orientation="h",
            marker_color="#dc2626",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Saldo no devengado: S/ %{x:,.1f}M<br>"
                "<extra></extra>"
            ),
        ))
        fig_shame.update_layout(
            title="Top 15 Ejecutoras — Mayor Capital Paralizado (Saldo No Devengado)",
            xaxis_title="Saldo No Devengado (millones S/)",
            yaxis_title="",
            height=520,
            margin=dict(t=50, b=30, l=20, r=20),
            plot_bgcolor="white",
        )
        st.plotly_chart(fig_shame, width="stretch")

        st.markdown("---")
        st.markdown("#### Tabla completa de ejecutoras")

        display_hall = df_hall.copy()
        rename_map = {
            "EJECUTORA_NOMBRE": "Ejecutora",
            "PLIEGO_NOMBRE": "Pliego",
            "DEPARTAMENTO_EJECUTORA_NOMBRE": "Departamento",
            "NIVEL_GOBIERNO_NOMBRE": "Nivel Gobierno",
            "MONTO_PIM": "PIM (S/)",
            "DEVENGADO_PERIODO": "Devengado (S/)",
            "avance_pct": "Avance %",
            "saldo_no_devengado": "Saldo No Devengado (S/)",
        }
        display_hall = display_hall.rename(columns={k: v for k, v in rename_map.items() if k in display_hall.columns})

        for col in ["PIM (S/)", "Devengado (S/)", "Saldo No Devengado (S/)"]:
            if col in display_hall.columns:
                display_hall[col] = display_hall[col].apply(
                    lambda v: f"S/ {v:,.0f}" if pd.notna(v) else "—"
                )
        if "Avance %" in display_hall.columns:
            display_hall["Avance %"] = display_hall["Avance %"].apply(
                lambda v: f"{v:.1f}%" if pd.notna(v) else "—"
            )

        st.dataframe(display_hall, width="stretch", hide_index=True, height=460)


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — REPORTE QA
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown("### 🔍 Reporte de Calidad de Datos (QA)")

    # Panel de metadatos del periodo actual
    kpis_qa = _get_resumen()
    if kpis_qa:
        with st.container():
            st.markdown("#### Metadatos del conjunto de datos actual")
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Periodo", kpis_qa.get("periodo", "—"))
            with mc2:
                st.metric("Ejecutoras procesadas", f"{kpis_qa.get('num_ejecutoras', 0):,}")
            with mc3:
                st.metric("Departamentos", kpis_qa.get("num_departamentos", "—"))
        st.markdown("---")

    # Intentar cargar reporte markdown
    qa_path = _ROOT / "data" / "processed" / "qa_report.md"
    if qa_path.exists():
        st.markdown(qa_path.read_text(encoding="utf-8"))
    else:
        st.info(
            "📄 **Reporte QA pendiente de generación.**\n\n"
            "El evaluador automático generará este reporte en el siguiente paso del pipeline. "
            "Cuando esté disponible, aparecerá aquí."
        )
        st.markdown("---")
        st.markdown("#### Verificaciones pendientes")
        checks = [
            ("Consistencia PIM vs Devengado", "Verifica que devengado ≤ PIM en todas las ejecutoras."),
            ("Valores nulos en columnas clave", "Detecta ejecutoras con MONTO_PIM nulo o cero."),
            ("Duplicados de ejecutora", "Asegura que cada ejecutora aparece una sola vez por periodo."),
            ("Rango de avance %", "Detecta valores fuera del rango esperado [0%, 100%]."),
            ("Completitud territorial", "Verifica que los 25 departamentos estén representados."),
        ]
        for nombre, desc in checks:
            with st.expander(f"⏳ {nombre}"):
                st.write(desc)
