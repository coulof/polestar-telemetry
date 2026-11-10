"""Shared look for the dashboard: palette, Plotly styling and CSS."""
import streamlit as st

# Polestar orange: RAL 2000 (#ED760E) on light surfaces, brand #FF7500 on dark.
LIGHT = {"series": "#ED760E", "good": "#1baf7a", "ink": "#0b0b0b", "muted": "#52514e", "grid": "rgba(82,81,78,0.15)", "low": "#e34948", "high": "#eda100"}
DARK = {"series": "#FF7500", "good": "#199e70", "ink": "#f0efec", "muted": "#c3c2b7", "grid": "rgba(195,194,183,0.15)", "low": "#e66767", "high": "#c98500"}

FONT = "Inter, Helvetica Neue, Helvetica, Arial, sans-serif"  # Unica77 is Helvetica-derived

CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1200px; }
h1 { font-weight: 650; letter-spacing: -0.01em; }
[data-testid="stMetricLabel"] { opacity: .7; }
[data-testid="stMetricValue"] { font-weight: 600; }
.pt-big { font-size: 2.1rem; font-weight: 650; line-height: 1.1; }
.pt-sub { opacity: .7; font-size: .9rem; }
/* keep nested metric columns two-per-row on phones instead of one tall stack */
@media (max-width: 640px) {
  [data-testid="stColumn"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
    min-width: calc(50% - 1rem) !important; flex: 1 1 calc(50% - 1rem) !important;
  }
}
</style>
"""


def is_dark():
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def palette():
    return DARK if is_dark() else LIGHT


def style(fig, height=300, title=None, **layout):
    """Apply the shared chart look: transparent, thin grid, ink-coloured text, no legend."""
    p = palette()
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=p["ink"]), x=0) if title else None,
        height=height, margin=dict(l=0, r=0, t=40 if title else 10, b=0), showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=p["muted"]), hoverlabel=dict(font_size=13), **layout,
    )
    fig.update_xaxes(showgrid=False, linecolor=p["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=p["grid"], zeroline=False)
    return fig
