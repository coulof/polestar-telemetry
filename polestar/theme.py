"""Shared look for the dashboard: palette, Plotly styling and CSS."""
import html

import streamlit as st

# Polestar orange: RAL 2000 (#ED760E) on light surfaces, brand #FF7500 on dark.
LIGHT = {"series": "#ED760E", "good": "#1baf7a", "ink": "#0b0b0b", "muted": "#53565A", "grid": "rgba(83,86,90,0.15)", "low": "#e34948", "high": "#eda100",
         "baseline": "#75787B", "faint": "#C8C9C7"}   # brand Grey 9 / Grey 3: comparison and background series
DARK = {"series": "#FF7500", "good": "#199e70", "ink": "#f0efec", "muted": "#C8C9C7", "grid": "rgba(200,201,199,0.15)", "low": "#e66767", "high": "#c98500",
        "baseline": "#B1B3B3", "faint": "#53565A"}   # brand Grey 5 / Grey 11

FONT = "Inter, Helvetica Neue, Helvetica, Arial, sans-serif"  # Unica77 is Helvetica-derived

CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1200px; }
h1, h2, h3 { letter-spacing: -0.02em; }   /* tight tracking, as in the brand typography sheet */
h1 { font-weight: 650; }
[data-testid="stMetricLabel"] { opacity: .7; }
[data-testid="stMetricValue"] { font-weight: 600; }
.pt-big { font-size: 2.1rem; font-weight: 650; line-height: 1.1; letter-spacing: -0.02em; }
.pt-sub { opacity: .7; font-size: .9rem; }
/* status chip pinned to the top-right corner of the hero card */
.st-key-hero { position: relative; }
/* no chart toolbar (download / full screen) in the hero card: it collided with the status chip on phones */
.st-key-hero [data-testid="stElementToolbar"] { display: none; }
.st-key-hero [data-testid="stElementContainer"]:has(.pt-chip) { position: absolute; top: .8rem; right: 1rem; width: auto; z-index: 2; }
.pt-chip { display: inline-flex; align-items: center; gap: .45rem; padding: .2rem .75rem; border-radius: 999px; font-size: .85rem; font-weight: 600; }
.pt-dot { width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }
.pt-chip.on { animation: pt-pulse 2.6s ease-in-out infinite; }
@keyframes pt-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
@media (prefers-reduced-motion: reduce) { .pt-chip.on { animation: none; } }
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


def chip(text, on, color="green"):
    """HTML for the status chip: tinted and gently pulsing while `on` (green for charging, gold for
    driving), grey otherwise."""
    if on and color == "gold":      # Swedish gold #F6BE00 (secondary brand colour)
        bg, fg = ("rgba(246,190,0,.20)", "#F6BE00") if is_dark() else ("rgba(246,190,0,.24)", "#7A5C00")
    elif on:
        bg, fg = ("rgba(25,158,112,.22)", "#6fdcb2") if is_dark() else ("rgba(27,175,122,.15)", "#0b6b4a")
    else:
        bg, fg = ("rgba(200,201,199,.15)", "#C8C9C7") if is_dark() else ("rgba(83,86,90,.10)", "#53565A")
    return (f'<span class="pt-chip{" on" if on else ""}" style="background:{bg};color:{fg}">'
            f'<span class="pt-dot"></span>{html.escape(str(text))}</span>')


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
